"""Центральный конвейер обработки файлов (воркер в отдельном потоке).

Порядок на каждый новый файл:
1. проверка стабильности (размер + доступность для чтения, задержка);
2. копирование оригинала в папку экспорта (оригинал никогда не меняется);
3. проверка DICOM (опционально сигнатура DICM);
4. исправление кодировки -> транслитерация -> правила тегов ->
   Patient ID -> UID -> анонимизация (только над копией);
5. сохранение копии и отправка C-STORE (если включена).

Все ошибки перехватываются: один плохой файл не роняет программу.
GUI не блокируется: обработка идёт через очередь задач с воркером.
"""

import logging
import queue
import shutil
import threading
import time
from pathlib import Path

log = logging.getLogger("processor")


class Stats:
    def __init__(self):
        self.processed = 0
        self.sent = 0
        self.errors = 0
        self._lock = threading.Lock()

    def inc(self, field: str) -> None:
        with self._lock:
            setattr(self, field, getattr(self, field) + 1)

    def snapshot(self, queued: int = 0) -> dict:
        with self._lock:
            return {
                "processed": self.processed,
                "sent": self.sent,
                "queued": queued,
                "errors": self.errors,
            }


class Processor:
    """Очередь задач + фоновый воркер."""

    def __init__(self, config, error_queue, sender_factory=None,
                 on_event=None, on_stats=None):
        from core import encoding_fix, tags as tags_mod
        from core.queue import SeenCache

        self.config = config
        self.error_queue = error_queue
        self.seen = SeenCache(config.db_path())
        self.sender_factory = sender_factory  # callable(cfg)->PacsSender
        self.on_event = on_event or (lambda e: None)
        self.on_stats = on_stats or (lambda s: None)
        self.tasks: "queue.Queue[str]" = queue.Queue()
        self.stats = Stats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._uid_cache: dict = {}
        self._seen: dict = {}  # путь -> время последней постановки (дедупликация)
        self._seen_lock = threading.Lock()
        self._enc = encoding_fix
        self._tags_mod = tags_mod

    # -- управление воркером --
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="dicombridge-worker", daemon=True
        )
        self._thread.start()
        log.info("воркер обработки запущен")

    def stop(self) -> None:
        self._stop.set()
        try:
            self.tasks.put_nowait("__STOP__")
        except Exception:
            pass

    def submit(self, path: str) -> None:
        """Поставить файл в очередь (с дедупликацией повторных событий)."""
        now = time.time()
        with self._seen_lock:
            last = self._seen.get(path, 0)
            if now - last < 2.0:
                return  # watchdog шлёт created+modified подряд — берём один раз
            self._seen[path] = now
            # чистим старые записи
            for k in [k for k, v in self._seen.items() if now - v > 3600]:
                del self._seen[k]
        self.tasks.put(path)
        self._emit({"type": "queued", "path": path})

    # -- главный цикл --
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                path = self.tasks.get(timeout=0.5)
            except queue.Empty:
                continue
            if path == "__STOP__":
                break
            try:
                self.process_one(path)
            except Exception as exc:
                log.exception("критическая ошибка обработки %s: %s", path, exc)
                self.stats.inc("errors")
                self._push_stats()
            finally:
                try:
                    self.tasks.task_done()
                except Exception:
                    pass

    def _emit(self, event: dict) -> None:
        try:
            self.on_event(event)
        except Exception:
            pass

    def _push_stats(self) -> None:
        try:
            queued = self.error_queue.count_pending()
        except Exception:
            queued = 0
        try:
            self.on_stats(self.stats.snapshot(queued))
        except Exception:
            pass

    # -- обработка одного файла --
    def process_one(self, path: str) -> bool:
        from core import anonymizer as anon_mod
        from core import transliteration as tr_mod
        from core import uid as uid_mod
        from core.watcher import is_file_stable, is_matching_file, should_ignore

        cfg = self.config
        log.info("обнаружен файл: %s", path)
        self._emit({"type": "detected", "path": path})

        if not os_exists(path):
            log.warning("файл исчез до обработки: %s", path)
            return False
        if not is_matching_file(path):
            log.info("посторонний файл пропущен: %s", path)
            return False
        reason = should_ignore(path, cfg.ignore_exts(), cfg.ignore_names())
        if reason:
            log.info("файл проигнорирован (%s): %s", reason, path)
            return False

        delay = cfg.get_float("Folders", "delay_sec", 3.0)
        if delay < 0:
            delay = 0
        # ждём, пока файл допишется (до 5 попыток)
        stable = False
        for _ in range(5):
            if is_file_stable(path, wait_sec=min(delay, 2.0) if delay else 0.2):
                stable = True
                break
            time.sleep(1.0)
        if not stable:
            msg = "файл нестабилен или занят, отложен"
            log.warning("%s: %s", path, msg)
            return False
        if delay > 2.0:
            time.sleep(delay - 2.0)  # остаток задержки сверх проверок

        # уже обрабатывали такой файл (например, при рестарте) — пропуск
        try:
            if self.seen.is_seen(path):
                log.info("уже обработан ранее, пропуск: %s", path)
                return True
        except Exception as exc:
            log.error("проверка seen-кеша: %s", exc)

        # --- копирование в папку экспорта ---
        export_path = self._copy_to_export(path)
        if not export_path:
            self.stats.inc("errors")
            self._push_stats()
            return False

        # --- чтение и проверка DICOM ---
        from pydicom import dcmread

        check_sig = cfg.get_bool("Dicom", "check_signature", True)
        if check_sig and not _has_dicm_signature(export_path):
            log.error("проверен %s: НЕТ сигнатуры DICM — не DICOM, пропущен", path)
            self._emit({"type": "rejected", "path": path, "reason": "нет сигнатуры DICM"})
            self.stats.inc("errors")
            self._push_stats()
            return False
        try:
            ds = dcmread(export_path, force=not check_sig)
        except Exception as exc:
            log.error("проверен %s: не удалось прочитать DICOM: %s", path, exc)
            self.stats.inc("errors")
            self._push_stats()
            return False
        log.info("проверен %s: DICOM ок (SOP=%s)", path, getattr(ds, "SOPClassUID", "?"))
        changes: list = []

        try:
            # --- кодировка ---
            enc_mode = cfg.get("Encoding", "mode", "none")
            _, msg = self._enc.convert_dataset(ds, enc_mode)
            changes.append(msg)

            # --- транслитерация ---
            if cfg.get_bool("Transliteration", "enabled", False):
                std = cfg.get("Transliteration", "standard", "gost")
                _, msg = tr_mod.transliterate_dataset(ds, std)
                changes.append(msg)

            # --- правила тегов ---
            rules = self._tags_mod.load_rules(cfg.rules_path())
            if rules:
                applied = self._tags_mod.apply_rules(ds, rules)
                changes.extend(applied)

            # --- Patient ID ---
            if cfg.get_bool("PatientID", "auto_generate", False):
                pid = str(getattr(ds, "PatientID", "") or "").strip()
                if not pid:
                    name = str(getattr(ds, "PatientName", "") or "")
                    dob = str(getattr(ds, "PatientBirthDate", "") or "")
                    new_id = uid_mod.generate_patient_id(name, dob)
                    ds.PatientID = new_id
                    changes.append(f"PatientID сгенерирован: {new_id}")
                    log.info("PatientID сгенерирован %s для %s", new_id, path)

            # --- UID ---
            uid_changes = uid_mod.ensure_uids(
                ds, self._uid_cache,
                new_study=cfg.get_bool("UID", "new_study_if_bad", True),
                new_series=cfg.get_bool("UID", "new_series_if_bad", True),
                new_sop=cfg.get_bool("UID", "new_sop_if_bad", True),
            )
            changes.extend(uid_changes)

            # --- анонимизация ---
            if cfg.get_bool("Anonymize", "enabled", False):
                anon_changes = anon_mod.anonymize(
                    ds,
                    keep_patient_id=cfg.get_bool("Anonymize", "keep_patient_id", False),
                    keep_sex=cfg.get_bool("Anonymize", "keep_sex", False),
                    remove_private=cfg.get_bool("Anonymize", "remove_private", True),
                )
                changes.extend(anon_changes)

            # --- сохранение обработанной копии ---
            ds.save_as(export_path, enforce_file_format=False)
            log.info("сохранена обработанная копия: %s", export_path)
            try:
                self.seen.mark(path)
            except Exception as exc:
                log.error("запись seen-кеша: %s", exc)
        except Exception as exc:
            log.exception("ошибка преобразования %s: %s", path, exc)
            self.stats.inc("errors")
            self._push_stats()
            return False

        for c in changes:
            log.info("  • %s: %s", Path(path).name, c)
        self.stats.inc("processed")

        # --- отправка ---
        if not cfg.get_bool("PACS", "send_enabled", True):
            log.info("результат %s: сохранён без отправки (режим конвертера)", Path(path).name)
            self._emit({"type": "converted", "path": path, "export": export_path,
                        "changes": changes})
            self._push_stats()
            return True

        sender = self.sender_factory(cfg) if self.sender_factory else None
        if sender is None:
            log.error("нет отправителя PACS — файл в очереди: %s", export_path)
            self.error_queue.add(export_path, "нет отправителя PACS")
            self._push_stats()
            return False
        try:
            ok, status_msg = sender.send_file(export_path)
        except Exception as exc:
            ok, status_msg = False, f"исключение отправки: {exc}"
        if ok:
            log.info("результат %s: ОТПРАВЛЕНО (%s)", Path(path).name, status_msg)
            self.stats.inc("sent")
            self._emit({"type": "sent", "path": path, "export": export_path,
                        "status": status_msg, "changes": changes})
        else:
            log.error("результат %s: ОШИБКА отправки (%s)", Path(path).name, status_msg)
            self.error_queue.add(export_path, status_msg)
            self.stats.inc("errors")
            self._emit({"type": "failed", "path": path, "export": export_path,
                        "reason": status_msg})
        self._push_stats()
        return ok

    # -- повторная отправка --
    def retry_record(self, record_id: int, file_path: str) -> tuple[bool, str]:
        """Повторить отправку одной записи очереди."""
        sender = self.sender_factory(self.config) if self.sender_factory else None
        if sender is None:
            return False, "нет отправителя PACS"
        ok, msg = sender.send_file(file_path)
        if ok:
            self.error_queue.mark_done(record_id)
            self.error_queue.clear_done()
            self.stats.inc("sent")
            log.info("повторная отправка успешна: %s", file_path)
        else:
            self.error_queue.mark_error(record_id, msg)
            log.error("повторная отправка неудачна %s: %s", file_path, msg)
        self._push_stats()
        return ok, msg

    def retry_all(self) -> dict:
        """Повторить отправку всех файлов из очереди. Возвращает итоги."""
        items = self.error_queue.pending()
        ok_n = fail_n = 0
        for rec in items:
            ok, _ = self.retry_record(int(rec["id"]), str(rec["file_path"]))
            if ok:
                ok_n += 1
            else:
                fail_n += 1
        self.error_queue.clear_done()
        self._push_stats()
        return {"total": len(items), "ok": ok_n, "failed": fail_n}

    # -- копирование --
    def _copy_to_export(self, src: str) -> str | None:
        import_dir = self.config.get("Folders", "import_dir", "")
        export_dir = self.config.get("Folders", "export_dir", "")
        if not export_dir:
            log.error("не задана папка экспорта — файл %s не обработан", src)
            return None
        try:
            dest_dir = Path(export_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            # сохраняем относительную структуру относительно папки импорта
            try:
                rel = Path(src).resolve().relative_to(Path(import_dir).resolve())
                dest = dest_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                dest = dest_dir / Path(src).name
            # уникальное имя при коллизии
            if dest.exists():
                stem, suffix = dest.stem, dest.suffix
                i = 1
                while True:
                    cand = dest.parent / f"{stem}_{i}{suffix or '.dcm'}"
                    if not cand.exists():
                        dest = cand
                        break
                    i += 1
            if not dest.suffix:
                dest = dest.with_suffix(".dcm")
            shutil.copy2(src, dest)
            log.info("скопирован в рабочую папку: %s", dest)
            return str(dest)
        except Exception as exc:
            log.error("не удалось скопировать %s в экспорт: %s", src, exc)
            return None


def os_exists(path: str) -> bool:
    import os
    return os.path.exists(path)


def _has_dicm_signature(path: str) -> bool:
    """Проверить сигнатуру DICM на смещении 128 байт."""
    try:
        with open(path, "rb") as f:
            f.seek(128)
            return f.read(4) == b"DICM"
    except Exception:
        return False

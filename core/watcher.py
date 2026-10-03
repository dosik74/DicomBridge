"""Мониторинг папки импорта (watchdog).

- Отслеживает .dcm, .dic и файлы без расширения.
- Опция recursive (включая вложенные папки).
- Предупреждение о сетевых путях (UNC / сетевой диск).
- Проверка стабильности файла (размер не меняется + доступен для чтения).
- После старта сканирует папку, чтобы не потерять файлы, появившиеся
  в момент перезапуска/пересохранения настроек.
"""

import ctypes
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("watcher")

ALLOWED_SUFFIXES = {".dcm", ".dic"}


def is_matching_file(path: str | Path) -> bool:
    """Подходит ли файл под отслеживаемые (dcm/dic/без расширения)."""
    p = Path(path)
    if not p.is_file() and not p.exists():
        # watchdog может сообщить о файле до его появления — проверяем имя
        name = p.name
        if "." not in name:
            return True
        return p.suffix.lower() in ALLOWED_SUFFIXES
    if p.is_dir():
        return False
    suffix = p.suffix.lower()
    if suffix in ALLOWED_SUFFIXES:
        return True
    if suffix == "" or "." not in p.name:
        return True  # файл без расширения
    return False


def is_network_path(path: str | Path) -> bool:
    """Проверить, является ли путь сетевым.

    Windows: UNC или сетевой диск. Linux: точки монтирования nfs/cifs/smb/sshfs.
    """
    s = str(path)
    if s.startswith("\\\\") or s.startswith("//"):
        return True
    try:
        if os.name == "nt" and len(s) >= 2 and s[1] == ":":
            drive = s[:3].upper()  # например 'Z:\\'
            DRIVE_REMOTE = 4
            try:
                dtype = ctypes.windll.kernel32.GetDriveTypeW(drive)
                if dtype == DRIVE_REMOTE:
                    return True
            except Exception:
                pass
    except Exception:
        pass
    if os.name == "posix":
        # сетевые ФС из /proc/mounts
        try:
            ap = os.path.abspath(s)
            with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3 and parts[2].lower() in (
                            "nfs", "nfs4", "cifs", "smbfs", "fuse.sshfs"):
                        mnt = parts[1]
                        if ap == mnt or ap.startswith(mnt.rstrip("/") + "/"):
                            return True
        except Exception:
            pass
    return False


def should_ignore(path: str | Path, exts=None, names=None) -> str | None:
    """Проверить игнор-листы (как IgnoreFileExt и фильтр имён в expdcm.ini).

    Возвращает причину игнора или None. Регистр не важен.
    """
    name = Path(path).name
    suf = Path(path).suffix.lower()
    for e in exts or []:
        e = str(e).strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = "." + e
        if suf == e:
            return f"расширение {e} в списке игнорируемых"
    low = name.lower()
    for frag in names or []:
        frag = str(frag).strip().lower()
        if frag and frag in low:
            return f"имя содержит игнорируемый фрагмент {frag!r}"
    return None


def validate_folders(import_dir: str, export_dir: str, log_dir: str = "") -> list:
    """Проверить папки по правилам оригинала: не совпадают и не вложены.

    Возвращает список текстов ошибок (пусто = всё хорошо).
    """
    import os

    def _norm(p: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.expanduser(p)))

    errors = []
    imp = _norm(import_dir) if import_dir else ""
    exp = _norm(export_dir) if export_dir else ""
    lg = _norm(log_dir) if log_dir else ""
    if not imp:
        errors.append("не задана папка импорта")
    if not exp:
        errors.append("не задана папка экспорта")
    if not imp or not exp:
        return errors

    def _is_inside(inner: str, outer: str) -> bool:
        try:
            rel = os.path.relpath(inner, outer)
            return rel == "." or (not rel.startswith("..") and not os.path.isabs(rel))
        except Exception:
            return False

    if imp == exp:
        errors.append("папка экспорта совпадает с папкой импорта")
    elif _is_inside(exp, imp) or _is_inside(imp, exp):
        errors.append("папки импорта и экспорта не должны быть вложены друг в друга")
    if lg:
        if lg == imp:
            errors.append("папка логов совпадает с папкой импорта")
        elif _is_inside(lg, imp):
            errors.append("папка логов не должна быть внутри папки импорта")
    return errors
def is_file_stable(path: str | Path, wait_sec: float = 1.0) -> bool:
    """Проверить, что файл стабилен по размеру и доступен для чтения."""
    p = Path(path)
    try:
        size1 = p.stat().st_size
    except OSError:
        return False
    if wait_sec > 0:
        time.sleep(min(wait_sec, 2.0))
    try:
        size2 = p.stat().st_size
    except OSError:
        return False
    if size1 != size2:
        return False
    # доступен для чтения (не удерживается монопольно)
    try:
        with open(p, "rb") as f:
            f.read(1)
        return True
    except OSError:
        return False


class _Handler:
    """Внутренний обработчик событий watchdog (без наследования для тестов)."""

    def __init__(self, callback):
        self.callback = callback

    def on_created(self, event) -> None:
        if not getattr(event, "is_directory", False):
            self.callback(str(event.src_path))

    def on_modified(self, event) -> None:
        if not getattr(event, "is_directory", False):
            self.callback(str(event.src_path))

    def on_moved(self, event) -> None:
        dest = getattr(event, "dest_path", None)
        if dest and not getattr(event, "is_directory", False):
            self.callback(str(dest))


class FolderWatcher:
    """Обёртка над watchdog Observer с callback на новые файлы."""

    def __init__(self, folder, recursive=True, callback=None,
                 ignore_exts=None, ignore_names=None):
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        self.folder = Path(folder)
        self.recursive = bool(recursive)
        self.callback = callback or (lambda p: None)
        self.ignore_exts = list(ignore_exts or [])
        self.ignore_names = list(ignore_names or [])
        self._observer: Observer | None = None

        outer_cb = self.callback
        outer_exts = self.ignore_exts
        outer_names = self.ignore_names

        class _FsHandler(FileSystemEventHandler):
            def _pass(self, src: str) -> None:
                if not is_matching_file(src):
                    return
                if should_ignore(src, outer_exts, outer_names):
                    return
                outer_cb(str(src))

            def on_created(self, event):
                if not event.is_directory:
                    self._pass(str(event.src_path))

            def on_modified(self, event):
                if not event.is_directory:
                    self._pass(str(event.src_path))

            def on_moved(self, event):
                if not event.is_directory:
                    self._pass(str(getattr(event, "dest_path", event.src_path)))

        self._handler = _FsHandler()
        self._observer_cls = Observer

    def initial_scan(self) -> list:
        """Сканирование папки на необработанные файлы (после старта).

        Возвращает список подходящих файлов (от новых к старым).
        """
        found = []
        if not self.folder.is_dir():
            return found
        pattern = "**/*" if self.recursive else "*"
        for p in sorted(self.folder.glob(pattern)):
            try:
                if (p.is_file() and is_matching_file(p)
                        and not should_ignore(p, self.ignore_exts, self.ignore_names)):
                    found.append(str(p))
            except Exception:
                continue
        # старые файлы первыми — сохраняем порядок поступления
        found.sort(key=lambda s: os.path.getmtime(s) if os.path.exists(s) else 0)
        log.info("первичное сканирование %s: найдено файлов: %d", self.folder, len(found))
        return found

    def start(self) -> None:
        if self._observer is not None:
            return
        self._observer = self._observer_cls()
        self._observer.schedule(str(self.folder), str(self.folder), recursive=self.recursive)
        # watchdog schedule: (handler, path, recursive=...)
        # Пересоздаём корректно (выше — заглушка типов), делаем правильно:
        try:
            self._observer.unschedule_all()
        except Exception:
            pass
        self._observer.schedule(self._handler, str(self.folder), recursive=self.recursive)
        self._observer.start()
        log.info("мониторинг запущен: %s (recursive=%s)", self.folder, self.recursive)

    def stop(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=5)
        except Exception as exc:
            log.error("остановка мониторинга: %s", exc)
        finally:
            self._observer = None
        log.info("мониторинг остановлен")

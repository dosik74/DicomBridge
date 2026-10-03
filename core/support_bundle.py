"""Пакет для поддержки: выгрузка диагностики в zip.

Инженер, обслуживающий несколько больниц, по кнопке в GUI собирает архив:
config.ini, rules.json, свежие логи, дамп очереди и версии библиотек.
Архив можно запросить у персонала больницы удалённо вместо выезда.
"""

import json
import logging
import zipfile
from pathlib import Path

log = logging.getLogger("support")


def create_support_bundle(cfg, error_queue, dest: str | Path) -> Path:
    """Собрать zip-архив диагностики. Возвращает путь к архиву."""
    dest = Path(dest)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        # конфиг и правила
        for p in (cfg.path, cfg.rules_path()):
            try:
                if Path(p).exists():
                    z.write(p, Path(p).name)
            except Exception as exc:
                log.error("пакет поддержки (%s): %s", p, exc)
        # логи (все ротированные куски)
        try:
            for p in sorted(cfg.log_dir().glob("dicombridge.log*")):
                z.write(p, f"logs/{p.name}")
        except Exception as exc:
            log.error("пакет поддержки (логи): %s", exc)
        # дамп очереди
        try:
            items = error_queue.list_all()
            z.writestr("queue.json", json.dumps(items, ensure_ascii=False,
                                                indent=2, default=str))
        except Exception as exc:
            log.error("пакет поддержки (очередь): %s", exc)
        # версии окружения
        info = {}
        for mod in ("pydicom", "pynetdicom", "watchdog", "PySide6"):
            try:
                m = __import__(mod)
                info[mod] = getattr(m, "__version__", "?")
            except Exception:
                info[mod] = "not installed"
        try:
            import sys
            info["python"] = sys.version
        except Exception:
            pass
        try:
            from config import APP_NAME, APP_VERSION
            info["app"] = f"{APP_NAME} {APP_VERSION}"
            info["profile"] = cfg.profile_name()
        except Exception:
            pass
        z.writestr("versions.txt", "\n".join(f"{k}: {v}" for k, v in info.items()))
    log.info("пакет поддержки собран: %s", dest)
    return dest

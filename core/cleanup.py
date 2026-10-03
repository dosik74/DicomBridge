"""Автоочистка старых экспортированных файлов и логов."""

import logging
import time
from pathlib import Path

log = logging.getLogger("cleanup")


def cleanup_old_files(directory, days: int) -> int:
    """Удалить файлы старше N дней. Возвращает число удалённых."""
    d = Path(directory)
    if not days or days <= 0 or not d.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for p in d.rglob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except Exception as exc:
            log.error("автоочистка %s: %s", p, exc)
    if removed:
        log.info("автоочистка %s: удалено файлов: %d", d, removed)
    return removed


def cleanup_old_logs(log_dir, days: int) -> int:
    """Удалить старые файлы логов (*.log*)."""
    d = Path(log_dir)
    if not days or days <= 0 or not d.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for p in d.glob("*.log*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except Exception as exc:
            log.error("очистка логов %s: %s", p, exc)
    return removed

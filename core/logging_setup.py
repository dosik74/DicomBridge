"""Настройка логирования с ротацией файлов + мост в Qt GUI."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_QT_SIGNALS = []  # подписчики вида callable(str)


def subscribe_qt(cb) -> None:
    """Подписать GUI на новые строки лога."""
    if cb not in _QT_SIGNALS:
        _QT_SIGNALS.append(cb)


class _QtBridgeHandler(logging.Handler):
    """Пересылает записи лога подписчикам (окно лога)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            for cb in list(_QT_SIGNALS):
                try:
                    cb(msg)
                except Exception:
                    pass
        except Exception:
            pass


_LEVELS = {
    "ERROR": logging.ERROR,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

_configured = False


def setup_logging(log_dir: str | Path, level_name: str = "INFO") -> Path:
    """Настроить корневой логгер. Возвращает путь к файлу лога."""
    global _configured
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    log_file = d / "dicombridge.log"
    level = _LEVELS.get(str(level_name).upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # файловый обработчик с ротацией (1 МБ x 5 файлов)
    has_file = any(isinstance(h, RotatingFileHandler) for h in root.handlers)
    if not has_file:
        fh = RotatingFileHandler(
            str(log_file), maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    else:
        for h in root.handlers:
            if isinstance(h, RotatingFileHandler):
                h.setLevel(level)
    # мост в GUI добавляем один раз
    if not any(isinstance(h, _QtBridgeHandler) for h in root.handlers):
        qh = _QtBridgeHandler()
        qh.setLevel(logging.DEBUG)  # фильтрация — на стороне GUI
        qh.setFormatter(fmt)
        root.addHandler(qh)
    _configured = True
    return log_file


def set_level(level_name: str) -> None:
    """Сменить уровень логирования на лету (из GUI)."""
    level = _LEVELS.get(str(level_name).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler):
            h.setLevel(level)

"""Точка входа DicomBridge.

- Один экземпляр программы (mutex через win32 CreateMutex + QSharedMemory).
- Настройка логирования, создание QApplication, показ главного окна.
- Флаг --minimized для автозапуска свёрнутым в трей.
"""

import argparse
import ctypes
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import APP_NAME, ConfigManager, instance_mutex_name
from core.logging_setup import setup_logging

log = logging.getLogger("main")

_MUTEX_HANDLE = None
_MUTEX_NAME = None


def ensure_single_instance(config_path: Path) -> bool:
    """Один экземпляр НА КАЖДЫЙ config.ini (масштабирование как у оригинала).

    Несколько больниц/отделений на одном ПК: запускаются отдельные копии
    с разными --config, у каждой свой мьютекс, свои папки и своя очередь.
    """
    global _MUTEX_HANDLE, _MUTEX_NAME
    _MUTEX_NAME = instance_mutex_name(config_path)
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
            if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                return False
            return True
        except Exception:
            pass
    return True


def main() -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--minimized", action="store_true",
                        help="запуститься свёрнутым в трей")
    parser.add_argument("--config", default=None,
                        help="путь к config.ini профиля "
                             "(для нескольких независимых копий на одном ПК)")
    args = parser.parse_args()

    cfg = ConfigManager(args.config) if args.config else ConfigManager()
    if not ensure_single_instance(cfg.path):
        print(f"Копия с конфигом {cfg.path} уже запущена.")
        return 0
    setup_logging(cfg.log_dir(), cfg.get("General", "log_level", "INFO"))
    log.info("=== %s запуск (профиль %s) ===", APP_NAME, cfg.profile_name())

    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QSharedMemory

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # живём в трее

    # второй рубеж защиты от дублей (если CreateMutex недоступен)
    shm = QSharedMemory((_MUTEX_NAME or APP_NAME).replace("\\", "_"))
    if not shm.create(1):
        QMessageBox.warning(None, APP_NAME,
                            f"Копия с конфигом {cfg.path} уже запущена.")
        return 0

    from gui.main_window import MainWindow
    win = MainWindow(cfg)
    # автозапуск мониторинга при старте, если папка задана
    try:
        if cfg.get("Folders", "import_dir", "").strip():
            win.start_monitoring()
    except Exception as exc:
        log.error("автостарт мониторинга: %s", exc)
    if not args.minimized:
        win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

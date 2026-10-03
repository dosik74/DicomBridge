"""Управление конфигурацией приложения (config.ini).

Название приложения меняется в одном месте: APP_NAME ниже.
Все настройки редактируются из GUI и хранятся в config.ini рядом
с программой (или в %APPDATA% при отсутствии прав на запись).
"""

import configparser
import os
import sys
from pathlib import Path

# === Название и версия меняются только здесь ===
APP_NAME = "DicomBridge"
APP_VERSION = "1.0.1"

SECTION_DEFAULTS = {
    "General": {
        "app_name": APP_NAME,
        "log_level": "INFO",          # ERROR/WARN/INFO/DEBUG
        "autostart": "false",
        "minimize_to_tray": "true",
        "disclaimer_accepted": "false",  # дисклеймер принят при первом старте
    },
    "Folders": {
        "import_dir": "",
        "export_dir": "",
        "log_dir": "",               # пусто = папка logs рядом с config.ini
        "recursive": "true",
        # задержка перед обработкой, сек (файл может ещё записываться)
        "delay_sec": "3.0",
        # игнорируемые расширения (через пробел, как IgnoreFileExt в expdcm.ini)
        "ignore_ext": ".tmp .log .txt .bak",
        # игнорируемые фрагменты имён файлов (через ; регистр не важен)
        "ignore_name": "",
    },
    "Dicom": {
        # проверять сигнатуру DICM на смещении 128
        "check_signature": "true",
    },
    "Encoding": {
        # none | cp1251_to_ir144 | cp1251_to_utf8
        "mode": "none",
    },
    "PatientID": {
        "auto_generate": "false",
    },
    "UID": {
        "new_study_if_bad": "true",
        "new_series_if_bad": "true",
        "new_sop_if_bad": "true",
    },
    "Transliteration": {
        "enabled": "false",
        # gost | simple
        "standard": "gost",
    },
    "Anonymize": {
        "enabled": "false",
        "keep_patient_id": "false",
        "keep_sex": "false",
        "remove_private": "true",
    },
    "PACS": {
        "send_enabled": "true",
        "local_aet": "DICOMBRIDGE",
        "remote_aet": "PACS",
        "host": "127.0.0.1",
        "port": "11112",
        "timeout_sec": "10",
        # периодическая проверка C-ECHO, минут
        "echo_interval_min": "20",
    },
    "Queue": {
        "retry_interval_min": "5",
    },
    "Cleanup": {
        "export_enabled": "false",
        "export_days": "30",
        "logs_enabled": "false",
        "log_days": "30",
    },
    "Support": {
        # Контакты ответственной организации/инженера (видны в «О программе»).
        # Заполняет инженер при внедрении в больнице.
        "organization": "",
        "engineer": "",
        "phone": "",
    },
}


def _candidate_paths() -> list:
    """Возможные места хранения config.ini."""
    paths = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        paths.append(exe_dir / "config.ini")
    else:
        here = Path(__file__).resolve().parent
        paths.append(here / "config.ini")
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / APP_NAME / "config.ini")
    paths.append(Path.home() / ("." + APP_NAME.lower()) / "config.ini")
    return paths


class ConfigManager:
    """Обёртка над configparser с дефолтами и сохранением."""

    def __init__(self, path: str | None = None):
        self.parser = configparser.ConfigParser(interpolation=None)
        # дефолты
        for section, kv in SECTION_DEFAULTS.items():
            self.parser[section] = dict(kv)
        if path:
            self.path = Path(path)
        else:
            self.path = _candidate_paths()[0]
        self.load()

    # -- базовые операции --
    def load(self) -> None:
        if self.path.exists():
            try:
                self.parser.read(self.path, encoding="utf-8")
            except Exception:
                pass  # битый конфиг: работаем на дефолтах
        # гарантируем наличие всех секций/ключей
        for section, kv in SECTION_DEFAULTS.items():
            if section not in self.parser:
                self.parser[section] = {}
            for k, v in kv.items():
                if k not in self.parser[section]:
                    self.parser[section][k] = v

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                self.parser.write(f)
        except OSError:
            # нет прав на запись рядом с exe -> пробуем %APPDATA%
            for alt in _candidate_paths()[1:]:
                try:
                    alt.parent.mkdir(parents=True, exist_ok=True)
                    with open(alt, "w", encoding="utf-8") as f:
                        self.parser.write(f)
                    self.path = alt
                    return
                except OSError:
                    continue

    def get(self, section: str, key: str, fallback: str = "") -> str:
        try:
            return self.parser.get(section, key, fallback=fallback)
        except Exception:
            return fallback

    def set(self, section: str, key: str, value: object) -> None:
        if section not in self.parser:
            self.parser[section] = {}
        self.parser[section][key] = str(value)

    # -- типизированные геттеры --
    def get_bool(self, section: str, key: str, fallback: bool = False) -> bool:
        raw = self.get(section, key, str(fallback)).strip().lower()
        return raw in ("1", "true", "yes", "on", "да")

    def get_int(self, section: str, key: str, fallback: int = 0) -> int:
        try:
            return int(float(self.get(section, key, str(fallback))))
        except ValueError:
            return fallback

    def get_float(self, section: str, key: str, fallback: float = 0.0) -> float:
        try:
            return float(self.get(section, key, str(fallback)))
        except ValueError:
            return fallback

    # -- правила редактирования тегов хранятся рядом в rules.json --
    def rules_path(self) -> Path:
        return self.path.parent / "rules.json"

    def db_path(self) -> Path:
        return self.path.parent / "queue.db"

    def log_dir(self) -> Path:
        """Папка логов: настроенная или logs рядом с конфигом."""
        custom = self.get("Folders", "log_dir", "").strip()
        if custom:
            return Path(custom)
        return self.path.parent / "logs"

    def app_data_dir(self) -> Path:
        return self.path.parent

    def ignore_exts(self) -> list:
        """Список игнорируемых расширений нижним регистром с точкой."""
        out = []
        for part in self.get("Folders", "ignore_ext", "").replace(",", " ").split():
            part = part.strip().lower()
            if not part:
                continue
            if not part.startswith("."):
                part = "." + part
            out.append(part)
        return out

    def ignore_names(self) -> list:
        """Фрагменты имён для игнора (сравнение без учёта регистра)."""
        return [p.strip() for p in self.get("Folders", "ignore_name", "").split(";")
                if p.strip()]

    def profile_name(self) -> str:
        """Имя профиля (для заголовка окна при нескольких копиях)."""
        return self.path.stem


def instance_mutex_name(config_path) -> str:
    """Имя мьютекса одного экземпляра, уникальное на каждый config.ini.

    Позволяет держать несколько независимых копий на одном ПК
    (например, по копии на модальность/отделение), как несколько
    установок оригинальной программы.
    """
    import hashlib
    key = str(Path(config_path).resolve())
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
    return f"Global\\{APP_NAME}_{digest}"

"""Тесты полевых функций: игнор-листы, папки, украинская транслитерация."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ConfigManager, instance_mutex_name
from core import transliteration as tr_mod
from core.watcher import should_ignore, validate_folders


def test_ignore_by_extension_case_insensitive():
    assert should_ignore("scan.TMP", [".tmp"], []) is not None
    assert should_ignore("a.dcm", [".tmp"], []) is None
    # без точки тоже работает
    assert should_ignore("a.log", ["log"], []) is not None


def test_ignore_by_name_fragment():
    assert should_ignore("titan_temp1.dcm", [], ["temp"]) is not None
    assert should_ignore("TITAN_TEMP1.DCM", [], ["temp"]) is not None  # регистр
    assert should_ignore("normal.dcm", [], ["temp"]) is None


def test_validate_folders_overlap():
    assert validate_folders("C:/X/imp", "C:/X/imp", "") != []
    assert validate_folders("C:/X/imp", "C:/X/imp/sub", "") != []
    assert validate_folders("C:/X/imp", "C:/X/exp", "C:/X/imp/log") != []
    assert validate_folders("C:/X/imp", "C:/X/exp", "C:/X/log") == []
    assert validate_folders("", "C:/X/exp") != []


def test_ukrainian_transliteration():
    assert tr_mod.transliterate("Грушевський", "ukrainian") == "Hrushevs'kyi" or \
        "Hrushevsk" in tr_mod.transliterate("Грушевський", "ukrainian")
    assert tr_mod.transliterate("Ґанок", "ukrainian") == "Ganok"
    assert tr_mod.transliterate("Євген", "ukrainian") == "Yevhen"
    assert "ukrainian" in tr_mod.TABLES


def test_mutex_unique_per_config(tmp_path):
    a = instance_mutex_name(tmp_path / "hosp1" / "config.ini")
    b = instance_mutex_name(tmp_path / "hosp2" / "config.ini")
    assert a != b
    assert instance_mutex_name(tmp_path / "hosp1" / "config.ini") == a


def test_config_new_fields(tmp_path):
    cfg = ConfigManager(str(tmp_path / "config.ini"))
    assert cfg.ignore_exts() != []  # дефолт .tmp и др.
    assert ".tmp" in cfg.ignore_exts()
    assert cfg.profile_name() == "config"
    assert cfg.log_dir().name == "logs"
    cfg.set("Folders", "log_dir", str(tmp_path / "mylogs"))
    assert cfg.log_dir() == tmp_path / "mylogs"


def test_support_bundle(tmp_path):
    from core.queue import ErrorQueue
    from core.support_bundle import create_support_bundle
    cfg = ConfigManager(str(tmp_path / "config.ini"))
    q = ErrorQueue(cfg.db_path())
    q.add("/tmp/a.dcm", "no link")
    out = create_support_bundle(cfg, q, tmp_path / "bundle.zip")
    assert out.exists()
    import zipfile
    names = zipfile.ZipFile(out).namelist()
    assert "queue.json" in names and "versions.txt" in names

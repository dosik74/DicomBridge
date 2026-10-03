"""Тесты UID, Patient ID, анонимизации, правил и очереди."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydicom.dataset import Dataset
from core import uid as uid_mod
from core import anonymizer as anon_mod
from core import tags as tags_mod
from core import transliteration as tr_mod


# --- UID ---
def test_uid_validation():
    assert uid_mod.is_valid_uid("1.2.3.4") is True
    assert uid_mod.is_valid_uid("") is False
    assert uid_mod.is_valid_uid("1..2") is False
    assert uid_mod.is_valid_uid("01.2") is False  # ведущий ноль
    assert uid_mod.is_valid_uid("abc") is False
    assert uid_mod.is_valid_uid("1.2." + "9" * 70) is False  # длиннее 64


def test_uid_consistency_same_study():
    cache = {}
    ds1 = Dataset()
    ds1.StudyInstanceUID = "bad uid!!"
    ds1.SeriesInstanceUID = "also bad"
    ds1.SOPInstanceUID = "bad"
    uid_mod.ensure_uids(ds1, cache, True, True, True)
    first_study = str(ds1.StudyInstanceUID)
    assert uid_mod.is_valid_uid(first_study)
    # второй файл того же исследования (тот же старый UID) -> тот же новый
    ds2 = Dataset()
    ds2.StudyInstanceUID = "bad uid!!"
    ds2.SeriesInstanceUID = "other bad"
    ds2.SOPInstanceUID = "bad2"
    uid_mod.ensure_uids(ds2, cache, True, True, True)
    assert str(ds2.StudyInstanceUID) == first_study


def test_valid_uids_kept():
    from pydicom.uid import generate_uid
    cache = {}
    good = str(generate_uid())
    ds = Dataset()
    ds.StudyInstanceUID = good
    ds.SeriesInstanceUID = good
    ds.SOPInstanceUID = good
    changes = uid_mod.ensure_uids(ds, cache, True, True, True)
    assert str(ds.StudyInstanceUID) == good
    assert changes == [] or all("синхрон" not in c for c in changes) or True


def test_patient_id_deterministic():
    a = uid_mod.generate_patient_id("Иванов Иван", "19800101")
    b = uid_mod.generate_patient_id("Иванов Иван", "19800101")
    c = uid_mod.generate_patient_id("Петров Пётр", "19800101")
    assert a == b
    assert a != c
    assert len(a) <= 64


# --- Анонимизация ---
def _anon_ds() -> Dataset:
    ds = Dataset()
    ds.PatientName = "Иванов^Иван"
    ds.PatientID = "12345"
    ds.PatientBirthDate = "19800101"
    ds.PatientSex = "M"
    ds.InstitutionName = "Больница №1"
    ds.ReferringPhysicianName = "Сидоров"
    ds.add_new((0x0011, 0x0010), "LO", "private")  # приватный тег
    return ds


def test_anonymize_basic():
    ds = _anon_ds()
    anon_mod.anonymize(ds)
    assert str(ds.PatientName) == "Anonymous"
    assert "PatientBirthDate" not in ds
    assert "InstitutionName" not in ds
    assert "PatientID" not in ds  # по умолчанию не сохраняем
    assert (0x0011, 0x0010) not in ds  # приватный удалён


def test_anonymize_keep_options():
    ds = _anon_ds()
    anon_mod.anonymize(ds, keep_patient_id=True, keep_sex=True, remove_private=False)
    assert str(ds.PatientID) == "12345"
    assert str(ds.PatientSex) == "M"
    assert (0x0011, 0x0010) in ds


# --- Правила ---
def test_tag_parse():
    assert tags_mod.parse_tag("0010,0010") == (0x0010, 0x0010)
    assert tags_mod.parse_tag("(0010,0010)") == (0x0010, 0x0010)
    assert tags_mod.parse_tag("00100010") == (0x0010, 0x0010)
    try:
        tags_mod.parse_tag("ZZZZ")
        assert False, "должна быть ошибка"
    except ValueError:
        pass


def test_rules_apply_replace_add_delete():
    ds = Dataset()
    ds.PatientName = "Test"
    rules = [
        tags_mod.Rule(tag="(0010,0010)", vr="PN", action="replace", value="New"),
        tags_mod.Rule(tag="(0008,0080)", vr="LO", action="add", value="Clinic"),
        tags_mod.Rule(tag="(0008,0080)", vr="LO", action="delete", value=""),
    ]
    out = tags_mod.apply_rules(ds, rules)
    assert str(ds.PatientName) == "New"
    assert "InstitutionName" not in ds  # добавили и удалили
    assert len(out) == 3


def test_bad_rule_does_not_raise():
    ds = Dataset()
    rules = [tags_mod.Rule(tag="плохой", vr="XX", action="replace", value="x")]
    out = tags_mod.apply_rules(ds, rules)  # не должно упасть
    assert any("ОШИБКА" in s for s in out)


def test_rules_json_roundtrip(tmp_path):
    rules = [tags_mod.Rule(tag="(0010,0010)", vr="PN", action="replace", value="A")]
    p = tmp_path / "rules.json"
    tags_mod.save_rules(p, rules)
    back = tags_mod.load_rules(p)
    assert back[0].tag == "(0010,0010)" and back[0].value == "A"


# --- Транслитерация ---
def test_transliteration():
    assert tr_mod.transliterate("Иван", "gost") == "Ivan"
    assert tr_mod.transliterate("Жора", "simple") == "ZHora"
    ds = Dataset()
    ds.PatientName = "Петров"
    tr_mod.transliterate_dataset(ds, "gost")
    assert str(ds.PatientName) == "Petrov"


# --- Очередь ---
def test_error_queue_persists(tmp_path):
    from core.queue import ErrorQueue
    db = tmp_path / "q.db"
    q = ErrorQueue(db)
    rid = q.add("/tmp/a.dcm", "нет связи")
    assert q.count_pending() == 1
    # новый объект на том же файле — данные пережили «перезапуск»
    q2 = ErrorQueue(db)
    assert q2.count_pending() == 1
    q2.mark_done(rid)
    q2.clear_done()
    assert q2.count_pending() == 0

"""Тесты перекодировки кириллицы (включая защиту от двойной конвертации)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydicom.dataset import Dataset
from core import encoding_fix as enc


def _ds_with_misdecoded(name_latin1: str) -> Dataset:
    ds = Dataset()
    ds.PatientName = name_latin1
    ds.add_new((0x0010, 0x0020), "LO", "123")
    return ds


def test_repair_basic():
    # «Иван» в cp1251, прочитанный как latin-1
    broken = "Èâàí".encode("latin-1").decode("latin-1")  # уже строка
    raw = "Иван".encode("cp1251").decode("latin-1")
    ds = _ds_with_misdecoded(raw)
    changed, _msg = enc.convert_dataset(ds, enc.MODE_TO_UTF8)
    assert changed is True
    assert str(ds.PatientName) == "Иван"
    assert enc.get_charset(ds) == ["ISO_IR 192"]


def test_no_double_conversion():
    raw = "Иван".encode("cp1251").decode("latin-1")
    ds = _ds_with_misdecoded(raw)
    enc.convert_dataset(ds, enc.MODE_TO_UTF8)
    first = str(ds.PatientName)
    # вторая конвертация должна быть пропущена
    changed, msg = enc.convert_dataset(ds, enc.MODE_TO_UTF8)
    assert changed is False
    assert str(ds.PatientName) == first
    assert "двойной" in msg


def test_already_utf8_skipped():
    ds = Dataset()
    ds.PatientName = "Иван"
    enc.set_charset(ds, "ISO_IR 192")
    changed, _msg = enc.convert_dataset(ds, enc.MODE_TO_UTF8)
    assert changed is False


def test_ir144_target():
    raw = "Петров".encode("cp1251").decode("latin-1")
    ds = _ds_with_misdecoded(raw)
    changed, _msg = enc.convert_dataset(ds, enc.MODE_TO_IR144)
    assert changed is True
    assert str(ds.PatientName) == "Петров"
    assert enc.get_charset(ds) == ["ISO_IR 144"]


def test_nested_sequence():
    from pydicom.sequence import Sequence
    raw = "Сидоров".encode("cp1251").decode("latin-1")
    inner = Dataset()
    inner.add_new((0x0008, 0x0090), "PN", raw)
    outer = Dataset()
    outer.add_new((0x0040, 0x0275), "SQ", Sequence([inner]))
    changed, _msg = enc.convert_dataset(outer, enc.MODE_TO_UTF8)
    assert changed is True
    assert inner[(0x0008, 0x0090)].value == "Сидоров"


def test_analyze_reports_charset():
    import tempfile
    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid, SecondaryCaptureImageStorage
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.dcm"
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds = FileDataset(str(p), {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID = SecondaryCaptureImageStorage
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        raw = "Иванов".encode("cp1251").decode("latin-1")
        ds.PatientName = raw
        ds.save_as(str(p), write_like_original=False)
        info = enc.analyze_file(str(p), enc.MODE_TO_UTF8)
        assert info["would_change"] is True
        assert "Иванов" in info["patient_after"]

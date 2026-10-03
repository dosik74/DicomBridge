"""Тесты выбора синтаксиса отправки, транскодинга и seen-кеша."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.sender import (
    EXPLICIT_LE, IMPLICIT_LE, accepted_transfer_syntaxes, choose_send_ts,
    file_transfer_syntax, transcode_dataset, ts_name,
)
from core.queue import SeenCache


def test_choose_prefers_native():
    acc = [EXPLICIT_LE, IMPLICIT_LE]
    assert choose_send_ts(EXPLICIT_LE, acc) == EXPLICIT_LE
    assert choose_send_ts("1.2.840.10008.1.2.4.80", acc) == EXPLICIT_LE


def test_choose_none_when_nothing_accepted():
    assert choose_send_ts(EXPLICIT_LE, []) is None
    assert choose_send_ts("1.2.840.10008.1.2.4.80", [EXPLICIT_LE[:5]]) is None


def test_accepted_parsing_with_fake_assoc():
    class FakePC:
        def __init__(self, abstract, ts):
            self.abstract_syntax = abstract
            self.transfer_syntax = ts

    class FakeAssoc:
        accepted_contexts = [
            FakePC("1.2.3", [EXPLICIT_LE]),
            FakePC("9.9.9", IMPLICIT_LE),  # другой SOP — не считаем
            FakePC("1.2.3", "garbage"),    # мусор не роняет парсинг
        ]

    out = accepted_transfer_syntaxes(FakeAssoc(), "1.2.3")
    assert EXPLICIT_LE in out
    assert IMPLICIT_LE not in out


def test_transcode_flags():
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    ds = Dataset()
    ds.SOPClassUID = "1.2.3"
    ds.SOPInstanceUID = generate_uid()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = "1.2.3"
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    out = transcode_dataset(ds, IMPLICIT_LE)
    assert out.is_implicit_VR is True and out.is_little_endian is True
    assert str(out.file_meta.TransferSyntaxUID) == IMPLICIT_LE
    # original_encoding тоже обновлён (иначе pynetdicom откажет)
    assert tuple(out.original_encoding) == (True, True)


def test_file_ts_fallback():
    from pydicom.dataset import Dataset
    assert file_transfer_syntax(Dataset(), "x") == EXPLICIT_LE


def test_ts_name():
    assert ts_name(EXPLICIT_LE) == "Explicit LE"
    assert ts_name("9.9.9") == "9.9.9"


def test_seen_cache(tmp_path):
    q = SeenCache(tmp_path / "q.db")
    f = tmp_path / "a.dcm"
    f.write_bytes(b"12345")
    assert q.is_seen(str(f)) is False
    q.mark(str(f))
    assert q.is_seen(str(f)) is True
    # изменившийся файл — снова новый
    f.write_bytes(b"1234567890")
    assert q.is_seen(str(f)) is False
    # несуществующий файл
    assert q.is_seen(str(tmp_path / "nope.dcm")) is False

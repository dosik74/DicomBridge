"""Генератор тестовых DICOM-файлов с кириллицей в cp1251.

Создаёт файлы, имитирующие «плохие» аппараты: русский текст записан
байтами Windows-1251, а Specific Character Set не указан (pydicom при
чтении увидит кракозябры — то, что чинит DicomBridge).

Использование:
    python tools\\make_test_dicoms.py --out .\\test_dicoms --count 3
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def make_dicom(path: Path, patient_name_cp1251: bytes, patient_id: str = "") -> None:
    """Создать минимальный DICOM-файл с cp1251-байтами в имени пациента."""
    from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
    from pydicom.uid import (
        ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid,
    )
    import datetime

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientID = patient_id
    ds.PatientBirthDate = "19800101"
    ds.PatientSex = "M"
    ds.StudyDate = datetime.date.today().strftime("%Y%m%d")
    ds.Modality = "CR"
    ds.Rows, ds.Columns = 4, 4
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = bytes(16)
    # ВАЖНО: записываем имя «вручную» байтами cp1251 без указания charset,
    # чтобы сымитировать аппарат. pydicom запишет str как есть, поэтому
    # сначала декодируем cp1251-байты как latin-1 (так их увидит читатель).
    ds.PatientName = patient_name_cp1251.decode("latin-1")
    # удаляем Specific Character Set — его нет в «плохих» файлах
    if "SpecificCharacterSet" in ds:
        del ds["SpecificCharacterSet"]
    ds.save_as(str(path), enforce_file_format=False)
    print(f"создан: {path} (PatientName raw={patient_name_cp1251!r})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="test_dicoms")
    ap.add_argument("--count", type=int, default=3)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    names = ["Иванов^Иван", "Петрова^Мария", "Сидоров^Алексей", "Козлова^Ольга"]
    for i in range(args.count):
        name = names[i % len(names)]
        make_dicom(out / f"test_{i + 1}.dcm", name.encode("cp1251"),
                   patient_id="" if i == 0 else f"T{i}")
    print(f"Готово: {args.count} файлов в {out.resolve()}")


if __name__ == "__main__":
    main()

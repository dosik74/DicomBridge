"""Генерация UID и детерминированного Patient ID.

- is_valid_uid(): проверка корректности UID (цифры и точки, длина ≤ 64,
  нет пустых компонентов и ведущих нулей).
- ensure_uids(): создаёт новые Study/Series/SOP UID, если они отсутствуют
  или некорректны. Согласованность: файлы одного исследования получают
  одинаковый новый Study UID через кеш старый→новый. Обновляется также
  Media Storage SOP Instance UID в file_meta.
- generate_patient_id(): детерминированный ID из хеша ФИО+даты рождения,
  чтобы одинаковые пациенты получали одинаковый ID.
"""

import hashlib
import logging
import re

from pydicom.uid import generate_uid as _pydicom_generate_uid

log = logging.getLogger("uid")

_UID_RE = re.compile(r"^[0-9]+(\.[0-9]+)*$")


def is_valid_uid(uid: object) -> bool:
    """Проверить UID на корректность по правилам DICOM."""
    if uid is None:
        return False
    s = str(uid).strip()
    if not s or len(s) > 64:
        return False
    if not _UID_RE.match(s):
        return False
    for comp in s.split("."):
        if comp == "":
            return False
        if len(comp) > 1 and comp.startswith("0"):
            return False  # ведущие нули запрещены
    return True


def generate_patient_id(patient_name: str = "", birth_date: str = "") -> str:
    """Сформировать детерминированный Patient ID из ФИО и даты рождения.

    Одинаковые входные данные всегда дают одинаковый ID (SHA-256, первые
    12 hex-символов в верхнем регистре). Префикс 'P' чтобы ID начинался с буквы.
    """
    base = f"{(patient_name or '').strip().upper()}|{(birth_date or '').strip()}"
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:12].upper()
    return f"P{digest}"


def ensure_uids(ds, cache: dict, new_study: bool = True,
                new_series: bool = True, new_sop: bool = True) -> list:
    """Проверить/исправить UID в датасете. Возвращает список описаний изменений."""
    changes = []

    def _fix(keyword: str, do_fix: bool, cache_key: str | None = None) -> None:
        current = ds.get(keyword, None)
        cur_val = str(current) if current else ""
        if is_valid_uid(cur_val):
            return
        if not do_fix:
            changes.append(f"{keyword}: некорректен ({cur_val or 'пуст'}), оставлен как есть")
            log.warning("%s некорректен: %r", keyword, cur_val)
            return
        if cache_key is not None and cur_val and cur_val in cache:
            new_uid = cache[cur_val]
        else:
            new_uid = str(_pydicom_generate_uid())
            if cache_key is not None and cur_val:
                cache[cur_val] = new_uid
        try:
            setattr(ds, keyword, new_uid)
        except Exception:
            ds.add_new(_kw_to_tag(keyword), "UI", new_uid)
        changes.append(f"{keyword}: {cur_val or 'пуст'} -> {new_uid}")
        log.info("%s заменён: %r -> %s", keyword, cur_val, new_uid)

    # Study UID: группируем файлы одного исследования.
    # Если старый UID отсутствует, группируем по PatientID+StudyDate.
    if new_study or not is_valid_uid(str(ds.get("StudyInstanceUID", ""))):
        old_study = str(ds.get("StudyInstanceUID", "") or "")
        if not old_study or not is_valid_uid(old_study):
            fallback_key = (
                f"NOSTUDY:{ds.get('PatientID', '')}:{ds.get('StudyDate', '')}"
            )
            if fallback_key in cache:
                new_uid = cache[fallback_key]
                _set_tag(ds, "StudyInstanceUID", new_uid)
                changes.append(f"StudyInstanceUID: пуст -> {new_uid} (групповой)")
            else:
                _fix("StudyInstanceUID", new_study, cache_key="__study__")
                # запоминаем групповой ключ тоже
                try:
                    cache[fallback_key] = str(ds.StudyInstanceUID)
                except Exception:
                    pass
        else:
            _fix("StudyInstanceUID", new_study, cache_key="__study__")
    _fix("SeriesInstanceUID", new_series)
    old_sop = str(ds.get("SOPInstanceUID", "") or "")
    _fix("SOPInstanceUID", new_sop)

    # Media Storage SOP Instance UID обязан совпадать с SOP Instance UID
    try:
        sop = str(ds.SOPInstanceUID)
        if getattr(ds, "file_meta", None) is not None:
            meta_uid = str(getattr(ds.file_meta, "MediaStorageSOPInstanceUID", ""))
            if meta_uid != sop:
                ds.file_meta.MediaStorageSOPInstanceUID = sop
                changes.append("MediaStorageSOPInstanceUID синхронизирован с SOPInstanceUID")
        # Media Storage SOP Class UID тоже синхронизируем
        try:
            cls = str(ds.SOPClassUID)
            if getattr(ds, "file_meta", None) is not None:
                if str(getattr(ds.file_meta, "MediaStorageSOPClassUID", "")) != cls:
                    ds.file_meta.MediaStorageSOPClassUID = cls
        except Exception:
            pass
    except Exception as exc:
        log.error("синхронизация file_meta: %s", exc)
    void = old_sop  # (для читаемости, без effect)
    return changes


def _set_tag(ds, keyword: str, value: str) -> None:
    try:
        setattr(ds, keyword, value)
    except Exception:
        ds.add_new(_kw_to_tag(keyword), "UI", value)


def _kw_to_tag(keyword: str) -> tuple:
    mapping = {
        "StudyInstanceUID": (0x0020, 0x000D),
        "SeriesInstanceUID": (0x0020, 0x000E),
        "SOPInstanceUID": (0x0008, 0x0018),
    }
    return mapping[keyword]

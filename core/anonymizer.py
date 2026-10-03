"""Анонимизация DICOM-файлов.

Заменяет имя пациента на 'Anonymous', очищает идентифицирующие поля
пациента/учреждения/персонала. Дополнительные теги можно чистить через
правила редактирования (core.tags). Приватные теги удаляются опционально.

ВАЖНО: анонимизация не гарантирует полного удаления идентифицирующих
данных (пиксельные данные, оверлеи, приватные теги производителей).
Предупреждение об этом показывается в GUI.
"""

import logging

log = logging.getLogger("anonymize")

# (тег, keyword pydicom) — поля, которые очищаем
_CLEAR_FIELDS = [
    ((0x0010, 0x0010), "PatientName"),        # -> Anonymous (отдельно)
    ((0x0010, 0x0030), "PatientBirthDate"),
    ((0x0010, 0x1000), "OtherPatientIDs"),
    ((0x0010, 0x1001), "OtherPatientNames"),
    ((0x0010, 0x1040), "PatientAddress"),
    ((0x0010, 0x2154), "PatientTelephoneNumbers"),
    ((0x0010, 0x2155), "PatientTelecomInformation"),
    ((0x0008, 0x0080), "InstitutionName"),
    ((0x0008, 0x0081), "InstitutionAddress"),
    ((0x0008, 0x0090), "ReferringPhysicianName"),
    ((0x0008, 0x1048), "PhysiciansOfRecord"),
    ((0x0008, 0x1050), "PerformingPhysicianName"),
    ((0x0008, 0x1060), "NameOfPhysiciansReadingStudy"),
    ((0x0008, 0x1070), "OperatorsName"),
    ((0x0008, 0x1010), "StationName"),
    ((0x0010, 0x4000), "PatientComments"),
    ((0x0040, 0x0275), "RequestAttributesSequence"),  # чистим целиком ниже
]

ANON_WARNING = (
    "Анонимизация снижает риск идентификации, но не гарантирует полного "
    "удаления идентифицирующих данных (приватные теги, комментарии, "
    "встроенная информация в пиксельных данных)."
)


def anonymize(ds, keep_patient_id: bool = False, keep_sex: bool = False,
              remove_private: bool = True) -> list:
    """Анонимизировать датасет. Возвращает список описаний изменений."""
    changes = []
    # Имя -> Anonymous
    try:
        old = str(ds.get("PatientName", ""))
        ds.PatientName = "Anonymous"
        changes.append(f"PatientName: {old} -> Anonymous")
    except Exception as exc:
        log.error("анонимизация PatientName: %s", exc)

    if not keep_patient_id:
        _clear(ds, "PatientID", changes)
    if not keep_sex:
        _clear(ds, "PatientSex", changes)

    for _tag, keyword in _CLEAR_FIELDS:
        if keyword in ("PatientName",):
            continue
        if keyword == "RequestAttributesSequence" and keyword in ds:
            try:
                del ds[keyword]
                changes.append("RequestAttributesSequence: удалена")
            except Exception as exc:
                log.error("анонимизация %s: %s", keyword, exc)
            continue
        _clear(ds, keyword, changes)

    if remove_private:
        try:
            before = len(list(ds.keys()))
            ds.remove_private_tags()
            after = len(list(ds.keys()))
            changes.append(f"приватные теги удалены: {before - after} шт.")
        except Exception as exc:
            log.error("удаление приватных тегов: %s", exc)
    log.info("анонимизация: %d изменений", len(changes))
    return changes


def _clear(ds, keyword: str, changes: list) -> None:
    try:
        if keyword in ds:
            old = str(ds.get(keyword, ""))
            del ds[keyword]
            # Для обязательных полей ставим пустое значение вместо удаления,
            # чтобы не ломать структуру? DICOM допускает отсутствие (type 2/3),
            # поэтому просто удаляем и фиксируем.
            changes.append(f"{keyword}: очищено (было {old!r})")
    except Exception as exc:
        log.error("анонимизация %s: %s", keyword, exc)

"""Исправление кодировки кириллицы в DICOM-тегах.

Проблема: многие аппараты/программы пишут русский текст байтами Windows-1251,
но в заголовке кодировка не указана (или указана неверно). pydicom тогда
читает такие байты как latin-1 — получаются «кракозябры».

Режимы (Encoding.mode в config.ini):
  - none: ничего не менять;
  - cp1251_to_ir144: перекодировать из Windows-1251 в ISO_IR 144 (ISO-8859-5);
  - cp1251_to_utf8: перекодировать из Windows-1251 в UTF-8 (ISO_IR 192).

Во всех случаях выставляется корректный Specific Character Set (0008,0005),
обрабатываются все текстовые VR (PN, LO, SH, LT, ST, UT и при необходимости CS),
включая вложенные последовательности (SQ). Есть защита от двойной
перекодировки и функция analyze_file() для кнопки «Проанализировать файл».
"""

import logging
from typing import Any

log = logging.getLogger("encoding")

MODE_NONE = "none"
MODE_TO_IR144 = "cp1251_to_ir144"
MODE_TO_UTF8 = "cp1251_to_utf8"

TARGET_CHARSET = {
    MODE_TO_IR144: "ISO_IR 144",
    MODE_TO_UTF8: "ISO_IR 192",
}

# Текстовые VR, которые перекодируем. CS — только если там кириллица
# (обычно CS строго ASCII, но дешёвые аппараты пишут туда русский).
TEXT_VRS = {"PN", "LO", "SH", "LT", "ST", "UT", "CS"}


def get_charset(ds) -> list:
    """Прочитать Specific Character Set как список строк."""
    try:
        val = ds.get((0x0008, 0x0005), None)
        if val is None:
            return []
        raw = val.value if hasattr(val, "value") else val
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            return [str(x) for x in raw if str(x)]
        s = str(raw)
        return [p.strip() for p in s.split("\\") if p.strip()]
    except Exception:
        return []


def set_charset(ds, charset: str) -> None:
    """Выставить Specific Character Set (0008,0005)."""
    ds.add_new((0x0008, 0x0005), "CS", charset)


def _looks_like_cyrillic(text: str) -> bool:
    """Есть ли в строке символы кириллицы (после корректного декодирования)."""
    for ch in text:
        o = ord(ch)
        if 0x0400 <= o <= 0x04FF:  # кириллица в Unicode
            return True
    return False


def _looks_misdecoded(text: str) -> bool:
    """Похоже ли, что cp1251-байты были прочитаны как latin-1.

    Эвристика: символы из диапазона 0xC0-0xFF (latin-1 буквы с диакритикой),
    которые в cp1251 являются кириллицей. Чистый ASCII не трогаем.
    """
    if not text:
        return False
    suspicious = 0
    for ch in text:
        o = ord(ch)
        if 0xC0 <= o <= 0xFF:
            suspicious += 1
    return suspicious > 0


def _repair_string(value: str) -> tuple[str, bool]:
    """Починить одну строку: latin-1 -> bytes -> cp1251 -> unicode.

    Возвращает (новая_строка, была_ли_изменена).
    """
    if not isinstance(value, str) or not value:
        return value, False
    if _looks_like_cyrillic(value):
        return value, False  # уже нормальная кириллица — не трогаем
    if not _looks_misdecoded(value):
        return value, False
    try:
        raw = value.encode("latin-1")
    except UnicodeEncodeError:
        return value, False
    try:
        fixed = raw.decode("cp1251")
    except UnicodeDecodeError:
        return value, False
    if fixed == value:
        return value, False
    return fixed, True


def needs_conversion(ds, mode: str) -> bool:
    """Нужна ли конвертация для данного файла и режима."""
    if mode == MODE_NONE:
        return False
    target = TARGET_CHARSET.get(mode)
    if not target:
        return False
    cs = get_charset(ds)
    # Защита от двойной перекодировки: файл уже в целевом стандарте.
    if target in cs:
        return False
    # Если файл уже в любой кириллической/юникод кодировке — не конвертируем.
    if "ISO_IR 144" in cs or "ISO_IR 192" in cs:
        return False
    return True


def convert_dataset(ds, mode: str) -> tuple[bool, str]:
    """Перекодировать текстовые элементы датасета.

    Возвращает (что-то_менялось, сообщение_для_лога).
    """
    if mode == MODE_NONE:
        return False, "кодировка: режим 'не менять'"
    target = TARGET_CHARSET.get(mode)
    if not target:
        return False, f"кодировка: неизвестный режим {mode!r}"

    if not needs_conversion(ds, mode):
        cs = get_charset(ds)
        msg = f"кодировка: пропущена (уже {cs or 'default'}), защита от двойной конвертации"
        log.warning("%s", msg)
        return False, msg

    changed_count = [0]

    def _fix_value(v: Any) -> Any:
        # pydicom хранит PN как объект PersonName, а не str — чиним через str()
        if v is None:
            return v
        if isinstance(v, str):
            new_v, changed = _repair_string(v)
            if changed:
                changed_count[0] += 1
            return new_v
        try:
            s = str(v)
            if s and s != str(type(v)):
                new_s, changed = _repair_string(s)
                if changed:
                    changed_count[0] += 1
                    return new_s
        except Exception:
            pass
        return v

    def _walk(dataset) -> None:
        for elem in dataset:
            try:
                if elem.VR == "SQ":
                    for item in elem.value or []:
                        _walk(item)
                elif elem.VR in TEXT_VRS:
                    if isinstance(elem.value, list):
                        elem.value = [_fix_value(x) for x in elem.value]
                    else:
                        elem.value = _fix_value(elem.value)
            except Exception as exc:  # один плохой элемент не роняет файл
                log.error("кодировка: элемент %s пропущен: %s", elem.tag, exc)

    _walk(ds)
    if changed_count[0]:
        set_charset(ds, target)
        msg = f"кодировка: перекодировано полей: {changed_count[0]}, выставлен {target}"
        log.info("%s", msg)
        return True, msg
    # Даже если кириллицы не нашли, выставляем целевой charset,
    # чтобы PACS одинаково интерпретировал файл? Нет — не выставляем,
    # чтобы не ломать чисто-латинские файлы. Просто логируем.
    msg = "кодировка: кириллица cp1251 не найдена, файл не менялся"
    log.info("%s", msg)
    return False, msg


def analyze_file(path: str, mode: str) -> dict:
    """Анализ файла для GUI: текущая кодировка + превью имени пациента.

    Возвращает словарь: charset, patient_before, patient_after, would_change.
    Ничего не записывает на диск.
    """
    from pydicom import dcmread

    info: dict = {
        "path": path,
        "charset": [],
        "patient_before": "",
        "patient_after": "",
        "would_change": False,
        "message": "",
    }
    try:
        ds = dcmread(path, force=True)
    except Exception as exc:
        info["message"] = f"Не удалось прочитать файл: {exc}"
        return info
    info["charset"] = get_charset(ds) or ["(не указан — default)"]
    try:
        info["patient_before"] = str(ds.get("PatientName", ""))
    except Exception:
        info["patient_before"] = ""
    if not needs_conversion(ds, mode):
        info["patient_after"] = info["patient_before"]
        info["message"] = (
            "Конвертация не требуется (уже целевая кодировка или режим 'не менять')."
        )
        return info
    before = info["patient_before"]
    after, changed = _repair_string(before)
    info["patient_after"] = after
    info["would_change"] = changed
    info["message"] = (
        "Кириллица будет исправлена." if changed
        else "В имени пациента кириллица cp1251 не detected; проверьте другие поля."
    )
    return info

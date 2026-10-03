"""Транслитерация кириллицы в латиницу (опционально).

Два стандарта на выбор (Transliteration.standard):
  - gost: ГОСТ 7.79 (Ж->Zh, Х->Kh, Ц->C, Ч->Ch, Ш->Sh, Щ->Shh, Ю->Yu, Я->Ya …)
  - simple: упрощённая таблица (Ж->ZH, Х->KH, Ц->TS, Ч->CH, Ш->SH, Щ->SCH …)
  - ukrainian: украинская КМУ №55 (Г->H, Ґ->G, Є->Ye, І->I, Ї->Yi, Й->Y …)
    (как опция выбора правила транслитерации в оригинальной программе)
"""

import logging

log = logging.getLogger("translit")

# ГОСТ 7.79, система Б (однозначная, с диграфами)
_GOST = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "Yo",
    "Ж": "Zh", "З": "Z", "И": "I", "Й": "J", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "Kh", "Ц": "C", "Ч": "Ch", "Ш": "Sh", "Щ": "Shh",
    "Ъ": '"', "Ы": "Y", "Ь": "'", "Э": "E'", "Ю": "Yu", "Я": "Ya",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "c", "ч": "ch", "ш": "sh", "щ": "shh",
    "ъ": '"', "ы": "y", "ь": "'", "э": "e'", "ю": "yu", "я": "ya",
}

_SIMPLE = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
    "Ж": "ZH", "З": "Z", "И": "I", "Й": "I", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "KH", "Ц": "TS", "Ч": "CH", "Ш": "SH", "Щ": "SCH",
    "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "YU", "Я": "YA",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Украинская транслитерация по правилам КМУ №55 (паспортная).
# Русские буквы, отсутствующие в украинском, маппятся по ГОСТ.
_UKRAINIAN = {
    "А": "A", "Б": "B", "В": "V", "Г": "H", "Ґ": "G", "Д": "D", "Е": "E",
    "Є": "Ye", "Ж": "Zh", "З": "Z", "И": "Y", "І": "I", "Ї": "Yi",
    "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O",
    "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U", "Ф": "F",
    "Х": "Kh", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh", "Щ": "Shch",
    "Ю": "Yu", "Я": "Ya", "Ь": "", "Ъ": "", "Ы": "Y", "Э": "E", "Ё": "Yo",
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
    "є": "ie", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i",
    "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ю": "iu", "я": "ia", "ь": "", "ъ": "", "ы": "y", "э": "e", "ё": "yo",
}

TABLES = {"gost": _GOST, "simple": _SIMPLE, "ukrainian": _UKRAINIAN}
TEXT_VRS = {"PN", "LO", "SH", "LT", "ST", "UT"}


def transliterate(text: str, standard: str = "gost") -> str:
    """Перевести одну строку в латиницу."""
    table = TABLES.get(standard, _GOST)
    return "".join(table.get(ch, ch) for ch in text)


def transliterate_dataset(ds, standard: str = "gost") -> tuple[bool, str]:
    """Транслитерировать все текстовые поля датасета (включая SQ)."""
    count = [0]

    def _walk(dataset) -> None:
        for elem in dataset:
            try:
                if elem.VR == "SQ":
                    for item in elem.value or []:
                        _walk(item)
                elif elem.VR in TEXT_VRS:
                    if isinstance(elem.value, list):
                        new_vals = []
                        for v in elem.value:
                            s = v if isinstance(v, str) else str(v)
                            nv = transliterate(s, standard)
                            if nv != s:
                                count[0] += 1
                            new_vals.append(nv)
                        elem.value = new_vals
                    elif elem.value is not None:
                        s = elem.value if isinstance(elem.value, str) else str(elem.value)
                        nv = transliterate(s, standard)
                        if nv != s:
                            count[0] += 1
                            elem.value = nv
            except Exception as exc:
                log.error("транслитерация: элемент %s пропущен: %s", elem.tag, exc)

    _walk(ds)
    msg = f"транслитерация ({standard}): изменено полей: {count[0]}"
    if count[0]:
        log.info("%s", msg)
        return True, msg
    return False, msg

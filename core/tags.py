"""Правила редактирования DICOM-тегов.

Правило: тег (группа,элемент), VR, действие (add/replace/delete/clear), значение.
Применяются автоматически к каждому новому файлу. Некорректное правило
не роняет обработку — ошибка пишется в лог.

Форматы тега: '(0010,0010)', '0010,0010', '00100010', '0010 0010'.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass

log = logging.getLogger("tags")

ACTIONS = ("add", "replace", "delete", "clear")
ACTION_NAMES = {
    "add": "добавить",
    "replace": "заменить",
    "delete": "удалить",
    "clear": "очистить",
}

_TAG_RE = re.compile(
    r"^\(?\s*([0-9A-Fa-f]{4})\s*[, ]\s*([0-9A-Fa-f]{4})\s*\)?$|^\(?\s*([0-9A-Fa-f]{8})\s*\)?$"
)

_VALID_VR = {
    "AE", "AS", "AT", "CS", "DA", "DS", "DT", "FL", "FD", "IS", "LO", "LT",
    "OB", "OD", "OF", "OL", "OV", "OW", "PN", "SH", "SL", "SQ", "SS", "ST",
    "TM", "UC", "UI", "UL", "UN", "UR", "US", "UT",
}


@dataclass
class Rule:
    tag: str = ""      # например "(0010,0010)"
    vr: str = ""       # например "PN"
    action: str = "replace"
    value: str = ""


def parse_tag(s: str) -> tuple:
    """Разобрать строку тега в кортеж (group, element). Бросает ValueError."""
    m = _TAG_RE.match((s or "").strip())
    if not m:
        raise ValueError(f"неверный формат тега: {s!r} (пример: 0010,0010)")
    if m.group(3):
        hex8 = m.group(3)
        return int(hex8[:4], 16), int(hex8[4:], 16)
    return int(m.group(1), 16), int(m.group(2), 16)


def tag_to_str(tag: tuple) -> str:
    return f"({tag[0]:04X},{tag[1]:04X})"


def validate_rule(rule: Rule) -> list:
    """Проверить правило. Возвращает список предупреждений (пусто = ок).

    Бросает ValueError при критической ошибке (плохой тег/VR/действие).
    Предупреждение о нестандартном теге — возвращается, а не бросается.
    """
    warnings = []
    if rule.action not in ACTIONS:
        raise ValueError(f"неизвестное действие: {rule.action!r}")
    tag = parse_tag(rule.tag)  # ValueError при плохом формате
    vr = (rule.vr or "").strip().upper()
    if not vr:
        raise ValueError("не указан VR")
    if vr not in _VALID_VR:
        raise ValueError(f"неизвестный VR: {vr!r}")
    if rule.action in ("add", "replace") and not rule.value:
        raise ValueError("для действий add/replace нужно значение")
    # соответствие VR словарю pydicom
    try:
        from pydicom.datadict import dictionary_VR, DicomDictionary
        key = (tag[0] << 16) | tag[1]
        if key in DicomDictionary:
            dict_vr = dictionary_VR(key)
            if dict_vr != vr:
                warnings.append(
                    f"тег {tag_to_str(tag)} в словаре имеет VR={dict_vr}, а в правиле VR={vr}"
                )
        else:
            warnings.append(f"тег {tag_to_str(tag)} нестандартный (нет в словаре DICOM)")
    except Exception:
        pass
    return warnings


def apply_rules(ds, rules: list) -> list:
    """Применить список правил к датасету. Возвращает описания изменений."""
    from pydicom.dataelem import DataElement

    applied = []
    for i, rule in enumerate(rules):
        try:
            tag = parse_tag(rule.tag)
            vr = (rule.vr or "").strip().upper()
            if vr not in _VALID_VR:
                raise ValueError(f"неизвестный VR: {vr!r}")
            if rule.action == "delete":
                if tag in ds:
                    del ds[tag]
                    applied.append(f"правило {i + 1}: {tag_to_str(tag)} удалён")
                else:
                    applied.append(f"правило {i + 1}: {tag_to_str(tag)} уже отсутствует")
            elif rule.action == "clear":
                if tag in ds:
                    ds[tag].value = "" if vr not in ("SQ",) else []
                    applied.append(f"правило {i + 1}: {tag_to_str(tag)} очищен")
            elif rule.action in ("add", "replace"):
                if rule.action == "add" and tag in ds:
                    applied.append(
                        f"правило {i + 1}: {tag_to_str(tag)} уже есть, add пропущен"
                    )
                    continue
                ds.add_new(tag, vr, rule.value)
                applied.append(
                    f"правило {i + 1}: {tag_to_str(tag)} {ACTION_NAMES[rule.action]} = {rule.value!r}"
                )
            else:
                raise ValueError(f"неизвестное действие: {rule.action!r}")
        except Exception as exc:
            # некорректное правило — в лог, обработка продолжается
            log.error("правило %d (%r) пропущено: %s", i + 1, rule, exc)
            applied.append(f"правило {i + 1}: ОШИБКА — {exc}")
    return applied


def rules_from_dicts(items: list) -> list:
    """Построить список Rule из списка словарей (для JSON)."""
    out = []
    for d in items or []:
        try:
            out.append(Rule(
                tag=str(d.get("tag", "")),
                vr=str(d.get("vr", "")).upper(),
                action=str(d.get("action", "replace")),
                value=str(d.get("value", "")),
            ))
        except Exception as exc:
            log.error("пропущено правило из JSON: %s (%s)", d, exc)
    return out


def rules_to_dicts(rules: list) -> list:
    return [asdict(r) for r in rules]


def load_rules(path) -> list:
    """Загрузить правила из JSON-файла. Возвращает список Rule."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return rules_from_dicts(data)
        return rules_from_dicts(data.get("rules", []))
    except Exception as exc:
        log.error("не удалось загрузить правила %s: %s", p, exc)
        return []


def save_rules(path, rules: list) -> None:
    """Сохранить правила в JSON-файл."""
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(rules_to_dicts(rules), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

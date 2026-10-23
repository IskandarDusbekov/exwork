"""Qator turlarini aniqlash: ishchilar / mashinalar / boshqa (reja §2.5–2.6)."""
import re
from typing import List, Optional, Tuple

WORKERS = "workers"      # I ustun — ish haqi
MACHINES = "machines"    # J ustun — mashina va mexanizmlar
OTHER = "other"          # K ustun — materiallar
CABLE = "cable"          # L ustun — kabel-o'tkazgich mahsulotlari (faqat smart: RES bo'limi yoki qo'lda)
EQUIPMENT = "equipment"  # M ustun — uskunalar (faqat smart: RES bo'limi yoki qo'lda)

KIND_COLUMN = {WORKERS: "I", MACHINES: "J", OTHER: "K", CABLE: "L", EQUIPMENT: "M"}
COLUMN_KIND = {col: kind for kind, col in KIND_COLUMN.items()}


def _compact_unit(unit_raw) -> str:
    s = str(unit_raw).lower().replace(" ", " ").replace("ё", "е")
    s = re.sub(r"[\s\.\,_]+", " ", s)
    s = s.replace("-", " ").replace("/", " ").replace("\\", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace(" ", "")


def is_chel_ch(unit_raw) -> bool:
    if unit_raw is None:
        return False
    compact = _compact_unit(unit_raw)
    if "чел" not in compact:
        return False
    return ("час" in compact) or (re.search(r"чел.*ч", compact) is not None)


def is_mash_ch(unit_raw) -> bool:
    if unit_raw is None:
        return False
    compact = _compact_unit(unit_raw)
    if "маш" not in compact:
        return False
    return ("час" in compact) or (re.search(r"маш.*ч", compact) is not None)


def _tokenize_ru(s: str) -> List[str]:
    s = s.lower().replace(" ", " ").replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-я]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.split() if s else []


def is_workers_labor(name_raw) -> bool:
    if name_raw is None:
        return False
    tokens = _tokenize_ru(str(name_raw))
    joined = " ".join(tokens)
    has_workers = any(t.startswith("рабоч") or t.startswith("раб") for t in tokens) or ("рабоч" in joined)
    has_builder = any(t.startswith("строит") or t.startswith("стро") for t in tokens) or ("строит" in joined)
    return has_workers and has_builder


def is_machinists_labor(name_raw) -> bool:
    if name_raw is None:
        return False
    tokens = _tokenize_ru(str(name_raw))
    joined = " ".join(tokens)
    return ("машинист" in joined) or any(t.startswith("маш") for t in tokens)


def is_workers_row(name_raw, unit_raw) -> bool:
    """Ishchilar mehnati qatori: nol qoidasi (§2.5) va I ustun uchun."""
    return is_chel_ch(unit_raw) and is_workers_labor(name_raw) and not is_machinists_labor(name_raw)


def section_kind(text) -> Optional[str]:
    """Resurs faylidagi bo'lim sarlavhasi («ТРУДОВЫЕ РЕСУРСЫ», «ОБОРУДОВАНИЕ» …) -> kategoriya."""
    t = str(text or "").lower().replace("ё", "е")
    if not t.strip() or "итого" in t or "всего" in t:
        return None
    if "оборудован" in t:
        return EQUIPMENT
    if "кабел" in t:
        return CABLE
    if "машин" in t or "механизм" in t:
        return MACHINES
    if "труд" in t:
        return WORKERS
    if "материал" in t or "инертн" in t or "конструкц" in t:
        return OTHER
    return None


_CABLE_WORDS = ("кабел", "провод", "шнур")
_EQUIPMENT_WORDS = ("оборудован", "насос", "трансформатор", "лифт", "кондиционер", "вентилятор",
                    "электродвигател", "котел", "водонагревател", "счетчик", "щит распределит")


def name_hint(name_raw) -> Optional[Tuple[str, str]]:
    """Nomdan taxmin (faqat TAKLIF uchun): ``(kategoriya, topilgan so'z)`` yoki None.
    «проволока» kabel emas — «провол» «провод» bilan boshlanmaydi."""
    tokens = _tokenize_ru(str(name_raw or ""))
    joined = " ".join(tokens)
    for word in _EQUIPMENT_WORDS:
        if (" " in word and word in joined) or any(t.startswith(word) for t in tokens):
            return EQUIPMENT, word
    for word in _CABLE_WORDS:
        if any(t.startswith(word) for t in tokens):
            return CABLE, word
    return None


def row_kind(name_raw, unit_raw) -> str:
    if is_workers_row(name_raw, unit_raw):
        return WORKERS
    if is_mash_ch(unit_raw) or is_machinists_labor(name_raw):
        return MACHINES
    return OTHER

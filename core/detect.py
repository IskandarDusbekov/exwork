"""Listlarni aniqlash (reja T2): lokal (БВ) va resurs (БР) listlarini juftlash.

Bu faqat standart tanlov — foydalanuvchi uni o'zgartirishi mumkin. Hisob-kitobga ta'sir qilmaydi.
"""
from typing import List, Optional, Tuple

LOCAL_MARKS = ("БВ", "BV")
RES_MARKS = ("БР", "BR")


def _prefix(name: str) -> str:
    return name.rsplit("_", 1)[0].strip() if "_" in name else ""


def _has_mark(name: str, marks) -> bool:
    upper = name.upper()
    return any(m in upper for m in marks)


def pair_sheets(local_sheets: List[str], res_sheets: List[str],
                local_choice: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """``(lokal_list, resurs_list)``: avval «…_БВ» ↔ «…_БР» (bir xil prefiks), bo'lmasa birinchisi."""
    if not local_sheets or not res_sheets:
        return (local_sheets[0] if local_sheets else None, res_sheets[0] if res_sheets else None)

    local = local_choice if local_choice in local_sheets else next(
        (s for s in local_sheets if _has_mark(s, LOCAL_MARKS)), local_sheets[0])

    prefix = _prefix(local)
    res_marked = [s for s in res_sheets if _has_mark(s, RES_MARKS)]
    if prefix:
        same = [s for s in res_marked if _prefix(s) == prefix] or [s for s in res_sheets if _prefix(s) == prefix and s != local]
        if same:
            return local, same[0]
    if res_marked:
        return local, res_marked[0]
    others = [s for s in res_sheets if s != local]
    return local, (others or res_sheets)[0]

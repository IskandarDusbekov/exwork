"""Smeta tuzilishini aniqlash (reja T1–T3).

* ``pair_sheets`` — lokal (БВ) va resurs (БР) listlarini juftlash (faqat standart tanlov).
* ``detect_layout`` — jadval sarlavhasi, «1 2 3 4…» raqamlash qatori, nom / birlik / narx
  ustunlari va ma'lumot oralig'i. Resursga bo'sh ustun qo'shilgan-qo'shilmaganidan qat'i nazar
  ishlaydi: ustunlar sarlavha matni bo'yicha topiladi.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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


# ===================== jadval tuzilishi =====================
_NAME_RE = re.compile(r"наименован")
# «Единица измерения», «ЕД.ИЗМ», «Ед. изм.» — lekin «на.ед.изм.» (narx sarlavhasi) emas
_UNIT_RE = re.compile(r"(?:^|[\s|(])(?:единиц[аы]\s+измер|ед\.?\s*изм)")
_TOTAL_RE = re.compile(r"^(итого|всего)\s+по\s+(локальн|смет)")
HEADER_SCAN_ROWS = 80


@dataclass
class Layout:
    header_row: int
    numbering_row: Optional[int]
    data_start: int
    data_stop: int  # oxirgi qatordan keyingisi (ИТОГО ПО ЛОКАЛЬНОЙ… yoki max_row + 1)
    name_col: int
    unit_col: int
    price_col: Optional[int] = None
    warnings: List[str] = field(default_factory=list)


def cell_value(ws, row: int, col: int):
    """Katak qiymati; ``ws.cell()`` kabi yangi katak yaratmaydi."""
    cell = ws._cells.get((row, col))
    return cell.value if cell is not None else None


def _norm(v) -> str:
    if v is None:
        return ""
    s = str(v).replace(" ", " ").replace("ё", "е").lower()
    return re.sub(r"\s+", " ", s).strip()


def _merged_value_map(ws, top: int, bottom: int) -> Dict[Tuple[int, int], object]:
    """Birlashgan kataklar sarlavhada butun oraliqqa tegishli: qiymatni hamma kataklarga yoyamiz."""
    out = {}
    for mcr in ws.merged_cells.ranges:
        if mcr.max_row < top or mcr.min_row > bottom:
            continue
        v = cell_value(ws, mcr.min_row, mcr.min_col)
        for r in range(mcr.min_row, mcr.max_row + 1):
            for c in range(mcr.min_col, mcr.max_col + 1):
                out[(r, c)] = v
    return out


def header_texts(ws, top: int, bottom: int) -> Dict[int, str]:
    """Ustun -> sarlavha qatorlaridagi matnlar, yuqoridan pastga « | » bilan."""
    merged = _merged_value_map(ws, top, bottom)
    texts: Dict[int, List[str]] = {}
    for r in range(top, bottom + 1):
        for c in range(1, ws.max_column + 1):
            v = merged.get((r, c), cell_value(ws, r, c))
            t = _norm(v)
            if t and (not texts.get(c) or texts[c][-1] != t):
                texts.setdefault(c, []).append(t)
    return {c: " | ".join(parts) for c, parts in texts.items()}


def _as_int(v) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _is_numbering_row(ws, r: int) -> bool:
    """«1 2 3 4 5 6»: chapdan o'ngga dastlabki raqamlar 1, 2, 3 …"""
    nums = [_as_int(cell_value(ws, r, c)) for c in range(1, ws.max_column + 1)]
    nums = [n for n in nums if n is not None]
    return len(nums) >= 3 and nums[:3] == [1, 2, 3]


def _find_header_row(ws) -> Optional[int]:
    for r in range(1, min(ws.max_row, HEADER_SCAN_ROWS) + 1):
        texts = [_norm(cell_value(ws, r, c)) for c in range(1, ws.max_column + 1)]
        if any(_NAME_RE.search(t) for t in texts) and any(_UNIT_RE.search(t) for t in texts):
            return r
    return None


def _pick_price_col(texts: Dict[int, str], name_col: int) -> Optional[int]:
    best, best_score = None, 0
    for c in sorted(texts):
        if c <= name_col:
            continue
        t = texts[c]
        per_unit = bool(re.search(r"на\s*\.?\s*ед|на единицу", t))
        if "цена" in t and "сумм" not in t:
            score = 3
        elif per_unit and "базис" in t:
            score = 2
        elif per_unit and "текущ" not in t:
            score = 1
        else:
            score = 0
        if score > best_score:
            best, best_score = c, score
    return best


def detect_layout(ws, need_price: bool = False, stop_at_total: bool = True) -> Optional[Layout]:
    """Sarlavha topilmasa ``None`` — chaqiruvchi eski (compat) ustunlarga qaytadi."""
    header = _find_header_row(ws)
    if header is None:
        return None
    numbering = next((r for r in range(header + 1, min(header + 8, ws.max_row) + 1) if _is_numbering_row(ws, r)), None)
    bottom = (numbering - 1) if numbering else header
    texts = header_texts(ws, header, bottom)

    name_col = next((c for c in sorted(texts) if _NAME_RE.search(texts[c])), None)
    unit_col = next((c for c in sorted(texts) if c != name_col and _UNIT_RE.search(texts[c])), None)
    if name_col is None or unit_col is None:
        return None

    warnings = []
    if numbering is None:
        warnings.append(f"«1 2 3 4…» raqamlash qatori topilmadi; ma'lumot {header + 1}-qatordan deb olindi")
    data_start = (numbering or header) + 1

    data_stop = ws.max_row + 1
    for r in (range(data_start, ws.max_row + 1) if stop_at_total else ()):
        if any(_TOTAL_RE.match(_norm(cell_value(ws, r, c))) for c in range(1, min(ws.max_column, 4) + 1)):
            data_stop = r
            break

    price_col = _pick_price_col(texts, name_col) if need_price else None
    if need_price and price_col is None:
        warnings.append("Narx ustuni sarlavhasi topilmadi («ЦЕНА» yoki «в базисном уровне / на.ед.изм.»)")
    return Layout(header, numbering, data_start, data_stop, name_col, unit_col, price_col, warnings)

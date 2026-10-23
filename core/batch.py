"""Bir yoki bir nechta fayl yuklanganda: listlarni tahlil qilish, nusxalarni ajratish va juftlash.

Smetachi butun obyekt faylini (masalan, «А блок»: ichida bir nechta lokal va resurs listlari)
yoki ikkita alohida faylni yuklaydi. Har bir list mazmunidan aniqlanadi:

* **Ресурс** — sarlavhada narx ustuni bor («цена», «стоимость», «в базисном уровне»);
* **Локал** — resurslar ro'yxati («Наименование», «Ед. изм.»), narx ustuni yo'q;
* qolganlari (svodka va h.k.) — o'tkazib yuboriladi.

Juftlash belgilari (eng kuchlisi birinchi): lokaldagi resurs nomlarining qanchasi resurs
listida aynan bor; smeta raqami («№ 1-6»); list nomining prefiksi («202611450_БВ» ↔ «…_БР»);
obyekt nomi. Bu faqat taklif — smetachi tasdiqlaydi yoki o'zgartiradi.
"""
import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .detect import cell_value, detect_layout, header_texts
from .normalize import norm_name, norm_unit

LOCAL = "local"
RESOURCE = "resource"
UNKNOWN = "unknown"
ROLES = (LOCAL, RESOURCE, UNKNOWN)

TITLE_SCAN_ROWS = 14
_PRICE_WORDS = ("цена", "стоимост", "базис")
_NUMBER_RE = re.compile(r"№\s*([0-9A-Za-zА-Яа-яЁё][\w\-./]*)")
MIN_OVERLAP = 0.3     # bundan kam bo'lsa — juft taklif qilinmaydi
MIN_SCORE = 3.0       # umumiy ball (raqam zid bo'lsa jarima bilan)
SHARED_OVERLAP = 0.5  # bitta resursni ikkinchi lokal bilan bo'lishish uchun


@dataclass
class SheetInfo:
    file: int
    file_name: str
    sheet: str
    role: str
    reason: str
    title: str = ""
    number: str = ""
    stroyka: str = ""
    obj: str = ""
    part: str = ""
    rows: int = 0
    signature: str = ""
    duplicate_of: Optional[str] = None
    names: frozenset = field(default_factory=frozenset, repr=False)

    @property
    def key(self) -> str:
        return f"{self.file}:{self.sheet}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("names")
        d["key"] = self.key
        return d


@dataclass
class Pair:
    local: str                   # SheetInfo.key
    res: Optional[str]           # SheetInfo.key yoki None (juft topilmadi)
    score: float = 0.0
    overlap: float = 0.0         # lokal resurslarining qancha qismi resursda aynan bor
    reasons: List[str] = field(default_factory=list)
    shared: bool = False         # bu resurs boshqa lokal bilan ham juftlangan
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ===================== bitta list =====================
def _row_text(ws, r: int) -> str:
    parts = []
    for c in range(1, min(ws.max_column, 20) + 1):
        v = cell_value(ws, r, c)
        if v is not None and str(v).strip():
            parts.append(str(v).strip())
    return " ".join(parts)


def _low(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("ё", "е")).strip()


def _clean(s: str) -> str:
    """«на , МОСТ НА ПК 75+34» -> «МОСТ НА ПК 75+34»."""
    s = re.sub(r"^\s*(на|по)\b[\s,.:]*", "", s.strip(), flags=re.I)
    return s.strip(" ,.;:")


def _labelled(head: Dict[int, str], low: Dict[int, str], prefix: str, label: str) -> str:
    """«НАИМЕНОВАНИЕ ОБЪЕКТА: …» yoki «(наименование … объекта)» yorlig'idan oldingi qator."""
    for r, t in low.items():
        if t.startswith(prefix):
            return _clean(head[r].split(":", 1)[1] if ":" in head[r] else "")
    for r, t in low.items():
        if t.startswith("(") and label in t:
            prev = [p for p in range(r - 1, 0, -1) if head.get(p) and not low[p].startswith(("(", "форма"))]
            if prev:
                return _clean(head[prev[0]])
    return ""


def inspect_sheet(ws, file: int = 0, file_name: str = "") -> SheetInfo:
    head = {r: _row_text(ws, r) for r in range(1, min(ws.max_row, TITLE_SCAN_ROWS) + 1)}
    head = {r: t for r, t in head.items() if t}
    low = {r: _low(t) for r, t in head.items()}

    layout = detect_layout(ws, need_price=True, stop_at_total=False)
    if layout is None:
        return SheetInfo(file, file_name, ws.title, UNKNOWN,
                         "«Наименование» ва «Ед. изм.» сарлавҳали жадвал топилмади")

    bottom = (layout.numbering_row - 1) if layout.numbering_row else layout.header_row
    header = " | ".join(header_texts(ws, layout.header_row, bottom).values())
    joined = " ".join(low.values())
    has_price = any(w in header for w in _PRICE_WORDS)
    if has_price:
        role, reason = RESOURCE, "нарх устуни бор («цена / стоимость»)"
        if "форма n 6" in joined or "сметный расчет" in joined or "ресурсная смета" in joined:
            reason += " · Форма 6а"
        name_col, unit_col, price_col = layout.name_col, layout.unit_col, layout.price_col
        start, stop = layout.data_start, layout.data_stop
    else:
        role, reason = LOCAL, "ресурслар рўйхати, нарх устуни йўқ"
        if "форма n 5" in joined or "ведомост" in joined:
            reason += " · Форма 5"
        loc = detect_layout(ws) or layout
        name_col, unit_col, price_col = loc.name_col, loc.unit_col, None
        start, stop = loc.data_start, loc.data_stop

    title_row = next((r for r, t in low.items()
                      if "ведомост" in t or "сметный расчет" in t or "ресурсная смета" in t), None)
    title = head.get(title_row, "") if title_row else ""
    number = ""
    if title:
        m = _NUMBER_RE.search(title)
        number = m.group(1) if m else ""
    part = ""
    if title_row and head.get(title_row + 1) and not low[title_row + 1].startswith("("):
        part = _clean(head[title_row + 1])

    names, rows_for_sig = set(), []
    for r in range(start, stop):
        name = norm_name(cell_value(ws, r, name_col))
        unit = norm_unit(cell_value(ws, r, unit_col))
        if not name or not unit:
            continue
        names.add(name)
        extra = (cell_value(ws, r, price_col),) if price_col else (cell_value(ws, r, 5), cell_value(ws, r, 6))
        rows_for_sig.append((name, unit) + tuple(str(x) for x in extra))
    signature = hashlib.sha1(repr((role, rows_for_sig)).encode("utf-8")).hexdigest()[:16]

    return SheetInfo(
        file, file_name, ws.title, role, reason, title=title, number=number,
        stroyka=_labelled(head, low, "наименование стройки", "наименование стройки"),
        obj=_labelled(head, low, "наименование объекта", "наименование объекта"),
        part=part, rows=len(rows_for_sig), signature=signature, names=frozenset(names),
    )


def inspect_workbook(wb, file: int = 0, file_name: str = "") -> List[SheetInfo]:
    return [inspect_sheet(ws, file, file_name) for ws in wb.worksheets]


# ===================== nusxalar va juftlash =====================
def mark_duplicates(infos: Iterable[SheetInfo]) -> List[SheetInfo]:
    """Bir xil turdagi, mazmuni aynan bir xil listlar (masalan, ikki faylda takrorlangan) — birinchisi qoladi."""
    seen: Dict[Tuple[str, str], str] = {}
    out = []
    for info in infos:
        if info.role in (LOCAL, RESOURCE) and info.rows:
            k = (info.role, info.signature)
            if k in seen:
                info.duplicate_of = seen[k]
            else:
                seen[k] = info.key
        out.append(info)
    return out


def _prefix(sheet: str) -> str:
    return sheet.rsplit("_", 1)[0].strip().lower() if "_" in sheet else ""


def _same(a: str, b: str) -> bool:
    return bool(a) and _clean(a).lower() == _clean(b).lower()


def pair_score(loc: SheetInfo, res: SheetInfo) -> Tuple[float, float, List[str]]:
    overlap = len(loc.names & res.names) / max(1, len(loc.names))
    score = overlap * 10
    reasons = [f"ресурслар мослиги {overlap:.0%}"]
    if loc.number and loc.number == res.number:
        score += 2
        reasons.append(f"смета № {loc.number}")
    elif loc.number and res.number:
        score -= 4  # иккаласида ҳам рақам бор ва улар бошқа — кучли қарши белги
        reasons.append(f"смета рақами бошқа: № {loc.number} / № {res.number}")
    if _prefix(loc.sheet) and _prefix(loc.sheet) == _prefix(res.sheet):
        score += 3
        reasons.append(f"лист номи «{_prefix(loc.sheet)}…»")
    if _same(loc.obj, res.obj):
        score += 1.5
        reasons.append("объект номи бир хил")
    if _same(loc.part, res.part):
        score += 1
        reasons.append("қисм номи бир хил")
    if loc.file == res.file:
        score += 0.3
    return score, overlap, reasons


def pair_label(loc: SheetInfo) -> str:
    base = loc.part or loc.obj or loc.title or loc.sheet
    return f"{base} · № {loc.number}" if loc.number and loc.number not in base else base


def plan_pairs(infos: List[SheetInfo]) -> Tuple[List[Pair], List[SheetInfo]]:
    """``(juftliklar, juftsiz qolgan resurslar)``. Har bir lokal uchun eng yaxshi resurs;
    avval bittadan (bir resurs — bir lokal), keyin qolgan lokallar uchun umumiy resurs."""
    locals_ = [i for i in infos if i.role == LOCAL and not i.duplicate_of]
    resources = [i for i in infos if i.role == RESOURCE and not i.duplicate_of]
    cands = []
    for li, loc in enumerate(locals_):
        for res in resources:
            score, overlap, reasons = pair_score(loc, res)
            if overlap >= MIN_OVERLAP and score >= MIN_SCORE:
                conflict = bool(loc.number and res.number and loc.number != res.number)
                cands.append((score, li, res, overlap, reasons, conflict))
    cands.sort(key=lambda c: -c[0])

    chosen: Dict[int, Pair] = {}
    used = set()
    for score, li, res, overlap, reasons, conflict in cands:
        if li in chosen or res.key in used:
            continue
        chosen[li] = Pair(locals_[li].key, res.key, round(score, 2), round(overlap, 4), reasons)
        used.add(res.key)
    # o'z resursi topilmagan lokal — faqat juda o'xshash va raqami zid bo'lmagan resursni bo'lishadi
    for score, li, res, overlap, reasons, conflict in cands:
        if li not in chosen and overlap >= SHARED_OVERLAP and not conflict:
            chosen[li] = Pair(locals_[li].key, res.key, round(score, 2), round(overlap, 4),
                              reasons + ["бу ресурс бошқа локал билан умумий"], shared=True)

    pairs = []
    for li, loc in enumerate(locals_):
        p = chosen.get(li) or Pair(loc.key, None, reasons=["мос ресурс листи топилмади"])
        p.label = pair_label(loc)
        pairs.append(p)
    unused = [r for r in resources if r.key not in {p.res for p in pairs}]
    return pairs, unused


def apply_roles(infos: List[SheetInfo], roles: Dict[str, str]) -> List[SheetInfo]:
    """Smetachi list turini qo'lda o'zgartirsa (``{kalit: rol}``)."""
    for info in infos:
        role = roles.get(info.key)
        if role in ROLES and role != info.role:
            info.role = role
            info.reason = "қўлда белгиланди"
    return infos

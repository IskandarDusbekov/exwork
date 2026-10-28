"""Bir yoki bir nechta fayl yuklanganda: listlarni tahlil qilish, nusxalarni ajratish va juftlash.

Smetachi butun obyekt faylini (masalan, «А блок»: ichida bir nechta lokal va resurs listlari)
yoki ikkita alohida faylni yuklaydi. Har bir list mazmunidan aniqlanadi:

* **Ресурс** — sarlavhada narx ustuni bor («цена», «стоимость», «в базисном уровне»);
* **Локал** — resurslar ro'yxati («Наименование», «Ед. изм.»), narx ustuni yo'q;
* qolganlari (svodka va h.k.) — o'tkazib yuboriladi.

Narx ustuni bo'lsa ham resurs emas (``kind`` — nega o'tkazildi, sahifada ko'rsatiladi):
narx katalogi (miqdor ustuni yo'q), allaqachon narxlangan ЛРВ («1.1, 1.2 …» — ish va uning
resurslari). Narxsiz bo'lsa ham lokal emas: дефект акти (ishlar ro'yxati). Yashirin listlar
qoladi, lekin nusxa bo'lsa ko'rinadigani olinadi va juftlashda afzal.

Juftlash belgilari (eng kuchlisi birinchi): lokaldagi resurs nomlarining qanchasi resurs
listida aynan bor; smeta raqami («№ 1-6»); list nomining prefiksi («202611450_БВ» ↔ «…_БР»);
obyekt nomi. Bu faqat taklif — smetachi tasdiqlaydi yoki o'zgartiradi.
"""
import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .columns import detect_local_columns
from .detect import cell_value, detect_layout, header_texts, override_layout
from .normalize import norm_name, norm_unit

LOCAL = "local"
RESOURCE = "resource"
UNKNOWN = "unknown"
ROLES = (LOCAL, RESOURCE, UNKNOWN)

TITLE_SCAN_ROWS = 14
_PRICE_WORDS = ("цена", "стоимост", "базис")
_QTY_WORDS = ("колич", "кол-во", "кол.", "объем", "объём")
DOTTED_SHARE = 0.3  # 1-ustunda «1.1, 2.3» ulushi shundan ko'p — ish + resurslari (ЛРВ), resurs ro'yxati emas
DOTTED_SCAN = 300

# Nega o'tkazildi / nima ekan (``SheetInfo.kind``)
KIND_CATALOG = "catalog"
KIND_PRICED = "priced"
KIND_ACT = "act"
KIND_SUMMARY = "summary"
KIND_EMPTY = "empty"
KIND_LABELS = {
    KIND_CATALOG: "нарх каталоги",
    KIND_PRICED: "нархланган смета",
    KIND_ACT: "дефект акти",
    KIND_SUMMARY: "сводка / умумий ҳисоб",
    KIND_EMPTY: "бўш лист",
}
_SUMMARY_WORDS = ("свод", "объектн", "рекомендуем", "стартов", "общий расчет", "общий расчёт", "якуний", "ҳаражат",
                  "харажат", "обложк", "титул", "пояснительн", "расчет стоимости", "итоговый")
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
    hidden: bool = False  # Excel'da yashirin list
    kind: str = ""        # o'tkazilgan list nima ekan: KIND_* (sahifada ko'rsatish uchun)
    # tizim (yoki smetachi) qaysi ustunni nima deb oldi: {"name": 3, "unit": 4, "price": 7, "qty_unit": 5,
    # "qty_total": 6, "start": 22, "end": 850, "header": 11}; ``manual`` — smetachi qo'lda ko'rsatgan
    columns: dict = field(default_factory=dict)
    manual: bool = False
    headers: dict = field(default_factory=dict)  # ustun -> sarlavha matni / namuna (qo'lda tanlash ro'yxati uchun)
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
    # smetachi qo'lda tanlashi uchun: har bir resurs listi bilan mosligi, eng yaxshisi birinchi
    options: List[dict] = field(default_factory=list)

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


def _dotted_share(ws, col: int, start: int, stop: int) -> float:
    """1-ustundagi raqamlarning qanchasi «1.1», «12.3» (ish ichidagi resurs) ko'rinishida."""
    dotted = total = 0
    for r in range(start, min(stop, start + DOTTED_SCAN)):
        v = cell_value(ws, r, col)
        if v is None or str(v).strip() == "":
            continue
        total += 1
        if isinstance(v, float) and not v.is_integer():
            dotted += 1
        elif isinstance(v, str) and re.fullmatch(r"\d+\.\d+", v.strip()):
            dotted += 1
    return dotted / total if total >= 5 else 0.0


HEADER_COLS = 30  # qo'lda tanlash ro'yxatida nechta ustun


def _column_headers(ws, layout) -> Dict[str, str]:
    """Ustun -> «sarlavha · namuna» (qisqa): smetachi qaysi ustun nima ekanini tanlashi uchun."""
    texts = {}
    if layout is not None and layout.header_row:
        bottom = (layout.numbering_row - 1) if layout.numbering_row else layout.header_row
        texts = header_texts(ws, layout.header_row, bottom)
    start = layout.data_start if layout is not None else 1
    out = {}
    for c in range(1, min(ws.max_column, HEADER_COLS) + 1):
        sample = next((str(v).strip() for r in range(start, min(start + 15, ws.max_row + 1))
                       for v in [cell_value(ws, r, c)] if v is not None and str(v).strip()), "")
        head = texts.get(c, "")
        if head or sample:
            out[str(c)] = " · ".join(t[:40] for t in (head, sample) if t)
    return out


def _skipped(file, file_name, ws, kind: str, reason: str, hidden: bool, headers=None) -> SheetInfo:
    return SheetInfo(file, file_name, ws.title, UNKNOWN, reason, hidden=hidden, kind=kind, headers=headers or {})


def inspect_sheet(ws, file: int = 0, file_name: str = "", role: Optional[str] = None,
                  cols: Optional[dict] = None) -> SheetInfo:
    """``role`` — smetachi qo'lda bergan tur (lokal / resurs bo'lsa avtomatik tekshiruvlar — дефект акти,
    narxlangan smeta — o'tkazilmaydi); ``cols`` — qo'lda ko'rsatilgan ustun va qatorlar."""
    head = {r: _row_text(ws, r) for r in range(1, min(ws.max_row, TITLE_SCAN_ROWS) + 1)}
    head = {r: t for r, t in head.items() if t}
    low = {r: _low(t) for r, t in head.items()}
    joined = " ".join(low.values())
    hidden = ws.sheet_state != "visible"
    forced = role if role in (LOCAL, RESOURCE) else None
    cols = cols or {}

    layout = override_layout(ws, detect_layout(ws, need_price=True, stop_at_total=False), cols, need_price=True)
    headers = _column_headers(ws, layout)
    if layout is None:
        if forced:
            return SheetInfo(file, file_name, ws.title, forced,
                             "қўлда белгиланди — «Наименование» ва «Ед. изм.» устунлари топилмади, уларни кўрсатинг",
                             hidden=hidden, manual=bool(cols), headers=headers)
        if not head:
            return _skipped(file, file_name, ws, KIND_EMPTY, "бўш лист", hidden, headers)
        if any(w in joined or w in _low(ws.title) for w in _SUMMARY_WORDS):
            return _skipped(file, file_name, ws, KIND_SUMMARY, "сводка / умумий ҳисоб — смета эмас, нархланмайди", hidden, headers)
        return SheetInfo(file, file_name, ws.title, UNKNOWN,
                         "«Наименование» ва «Ед. изм.» сарлавҳали жадвал топилмади", hidden=hidden, headers=headers)

    header = ""
    if layout.header_row:
        bottom = (layout.numbering_row - 1) if layout.numbering_row else layout.header_row
        header = " | ".join(header_texts(ws, layout.header_row, bottom).values())
    form6 = "форма n 6" in joined or "ресурсный сметный расчет" in joined
    has_price = any(w in header for w in _PRICE_WORDS) or (form6 and layout.price_col is not None)
    if forced is None:
        if "дефектн" in joined or "дефектн" in _low(ws.title):
            return _skipped(file, file_name, ws, KIND_ACT, "дефект акти — ишлар рўйхати, ресурслар эмас", hidden, headers)
        if has_price:
            if not any(w in header for w in _QTY_WORDS):
                return _skipped(file, file_name, ws, KIND_CATALOG,
                                "нарх каталоги — миқдор устуни йўқ, смета эмас (жуфтлашга олинмайди)", hidden, headers)
            if _dotted_share(ws, 1, layout.data_start, layout.data_stop) >= DOTTED_SHARE:
                return _skipped(file, file_name, ws, KIND_PRICED,
                                "нархланган локал смета (иш ва унинг ресурслари «1.1, 1.2…», нарх ёзилган) — тайёр. "
                                "Қайта нархлаш керак бўлса — турини «Локал» қилинг", hidden, headers)
    role = forced or (RESOURCE if has_price else LOCAL)
    found = {"header": layout.header_row}
    if role == RESOURCE:
        reason = "нарх устуни бор («цена / стоимость»)"
        if "форма n 6" in joined or "сметный расчет" in joined or "ресурсная смета" in joined:
            reason += " · Форма 6а"
        if layout.price_col is None:
            reason += " · нарх устуни топилмади — кўрсатинг"
        name_col, unit_col, price_col = layout.name_col, layout.unit_col, layout.price_col
        start, stop = layout.data_start, layout.data_stop
        found["price"] = price_col
    else:
        reason = "ресурслар рўйхати, нарх устуни йўқ"
        if "форма n 5" in joined or "ведомост" in joined:
            reason += " · Форма 5"
        loc = detect_local_columns(ws, cols)
        if loc is not None:
            name_col, unit_col, start, stop = loc.name, loc.unit, loc.data_start, loc.data_stop
            found.update(qty_unit=loc.qty_unit, qty_total=loc.qty_total)
        else:
            loc = override_layout(ws, detect_layout(ws) or layout, cols)
            name_col, unit_col, start, stop = loc.name_col, loc.unit_col, loc.data_start, loc.data_stop
            reason += " · миқдор устуни топилмади — кўрсатинг"
        price_col = None
    found.update(name=name_col, unit=unit_col, start=start, end=stop - 1)
    if forced:
        reason = "қўлда белгиланди · " + reason
    if cols:
        reason += " · устунлар қўлда кўрсатилган"
    if hidden:
        reason += " · яширин лист"

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
        part=part, rows=len(rows_for_sig), signature=signature, hidden=hidden, names=frozenset(names),
        columns=found, manual=bool(cols), headers=headers,
    )


def inspect_workbook(wb, file: int = 0, file_name: str = "", roles: Optional[dict] = None,
                     columns: Optional[dict] = None) -> List[SheetInfo]:
    """``roles`` / ``columns`` — smetachining qo'lda tuzatishlari, kaliti «fayl:list» (``SheetInfo.key``)."""
    roles, columns = roles or {}, columns or {}
    return [inspect_sheet(ws, file, file_name, roles.get(f"{file}:{ws.title}"), columns.get(f"{file}:{ws.title}"))
            for ws in wb.worksheets]


# ===================== nusxalar va juftlash =====================
def mark_duplicates(infos: Iterable[SheetInfo]) -> List[SheetInfo]:
    """Bir xil turdagi, mazmuni aynan bir xil listlar (masalan, ikki faylda takrorlangan) — birinchisi qoladi.
    Ko'rinadigan list yashirinidan oldin: smetachi asl listni yashirib, nusxasi bilan ishlaydi."""
    seen: Dict[Tuple[str, str], str] = {}
    for info in sorted(infos, key=lambda i: i.hidden):
        if info.role in (LOCAL, RESOURCE) and info.rows:
            k = (info.role, info.signature)
            if k in seen:
                info.duplicate_of = seen[k]
            else:
                seen[k] = info.key
    return list(infos)


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
    if res.hidden and not loc.hidden:
        score -= 0.5  # yashirin resurs — ko'rinadigan nusxasi bo'lsa, o'shanisi afzal
    return score, overlap, reasons


def pair_label(loc: SheetInfo, file_stem: str = "") -> str:
    """``file_stem`` — faylda yagona lokal bo'lsa uning nomi («2. ЭО»): «ЛОКАЛЬНО-РЕСУРСНАЯ ВЕДОМОСТЬ»dan aniqroq."""
    base = loc.part or loc.obj or file_stem or loc.title or loc.sheet
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
        alone = sum(1 for x in locals_ if x.file == loc.file) == 1
        p.label = pair_label(loc, re.sub(r"\.(xlsx|xlsm|xls)$", "", loc.file_name, flags=re.I).strip() if alone else "")
        p.options = pair_options(loc, resources)
        pairs.append(p)
    unused = [r for r in resources if r.key not in {p.res for p in pairs}]
    return pairs, unused


def pair_options(loc: SheetInfo, resources: List[SheetInfo]) -> List[dict]:
    """Smetachi qo'lda tanlashi uchun barcha resurs listlari — moslik bo'yicha (eng yaxshisi birinchi)."""
    out = []
    for res in resources:
        score, overlap, reasons = pair_score(loc, res)
        out.append({"res": res.key, "score": round(score, 2), "overlap": round(overlap, 4),
                    "same_file": res.file == loc.file, "reasons": reasons[:3]})
    out.sort(key=lambda o: (-o["score"], -o["overlap"]))
    return out


def apply_roles(infos: List[SheetInfo], roles: Dict[str, str]) -> List[SheetInfo]:
    """Smetachi list turini qo'lda o'zgartirsa (``{kalit: rol}``)."""
    for info in infos:
        role = roles.get(info.key)
        if role in ROLES and role != info.role:
            info.role = role
            info.reason = "қўлда белгиланди"
    return infos

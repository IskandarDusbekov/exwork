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
from django.utils.translation import gettext_noop as _noop

LOCAL = "local"
RESOURCE = "resource"
UNKNOWN = "unknown"
ROLES = (LOCAL, RESOURCE, UNKNOWN)

TITLE_SCAN_ROWS = 14
_PRICE_WORDS = ("цена", "стоимост", "базис", "нарх", "баҳо", "бахо", "narx", "baho")  # i18n: ok
_QTY_WORDS = ("колич", "кол-во", "кол.", "объем", "объём", "потреб", "миқдор", "микдор")  # i18n: ok
DOTTED_SHARE = 0.3  # 1-ustunda «1.1, 2.3» ulushi shundan ko'p — ish + resurslari (ЛРВ), resurs ro'yxati emas
DOTTED_SCAN = 300

# Nega o'tkazildi / nima ekan (``SheetInfo.kind``)
KIND_CATALOG = "catalog"
KIND_PRICED = "priced"
KIND_ACT = "act"
KIND_SUMMARY = "summary"
KIND_EMPTY = "empty"
KIND_LABELS = {
    KIND_CATALOG: _noop("нарх каталоги"),
    KIND_PRICED: _noop("нархланган смета"),
    KIND_ACT: _noop("дефект акти"),
    KIND_SUMMARY: _noop("сводка / умумий ҳисоб"),
    KIND_EMPTY: _noop("бўш лист"),
}
_SUMMARY_WORDS = ("свод", "объектн", "рекомендуем", "стартов", "общий расчет", "общий расчёт", "якуний", "ҳаражат",  # i18n: ok
                  "харажат", "обложк", "титул", "пояснительн", "расчет стоимости", "итоговый")  # i18n: ok
_NUMBER_RE = re.compile(r"№\s*([0-9A-Za-zА-Яа-яЁё][\w\-./]*)")  # i18n: ok
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
    # тузилма изи (номларга боғлиқ эмас, ``sheet_fingerprint``): смета­чи созламасини эслаб қолиш ва қўллаш учун
    fp: str = ""      # аниқ: устун кенгликлари + тепадаги бирлашган катаклар
    fpl: str = ""     # кенг: фақат устун кенгликлари
    anchor: int = 0   # «1 2 3 4…» рақамлаш қатори (0 — йўқ): эслаб қолинган биринчи қатор шунга нисбатан
    memo: str = ""    # эслаб қолинган созлама қўлланди: "fp" (аниқ) / "fpl" (кенг); "" — йўқ
    recall: dict = field(default_factory=dict)  # шу эслатмадан олинган устунлар (нархлашга ҳам шулар узатилади)
    pending: bool = False  # «Қўлда» усули: файл ҳали таҳлил қилинмаган (смета­чи белгилагач ёки «Автоматик ажратиш»да)
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
    return re.sub(r"\s+", " ", s.lower().replace("ё", "е")).strip()  # i18n: ok


def _clean(s: str) -> str:
    """«на , МОСТ НА ПК 75+34» -> «МОСТ НА ПК 75+34»."""
    s = re.sub(r"^\s*(на|по)\b[\s,.:]*", "", s.strip(), flags=re.I)  # i18n: ok
    return s.strip(" ,.;:")


def _labelled(head: Dict[int, str], low: Dict[int, str], prefix: str, label: str) -> str:
    """«НАИМЕНОВАНИЕ ОБЪЕКТА: …» yoki «(наименование … объекта)» yorlig'idan oldingi qator."""
    for r, t in low.items():
        if t.startswith(prefix):
            return _clean(head[r].split(":", 1)[1] if ":" in head[r] else "")
    for r, t in low.items():
        if t.startswith("(") and label in t:
            prev = [p for p in range(r - 1, 0, -1) if head.get(p) and not low[p].startswith(("(", "форма"))]  # i18n: ok
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
                             _noop("қўлда белгиланди — «Наименование» ва «Ед. изм.» устунлари топилмади, уларни кўрсатинг"),
                             hidden=hidden, manual=bool(cols), headers=headers)
        if not head:
            return _skipped(file, file_name, ws, KIND_EMPTY, _noop("бўш лист"), hidden, headers)
        if any(w in joined or w in _low(ws.title) for w in _SUMMARY_WORDS):
            return _skipped(file, file_name, ws, KIND_SUMMARY, _noop("сводка / умумий ҳисоб — смета эмас, нархланмайди"), hidden, headers)
        return SheetInfo(file, file_name, ws.title, UNKNOWN,
                         _noop("«Наименование» ва «Ед. изм.» сарлавҳали жадвал топилмади"), hidden=hidden, headers=headers)

    header = ""
    if layout.header_row:
        bottom = (layout.numbering_row - 1) if layout.numbering_row else layout.header_row
        header = " | ".join(header_texts(ws, layout.header_row, bottom).values())
    form6 = "форма n 6" in joined or "ресурсный сметный расчет" in joined  # i18n: ok
    has_price = any(w in header for w in _PRICE_WORDS) or (form6 and layout.price_col is not None)
    if forced is None:
        if "дефектн" in joined or "дефектн" in _low(ws.title):  # i18n: ok
            return _skipped(file, file_name, ws, KIND_ACT, _noop("дефект акти — ишлар рўйхати, ресурслар эмас"), hidden, headers)
        if has_price:
            # миқдор устуни йўқ — нарх каталоги бўлиши мумкин. Лекин баъзи дастурларнинг RES'ида (ABC «vr_…»)
            # миқдор сарлавҳаси бошқача: лист ресурс сифатида ўқилади ва «каталог» белгиси билан қолади —
            # ``promote_catalogs`` локал ресурслари шу ерда топилса, уни ресурс қилади
            catalog = not any(w in header for w in _QTY_WORDS)
            if not catalog and _dotted_share(ws, 1, layout.data_start, layout.data_stop) >= DOTTED_SHARE:
                return _skipped(file, file_name, ws, KIND_PRICED,
                                _noop("нархланган локал смета (иш ва унинг ресурслари «1.1, 1.2…», нарх ёзилган) — тайёр. "
                                "Қайта нархлаш керак бўлса — турини «Локал» қилинг"), hidden, headers)
    catalog = forced is None and has_price and not any(w in header for w in _QTY_WORDS)
    role = forced or (RESOURCE if has_price else LOCAL)
    found = {"header": layout.header_row}
    if role == RESOURCE:
        reason = _noop("нарх устуни бор («цена / стоимость»)")
        if "форма n 6" in joined or "сметный расчет" in joined or "ресурсная смета" in joined:  # i18n: ok
            reason += _noop(" · Форма 6а")
        if layout.price_col is None:
            reason += _noop(" · нарх устуни топилмади — кўрсатинг")
        name_col, unit_col, price_col = layout.name_col, layout.unit_col, layout.price_col
        start, stop = layout.data_start, layout.data_stop
        found["price"] = price_col
    else:
        reason = _noop("ресурслар рўйхати, нарх устуни йўқ")
        if "форма n 5" in joined or "ведомост" in joined:  # i18n: ok
            reason += _noop(" · Форма 5")
        loc = detect_local_columns(ws, cols)
        if loc is not None:
            name_col, unit_col, start, stop = loc.name, loc.unit, loc.data_start, loc.data_stop
            found.update(qty_unit=loc.qty_unit, qty_total=loc.qty_total)
        else:
            loc = override_layout(ws, detect_layout(ws) or layout, cols)
            name_col, unit_col, start, stop = loc.name_col, loc.unit_col, loc.data_start, loc.data_stop
            reason += _noop(" · миқдор устуни топилмади — кўрсатинг")
        price_col = None
    found.update(name=name_col, unit=unit_col, start=start, end=stop - 1)
    if forced:
        reason = _noop("қўлда белгиланди · ") + reason
    if cols:
        reason += _noop(" · устунлар қўлда кўрсатилган")
    if hidden:
        reason += _noop(" · яширин лист")

    title_row = next((r for r, t in low.items()
                      if "ведомост" in t or "сметный расчет" in t or "ресурсная смета" in t), None)  # i18n: ok
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
    if catalog:
        role, reason = UNKNOWN, _noop("нарх каталоги — миқдор устуни йўқ, смета эмас (жуфтлашга олинмайди)")

    return SheetInfo(
        file, file_name, ws.title, role, reason, title=title, number=number, kind=KIND_CATALOG if catalog else "",
        stroyka=_labelled(head, low, "наименование стройки", "наименование стройки"),  # i18n: ok
        obj=_labelled(head, low, "наименование объекта", "наименование объекта"),  # i18n: ok
        part=part, rows=len(rows_for_sig), signature=signature, hidden=hidden, names=frozenset(names),
        columns=found, manual=bool(cols), headers=headers,
    )


def inspect_workbook(wb, file: int = 0, file_name: str = "", roles: Optional[dict] = None,
                     columns: Optional[dict] = None, recall=None) -> List[SheetInfo]:
    """``roles`` / ``columns`` — smetachining qo'lda tuzatishlari, kaliti «fayl:list» (``SheetInfo.key``).
    ``recall(fp, fpl)`` — смета­чи аввал шундай тузилмали листда кўрсатгани: ``(эслатма, "fp" | "fpl")`` ёки ``None``
    (эслатма: ``{"role": …, "columns": {…}}``, ``memo_payload`` қайтаргани)."""
    roles, columns = roles or {}, columns or {}
    out = []
    for ws in wb.worksheets:
        key = f"{file}:{ws.title}"
        fp, fpl = sheet_fingerprint(ws)
        anchor = numbering_anchor(ws)
        role, cols = roles.get(key), columns.get(key)
        info = None
        hit = recall(fp, fpl) if recall and role is None and not cols and (fp or fpl) else None
        if hit:
            memo, how = hit
            auto = inspect_sheet(ws, file, file_name)
            # кенг из (фақат кенгликлар) бошқа шаблонга ҳам тўғри келиши мумкин — фақат тизим ўзи эплолмаганда
            if how == "fp" or needs_help(auto):
                info = _recalled(ws, file, file_name, memo, anchor)
                info.memo = how
            else:
                info = auto
        if info is None:
            info = inspect_sheet(ws, file, file_name, role, cols)
        info.fp, info.fpl, info.anchor = fp, fpl, anchor
        out.append(info)
    return out


# ===================== смета­чи созламасини эслаб қолиш (номларга эмас — тузилмага боғланади) =====================
FP_COLS = 16    # A..P устунлар кенглиги
FP_ROWS = 25    # тепадаги бирлашган катаклар (сарлавҳа, «шапка»)
ANCHOR_ROWS = 40


def sheet_fingerprint(ws) -> Tuple[str, str]:
    """Листнинг «шаблон» изи: бир дастурдан чиққан листларда устун кенгликлари ва шапкадаги бирлашган катаклар
    бир хил, матн (ном, объект, хатолар) эса ҳар хил. ``(аниқ, кенг)``; кенгликлар йўқ бўлса — ``("", "")``.
    2626 та ҳақиқий файлда: аниқ изда бир изга икки хил тузилма тушмади, кенг изда — 443 листдан 5 таси."""
    from openpyxl.utils import get_column_letter
    widths = []
    for c in range(1, FP_COLS + 1):
        dim = ws.column_dimensions.get(get_column_letter(c))
        widths.append(int(round(dim.width)) if dim is not None and dim.width else 0)
    if not any(widths):
        return "", ""
    merges = sorted(str(m) for m in ws.merged_cells.ranges if m.min_row <= FP_ROWS)
    loose = hashlib.sha1(repr(widths).encode()).hexdigest()[:20]
    strict = hashlib.sha1(repr((widths, merges)).encode()).hexdigest()[:20]
    return strict, loose


def numbering_anchor(ws) -> int:
    """«1 2 3 4…» рақамлаш қатори (тепадан ``ANCHOR_ROWS`` қаторда); йўқ — 0."""
    from .detect import _is_numbering_row
    return next((r for r in range(1, min(ws.max_row, ANCHOR_ROWS) + 1) if _is_numbering_row(ws, r)), 0)


def needs_help(info: SheetInfo) -> bool:
    """Тизим листни ўзи тўлиқ тушунмади: жадвал топилмади, керакли устун йўқ ёки маълумот қатори йўқ."""
    if info.role == UNKNOWN:
        return not info.kind  # «жадвал топилмади» (сводка, бўш лист, каталог — тушунилган)
    cols = info.columns or {}
    need = ("name", "unit", "price") if info.role == RESOURCE else ("name", "unit", "qty_total")
    return any(not cols.get(k) for k in need) or not info.rows


def memo_payload(role: str, cols: Optional[dict], anchor: int) -> dict:
    """Нимани эслаб қоламиз: тури ва устунлар; биринчи қатор — рақамлаш қаторига нисбатан (бошқа файлда шапка
    узунроқ бўлса ҳам тўғри тушади). Охирги қатор эслаб қолинмайди — у ҳар файлда бошқа («Итого» ўзи топилади)."""
    from .detect import COLUMN_FIELDS
    cols = cols or {}
    out = {k: int(cols[k]) for k in COLUMN_FIELDS if cols.get(k)}
    if cols.get("start"):
        if anchor and cols["start"] > anchor:
            out["start_off"] = int(cols["start"]) - anchor
        else:
            out["start"] = int(cols["start"])
    return {"role": role if role in ROLES else "", "columns": out}


def _recalled(ws, file: int, file_name: str, memo: dict, anchor: int) -> SheetInfo:
    from .detect import COLUMN_FIELDS
    saved = memo.get("columns") or {}
    cols = {k: v for k, v in saved.items() if k in COLUMN_FIELDS and v}
    if saved.get("start_off") and anchor:
        cols["start"] = anchor + saved["start_off"]
    elif saved.get("start"):
        cols["start"] = saved["start"]
    role = memo.get("role") or None
    if role == UNKNOWN:
        info = inspect_sheet(ws, file, file_name)
        info.role, info.reason = UNKNOWN, _noop("ўтказилди — аввалги созламангиз бўйича")
        return info
    info = inspect_sheet(ws, file, file_name, role, cols or None)
    info.reason = info.reason.replace(_noop("қўлда белгиланди · "), "") + _noop(" · аввалги созламангиз қўлланди")
    info.recall = cols
    info.manual = False  # смета­чи бу листда ўзи кўрсатмаган — «қўлда» эмас, «эслаб қолинган»
    return info


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
    reasons = [_noop("ресурслар мослиги {0:.0%}").format(overlap)]
    if loc.number and loc.number == res.number:
        score += 2
        reasons.append(_noop("смета № {0}").format(loc.number))
    elif loc.number and res.number:
        score -= 4  # иккаласида ҳам рақам бор ва улар бошқа — кучли қарши белги
        reasons.append(_noop("смета рақами бошқа: № {0} / № {1}").format(loc.number, res.number))
    if _prefix(loc.sheet) and _prefix(loc.sheet) == _prefix(res.sheet):
        score += 3
        reasons.append(_noop("лист номи «{0}…»").format(_prefix(loc.sheet)))
    if _same(loc.obj, res.obj):
        score += 1.5
        reasons.append(_noop("объект номи бир хил"))
    if _same(loc.part, res.part):
        score += 1
        reasons.append(_noop("қисм номи бир хил"))
    if loc.file == res.file:
        score += 0.3
    if res.hidden and not loc.hidden:
        score -= 0.5  # yashirin resurs — ko'rinadigan nusxasi bo'lsa, o'shanisi afzal
    return score, overlap, reasons


STRUCT_SCORE = 5.0  # номсиз жуфтлаш: лист номи (+3) ва смета рақами (+2) бир хил бўлиши шарт


def _structural(loc: SheetInfo, res: SheetInfo, struct: float, conflict: bool) -> bool:
    """Номлар мослигисиз ҳам ишончли жуфт: битта файлда, рақамлари зид эмас, лист номи ва рақами бир хил."""
    return loc.file == res.file and not conflict and struct >= STRUCT_SCORE


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
            conflict = bool(loc.number and res.number and loc.number != res.number)
            if overlap >= MIN_OVERLAP and score >= MIN_SCORE:
                cands.append((score, li, res, overlap, reasons, conflict))
            elif _structural(loc, res, score - overlap * 10, conflict):
                # ресурс номлари кам мос (смета­чи RES'да номни ўзгартирган: «инвертор 4 квт» → «20 квт»), лекин
                # тузилма аниқ кўрсатади: битта файл, лист номи ва смета рақами бир хил — номга боғланмаймиз
                cands.append((score, li, res, overlap,
                              reasons + [_noop("номлар кам мос — файл, лист ва смета рақами бўйича жуфтланди, текширинг")],
                              conflict))
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
                              reasons + [_noop("бу ресурс бошқа локал билан умумий")], shared=True)

    pairs = []
    for li, loc in enumerate(locals_):
        p = chosen.get(li) or Pair(loc.key, None, reasons=[_noop("мос ресурс листи топилмади")])
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
        conflict = bool(loc.number and res.number and loc.number != res.number)
        out.append({"res": res.key, "score": round(score, 2), "overlap": round(overlap, 4),
                    "same_file": res.file == loc.file, "reasons": reasons[:3],
                    # номлар кам мос, лекин тузилма аниқ: битта файл, лист номи ва смета рақами бир хил
                    "struct": overlap < MIN_OVERLAP and _structural(loc, res, score - overlap * 10, conflict)})
    out.sort(key=lambda o: (-o["score"], -o["overlap"]))
    return out


PROMOTE_OVERLAP = 0.3  # каталогга ўхшаган лист: локал ресурсларининг шунчаси унда бўлса — ресурс


def promote_catalogs(infos: List[SheetInfo], roles: Optional[Dict[str, str]] = None) -> List[SheetInfo]:
    """«Нарх каталоги» деб ўтказилган, лекин ичида локал ресурслари аниқ бор лист — бу RES (миқдор сарлавҳаси
    бошқача ёзилган, масалан ABC дастурининг «vr_…» листи). Смета­чи қўлда белгилаганига тегилмайди."""
    roles = roles or {}
    locals_ = [i for i in infos if i.role == LOCAL and i.names]
    for info in infos:
        if info.role != UNKNOWN or info.kind != KIND_CATALOG or not info.names or info.key in roles:
            continue
        best = max((len(loc.names & info.names) / max(1, len(loc.names)) for loc in locals_), default=0.0)
        if best >= PROMOTE_OVERLAP:
            info.role, info.kind = RESOURCE, ""
            info.reason = (_noop("нарх устуни бор, миқдор сарлавҳаси топилмади — лекин локал ресурсларининг {0:.0%} шу ерда, ресурс деб олинди").format(best))
    return infos


def apply_roles(infos: List[SheetInfo], roles: Dict[str, str]) -> List[SheetInfo]:
    """Smetachi list turini qo'lda o'zgartirsa (``{kalit: rol}``)."""
    for info in infos:
        role = roles.get(info.key)
        if role in ROLES and role != info.role:
            info.role = role
            info.reason = _noop("қўлда белгиланди")
    return infos

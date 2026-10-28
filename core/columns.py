"""Lokal smeta ustunlarini aniqlash va standart ko'rinishga keltirish.

Lokal (Форма 5, ЛРВ) har xil tuziladi: «Количество» bitta yoki ikkita ustunda, birlashgan kataklarda,
sarlavha har xil yozilgan («Кол-во», «КОЛИЧЕСТВО», «по проекту», «Всего», «Потребность»…) yoki umuman yo'q.
Narxlash esa standart joylarni kutadi: E — birlikka miqdor, F — jami miqdor, G — narx, H — summa.

``detect_local_columns`` — ustunlar vazifasini ikki yo'l bilan topadi:

* **sarlavha** — matn kichik harfga, «ё»→«е», qisqartmalar bir xil ko'rinishga keltiriladi;
* **mazmun** — sarlavha bo'lmasa yoki u noaniq bo'lsa: nom ustuni — eng uzun matnlar, birlik — qisqa
  «чел-ч», «м3», «т»…, miqdorlar — sonli ustunlar. Jami miqdor ustuni shundan taniladi: bitta ish ichidagi
  resurslarda «jami / birlikka» nisbati o'zgarmas (= ishning hajmi, masalan 4,13 / 118 = 0,035).

``prepare_local`` — miqdorlarni E/F ga ko'chiradi (formula emas, Excel ko'rsatgan qiymat) va G/H ni
narx/summa uchun bo'shatadi. Ko'chirib bo'lmasa — ``LayoutError`` (smetachiga aniq sabab bilan).
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from openpyxl.cell.cell import MergedCell

from .detect import Layout, _total_row, cell_value, detect_layout, header_texts, override_layout

QTY_UNIT_COL, QTY_TOTAL_COL, PRICE_COL, AMOUNT_COL = 5, 6, 7, 8  # E, F, G, H
SCAN_COLS = 14  # birlikdan keyin nechta ustun ko'riladi
CONTENT_SCAN_ROWS = 400


class LayoutError(ValueError):
    """Lokal jadval ustunlarini xavfsiz joylab bo'lmadi — narxlanmaydi (ma'lumot buzilmasin)."""


@dataclass
class LocalColumns:
    name: int
    unit: int
    qty_unit: Optional[int]
    qty_total: Optional[int]
    data_start: int
    data_stop: int
    header_row: Optional[int] = None
    numbering_row: Optional[int] = None
    money: List[int] = field(default_factory=list)  # eski narx/summa ustunlari (ustidan yoziladi)
    source: str = "header"  # header | content | manual (smetachi ko'rsatgan)
    spans: Dict[int, int] = field(default_factory=dict)  # resurs qatorlaridagi birlashmalar: anker -> oxirgi ustun
    notes: List[str] = field(default_factory=list)

    def as_layout(self) -> Layout:
        return Layout(self.header_row, self.numbering_row, self.data_start, self.data_stop, self.name, self.unit)


# ===================== qiymatlar =====================
_NUM_TEXT = re.compile(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def num(v) -> Optional[float]:
    """Son yoki sonli matn («94,8838», «1 234,5») -> float; boshqasi None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.strip().replace(" ", "").replace(" ", "").replace(",", ".")
        if _NUM_TEXT.fullmatch(t):
            return float(t)
    return None


def _text(v) -> str:
    return "" if v is None or num(v) is not None else str(v).strip()


def _hnorm(t: str) -> str:
    """Sarlavha matni: kichik harf, «ё»→«е», «обьем»→«объем», tinish belgilari bo'shliq."""
    t = (t or "").lower().replace("ё", "е").replace("ъ", "ь").replace("­", "")
    t = re.sub(r"[._\-–—/\\:;,()\"'«»]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_MONEY = re.compile(r"стоим|цен|сумм|руб|\bсум\b|расценк|затраты всего|итого затрат")
_PER_UNIT = re.compile(r"\bна\s?(?:1\s)?ед|\bна един|\bединиц[ыу]\b(?! изм)|\bнорм|\bрасход на\b|\bна измерит")
_TOTAL = re.compile(r"проект|\bвсего\b|\bобщ|\bитог|\bпотреб|\bна объ?ем|\bфакт|\bнеобход")
_QTY = re.compile(r"кол|\bобьем|\bобъем|\bрасход|\bпотреб|\bнорм|\bвыход|\bмиқдор|\bмикдор|\bсони\b")
_UNIT_WORD = re.compile(
    r"^(?:\d+\s?)?(?:чел|маш|м\d?|m\d?|т|кг|г|шт|компл|комплект|км|л|п\s?м|пог|га|кв|квт|квт ч|мвт|ч|час|"
    r"смен|сут|мес|ед|точк|узел|прибор|кан|набор|рулон|лист|тн|тонн|литр|место|ступ|проем|отв|%)\b")


def header_role(text: str) -> Optional[str]:
    """Bitta ustun sarlavhasi -> qty_unit | qty_total | qty | price | amount | None."""
    t = _hnorm(text)
    if not t:
        return None
    if _MONEY.search(t):
        if re.search(r"сумм|\bобщ|\bвсего|\bитог", t) and not re.search(r"\bцен", t):
            return "amount"
        return "price" if (_PER_UNIT.search(t) or re.search(r"\bцен", t)) else "amount"
    if _PER_UNIT.search(t):
        return "qty_unit"
    if _TOTAL.search(t):
        return "qty_total" if (_QTY.search(t) or "проект" in t or "потреб" in t or "всего" in t) else None
    if _QTY.search(t):
        return "qty"
    return None


def _is_unit(v) -> bool:
    t = _hnorm(_text(v))
    return 0 < len(t) <= 22 and bool(_UNIT_WORD.match(t))


# ===================== aniqlash =====================
def _spans(ws, rows: List[int], min_col: int) -> Dict[int, int]:
    """Resurs qatorlarining ko'pchiligida takrorlanadigan gorizontal birlashma (E:F, G:H…): anker -> oxirgi ustun.
    Faqat ish sarlavhalarida (hajm E:F da) bo'lgan birlashmalar hisobga olinmaydi."""
    on = set(rows)
    seen: Dict[Tuple[int, int], int] = {}
    for m in ws.merged_cells.ranges:
        if m.min_row in on and m.min_col >= min_col and m.max_col > m.min_col:
            seen[(m.min_col, m.max_col)] = seen.get((m.min_col, m.max_col), 0) + 1
    out: Dict[int, int] = {}
    for (a, b), n in sorted(seen.items(), key=lambda x: -x[1]):
        if a not in out and n >= max(3, len(rows) // 2):
            out[a] = b
    return out


def _resource_rows(ws, start: int, stop: int, name: int, unit: int) -> List[int]:
    return [r for r in range(start, min(stop, start + CONTENT_SCAN_ROWS)) if _text(cell_value(ws, r, name)) and _text(cell_value(ws, r, unit))]


def _ratio_score(ws, rows: List[int], a: int, b: int) -> Tuple[float, int]:
    """Jami/birlikka juftligi: ketma-ket resurs qatorlarida b/a o'zgarmas (bitta ish ichida). (ulush, guruhlar)."""
    groups, cur, prev = [], [], None
    for r in rows:
        va, vb = num(cell_value(ws, r, a)), num(cell_value(ws, r, b))
        if prev is not None and r != prev + 1:
            groups.append(cur); cur = []
        prev = r
        if va and vb:
            cur.append(vb / va)
        else:
            groups.append(cur); cur = []
    groups.append(cur)
    groups = [g for g in groups if len(g) >= 2]
    if not groups:
        return 0.0, 0
    def steady(g):  # ко'пчилиги (70%) медианадан 3% дан узоқ эмас — битта-иккита бошқа қатор халал бермайди
        mid = sorted(g)[len(g) // 2]
        return mid > 0 and sum(1 for x in g if abs(x / mid - 1) <= 0.03) >= 0.7 * len(g)
    ok = sum(1 for g in groups if steady(g))
    return ok / len(groups), len(groups)


def _fill(ws, rows: List[int], c: int) -> float:
    return sum(1 for r in rows if num(cell_value(ws, r, c)) is not None) / len(rows) if rows else 0.0


def _guess_name_unit(ws) -> Optional[Tuple[int, int, int]]:
    """Sarlavhasiz: nom — eng uzun matnli ustun, birlik — undan o'ngdagi qisqa «чел-ч», «м3»… (nom, birlik, boshi)."""
    last = min(ws.max_row, CONTENT_SCAN_ROWS)
    best = None
    for c in range(1, min(ws.max_column, 8) + 1):
        texts = [_text(cell_value(ws, r, c)) for r in range(1, last + 1)]
        long_ = [t for t in texts if len(t) >= 12]
        if len(long_) >= 5:
            avg = sum(map(len, long_)) / len(long_)
            if best is None or len(long_) * avg > best[0]:
                best = (len(long_) * avg, c)
    if best is None:
        return None
    name = best[1]
    unit, unit_hits = None, 0
    for c in range(name + 1, min(ws.max_column, name + 3) + 1):
        hits = sum(1 for r in range(1, last + 1) if _is_unit(cell_value(ws, r, c)))
        if hits > unit_hits:
            unit, unit_hits = c, hits
    if unit is None or unit_hits < 5:
        return None
    start = next((r for r in range(1, last + 1) if _text(cell_value(ws, r, name)) and _is_unit(cell_value(ws, r, unit))), None)
    return (name, unit, start) if start else None


def detect_local_columns(ws, override: Optional[dict] = None) -> Optional[LocalColumns]:
    """Ustunlar vazifasi (qiymatlar varag'i — ``data_only=True`` — bo'yicha). Topilmasa ``None``.
    ``override`` — smetachi qo'lda ko'rsatgan ustun/qatorlar (``detect.override_layout``), avtomatikdan ustun."""
    override = override or {}
    layout = override_layout(ws, detect_layout(ws), override)
    if layout is not None:
        cols = LocalColumns(layout.name_col, layout.unit_col, None, None, layout.data_start, layout.data_stop,
                            layout.header_row, layout.numbering_row, source="manual" if override else "header")
        if layout.header_row:
            bottom = (layout.numbering_row - 1) if layout.numbering_row else layout.header_row
            texts = header_texts(ws, layout.header_row, bottom)
        else:
            texts = {}
    else:
        g = _guess_name_unit(ws)
        if g is None:
            return None
        name, unit, start = g
        stop = _total_row(ws, start, max(4, name))
        cols = LocalColumns(name, unit, None, None, start, stop, source="content",
                            notes=["Жадвал сарлавҳаси топилмади — устунлар мазмунидан аниқланди"])
        texts = {}

    rows = _resource_rows(ws, cols.data_start, cols.data_stop, cols.name, cols.unit)
    first = max(cols.name, cols.unit) + 1
    # birlashma ustunni «yopadi» faqat u ustunda o'zi hech qachon son bo'lmasa (masalan «1. НВ В2» da F);
    # ish sarlavhalaridagina E:F birlashgan, resurs qatorlarida F to'la bo'lsa — F mustaqil ustun
    spans = {a: b for a, b in _spans(ws, rows, first).items()
             if all(_fill(ws, rows, c) < 0.05 for c in range(a + 1, b + 1))}
    cols.spans = spans
    covered = {c for a, b in spans.items() for c in range(a + 1, b + 1)}
    cands = [c for c in range(first, min(ws.max_column, first + SCAN_COLS) + 1) if c not in covered]
    roles = {c: header_role(texts.get(c, "")) for c in cands}
    fills = {c: _fill(ws, rows, c) for c in cands}
    numeric = [c for c in cands if fills[c] >= 0.25 and roles[c] not in ("price", "amount")]

    # 1) sarlavha aytgan juftlik — mazmun bilan tekshiriladi
    hu = next((c for c in cands if roles[c] == "qty_unit"), None)
    ht = next((c for c in cands if roles[c] == "qty_total" and c != hu), None)
    if hu and ht and fills[ht] >= 0.2:
        cols.qty_unit, cols.qty_total = hu, ht
    # 2) mazmun: «jami/birlikka» nisbati ish ichida o'zgarmas juftlik
    if cols.qty_total is None or (cols.qty_unit and _ratio_score(ws, rows, cols.qty_unit, cols.qty_total)[0] < 0.5):
        best = None
        for i, a in enumerate(numeric):
            for b in numeric[i + 1:i + 3]:
                score, n = _ratio_score(ws, rows, a, b)
                if n >= 1 and score >= 0.6 and (best is None or (score, n) > best[0]):
                    best = ((score, n), a, b)
        if best:
            if (cols.qty_unit, cols.qty_total) != (best[1], best[2]) and cols.qty_total is not None:
                cols.notes.append("Сарлавҳа бўйича миқдор устунлари мазмунга тўғри келмади — мазмун бўйича олинди")
            cols.qty_unit, cols.qty_total = best[1], best[2]
    # 3) битта миқдор устуни (жами)
    if cols.qty_total is None:
        single = ([c for c in cands if roles[c] in ("qty_total", "qty") and fills[c] >= 0.2]
                  or [c for c in cands if roles[c] == "qty_unit" and fills[c] >= 0.2] or numeric[:1])
        if single:
            cols.qty_total = single[0]
            cols.qty_unit = None
    if override.get("qty_total"):
        cols.qty_total, cols.qty_unit = override["qty_total"], override.get("qty_unit") or None
    if cols.qty_total is None:
        return None
    if cols.source == "content":
        cols.notes.append(f"Миқдор: жами — {_letter(cols.qty_total)}" +
                          (f", бирликка — {_letter(cols.qty_unit)}" if cols.qty_unit else ""))
    cols.money = [c for c in cands if roles[c] in ("price", "amount")]
    return cols


def _letter(c: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(c)


# ===================== standart ko'rinishga keltirish =====================
def prepare_local(ws, ws_vals, cols: LocalColumns) -> List[str]:
    """Miqdorlarni E (birlikka) / F (jami) ga ko'chiradi, G/H — narx/summa uchun. ``ws`` (formulalar) va
    ``ws_vals`` (qiymatlar) ikkalasi bir xil o'zgaradi. Qaytaradi: smetachi uchun izohlar."""
    notes = list(cols.notes)
    if cols.name >= QTY_UNIT_COL or cols.unit >= QTY_UNIT_COL:
        raise LayoutError(f"Номи {_letter(cols.name)}, бирлиги {_letter(cols.unit)} устунида — E/F/G/H га миқдор ва нарх "
                          "жойлаб бўлмайди. Жадвални стандарт кўринишга келтириб юкланг (C — номи, D — бирлиги)")
    src = {QTY_UNIT_COL: cols.qty_unit, QTY_TOTAL_COL: cols.qty_total}
    top = cols.header_row or cols.data_start
    merged_qty = [m for m in _qty_merges(ws, top, cols) if cols.spans.get(m.min_col) == m.max_col]
    if src[QTY_UNIT_COL] in (QTY_UNIT_COL, None) and src[QTY_TOTAL_COL] == QTY_TOTAL_COL and not merged_qty:
        return notes  # allaqachon standart

    rows = range(top, cols.data_stop)  # «Итого…» ва ундан пасти ўзгармайди
    moving = {c for c in src.values() if c}
    # E..H да бошқа (номаълум) маълумот бўлса — устидан ёзмаймиз
    rr = _resource_rows(ws_vals, cols.data_start, cols.data_stop, cols.name, cols.unit)
    for c in range(QTY_UNIT_COL, AMOUNT_COL + 1):
        if c in moving or c in cols.money or c in _covered(merged_qty):
            continue
        busy = sum(1 for r in rr if cell_value(ws_vals, r, c) not in (None, ""))
        if busy > max(2, len(rr) // 10):
            raise LayoutError(f"{_letter(c)} устунида миқдор ҳам, нарх ҳам бўлмаган маълумот бор — устидан ёзилмаслиги учун "
                              "нархланмади. Устунларни текширинг")

    parents = []
    for sheet in (ws, ws_vals):
        for m in list(sheet.merged_cells.ranges):
            if m.min_row >= top and m.min_col >= QTY_UNIT_COL and m.max_col <= AMOUNT_COL + 2 and m.max_col > m.min_col:
                if sheet is ws and m.min_row < cols.data_start and m.min_col <= min(moving) and m.max_col >= max(moving) and len(moving) > 1:
                    parents.append((m.min_row, m.max_row))  # «Количество» ota sarlavhasi
                sheet.unmerge_cells(str(m))

    plan = {}
    for r in rows:
        vals = {}
        for d, s in src.items():
            if not s:
                continue
            if r < cols.data_start:
                v = cell_value(ws, r, s)
            else:
                v = cell_value(ws_vals, r, s)
                n = num(v)
                v = n if n is not None and isinstance(v, str) else v
            vals[d] = (v, ws.cell(r, s)._style.__copy__() if ws.cell(r, s).has_style else None)
        plan[r] = vals
    for r, vals in plan.items():
        for sheet in (ws, ws_vals):
            for c in moving | {QTY_UNIT_COL, QTY_TOTAL_COL} | ({PRICE_COL, AMOUNT_COL} if r >= cols.data_start else set()):
                if (c not in cols.money or r >= cols.data_start) and (r, c) in sheet._cells                         and not isinstance(sheet._cells[(r, c)], MergedCell):
                    sheet._cells[(r, c)].value = None
            for d, (v, style) in vals.items():
                cell = sheet.cell(r, d)
                if isinstance(cell, MergedCell):  # масалан бўлим номи A:F — у ерда миқдор йўқ
                    continue
                cell.value = v
                if style is not None and sheet is ws:
                    cell._style = style
    for a, b in parents:
        ws.merge_cells(start_row=a, start_column=QTY_UNIT_COL, end_row=b, end_column=QTY_TOTAL_COL)

    what = [f"бирликка — {_letter(cols.qty_unit)}→E" if cols.qty_unit and cols.qty_unit != QTY_UNIT_COL else "",
            f"жами — {_letter(cols.qty_total)}→F" if cols.qty_total != QTY_TOTAL_COL else ""]
    what = ", ".join(w for w in what if w)
    notes.append("Миқдор устунлари стандарт жойга келтирилди" + (f" ({what})" if what else " (бирлашган катаклар ажратилди)") +
                 ": G — нарх, H — сумма")
    return notes


def _qty_merges(ws, top: int, cols: LocalColumns) -> List:
    """E..H даги горизонтал бирлашмалар (миқдор ёки нарх устунларини ёпиб турганлари)."""
    return [m for m in ws.merged_cells.ranges
            if m.min_row >= max(top, cols.data_start) and m.min_col >= QTY_UNIT_COL and m.max_col <= AMOUNT_COL
            and m.max_col > m.min_col]


def _covered(merges) -> set:
    return {c for m in merges for c in range(m.min_col + 1, m.max_col + 1)}

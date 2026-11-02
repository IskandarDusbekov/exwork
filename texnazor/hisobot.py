"""Excel: narxlangan ofyorta (ofyorta + har dalolatnoma va «Остатка» bloklari yonma-yon, Excel guruhi bilan) va
«Ф-2 №N» (faqat shu dalolatnomada bajarilgan ishlar) — texnazorning o'z faylidagi tartibda (ofyorta31.xlsx).

Asos — yuklangan faylning o'zi: ofyorta listi uslublari, kenglik, birlashtirilgan kataklar va **formulalari bilan**.
Blokda ofyortaning E..L ustunlari takrorlanadi: formulalar ustuni surilib ko'chiriladi (``=G14*F14`` -> ``=P14*O14``,
svodkada ``=H1257*F1258`` -> ``=Q1257*O1258``), faqat hajm kataklari dalolatnomadagi bilan almashadi. Excel ochilganda
hammasini o'zi hisoblaydi — sayt hisobi bilan bir xil (``sheet.Book`` ham shu formulalarni ishlatadi).
"""
import io
from copy import copy
from typing import Dict, List, Optional, Tuple

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.styles.cell_style import StyleArray
from openpyxl.cell.cell import MergedCell
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.merge import MergedCellRange

from . import services_acts as sa
from .formula import move_refs
from .models import Act, SiteObject

# bloklar foni — saytdagidek (har dalolatnoma o'z rangida, «Остатка» — yashil)
BLOCK_FILLS = ["DDEBF7", "FFF2CC", "FCE4D6", "E4DFEC", "DBF1F3", "EDEDED"]
REST_FILL = "C6E0B4"


def _source(obj: SiteObject):
    wb = load_workbook(sa._xlsx(obj), keep_links=False)
    if obj.sheet not in wb.sheetnames:
        raise sa.TexnazorError(f"Файлда «{obj.sheet}» листи топилмади")
    src = wb[obj.sheet]
    for ws in list(wb.worksheets):
        if ws is not src:
            wb.remove(ws)
    _first_sheet(wb)
    wb.calculation.fullCalcOnLoad = True  # formulalar keshsiz yoziladi — Excel ochganda hisoblaydi
    return wb, src


def _first_sheet(wb):
    """Listlar o'chirilgandan keyin ``workbookView`` da eski ``firstSheet`` / ``activeTab`` (masalan 1) qoladi —
    bitta listli faylni Excel «buzilgan» deb ochmaydi."""
    for v in wb.views:
        v.firstSheet, v.activeTab = 0, 0


def _split_cols(ws, upto: int):
    """Fayldagi ustun guruhlari (M:XFD kabi bitta yozuv) upto gacha alohida ustunlarga ajratiladi — aks holda
    blok ustunlari ular bilan ustma-ust yoziladi va Excel faylni «tuzatish»ni so'raydi."""
    dims = ws.column_dimensions
    for key, d in list(dims.items()):
        lo = d.min or column_index_from_string(key)
        hi = d.max or lo
        if hi <= lo or lo > upto:
            continue
        del dims[key]
        for c in range(lo, min(hi, upto) + 1):
            one = copy(d)
            one.index, one.min, one.max = get_column_letter(c), c, c
            dims[one.index] = one
        if hi > upto:
            tail = copy(d)
            tail.index, tail.min, tail.max = get_column_letter(upto + 1), upto + 1, hi
            dims[tail.index] = tail


def _hide_types(ws, lay, starts=(None,)):
    """Turlar ustunlari (summadan keyin: ЧЕЛ-Ч, МАШ-Ч, МАТРИАЛ, ЖИХОЗ …, КАБЕЛЬ) — yashirin (formulalari ishlayveradi).
    starts — bloklar boshi (0 dan); None — ofyortaning o'zi."""
    for start in starts:
        for j in range(lay.amount + 1, lay.block_end + 1):
            c = j if start is None else start + j - lay.qty
            ws.column_dimensions[get_column_letter(c + 1)].hidden = True


def _save(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _put(dst, src_cell, value):
    dst.value = value
    if src_cell.has_style:
        dst._style = copy(src_cell._style)


def _merge(ws, min_row: int, max_row: int, min_col: int, max_col: int):
    """``ws.merge_cells`` ning tez varianti: openpyxl har birlashtirishda hamma mavjudlari bilan solishtiradi va chegara
    uslubini qayta quradi (minglab birlashtirishda o'nlab soniya). Bu yerda kataklar uslubi manbadan ko'chirilgan —
    faqat ro'yxatga qo'shiladi va ichki kataklar MergedCell bo'ladi (uslubi saqlanadi)."""
    mcr = MergedCellRange(ws, f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{max_row}")
    ws.merged_cells.ranges.add(mcr)
    for r in range(min_row, max_row + 1):
        for c in range(min_col, max_col + 1):
            if r == min_row and c == min_col:
                continue
            old = ws._cells.get((r, c))
            m = MergedCell(ws, row=r, column=c)
            if old is not None and old.has_style:
                m._style = copy(old._style)
            ws._cells[(r, c)] = m


def _act_values(b, qty: Dict[int, float]) -> Dict[Tuple[int, int], float]:
    """Dalolatnomada o'zgaradigan kataklar: ish hajmi (E) va formulasiz resurs jami (F) — ``Book.override``."""
    return b.override(qty)


def _blocks(obj: SiteObject, b) -> List[Tuple[str, Dict[int, float], Optional[Act]]]:
    per = sa.all_qty(obj)
    out = [(a.label + (f" ({a.date:%d.%m.%Y})" if a.date else ""), per.get(a.pk, {}), a) for a in obj.acts.all()]
    out.append(("Остатка", sa.rest_qty(b, per), None))
    return out


# ===================== Хисобот =====================
def _write_block(ws, b, start: int, title: str, qty: Dict[int, float], collapsed: bool = False):
    """Bitta blok: ofyortaning ``qty..block_end`` ustunlari ``start`` (0 dan) ustunidan boshlab. Hajm / me'yor / narx
    ustunlari Excel guruhida («свернуть» — faqat summa ustuni qoladi); collapsed — yig'ilgan holda (saytdagidek)."""
    lay = b.lay
    shift = start - lay.qty

    def mover(c, r, _part):
        return (c + shift, r) if lay.qty <= c <= lay.block_end else (c, r)

    over = _act_values(b, qty)
    tops_done = {t for t in b.tops if qty.get(t)}
    fin = b.final_cell()
    tail = fin[1] if fin else None  # jamidan pastdagi tekshiruv qatorlari (shartnoma summasi, «ayirma») — blokka emas
    for j in range(lay.qty, lay.block_end + 1):
        letter = get_column_letter(j + 1)
        dim = ws.column_dimensions.get(letter)
        new = ws.column_dimensions[get_column_letter(j + shift + 1)]
        new.width = dim.width if dim is not None and dim.width else 13
    # sarlavha: blok nomi sarlavha ustidagi qatorda, keyin fayldagi sarlavha qatorlari
    for j in range(lay.qty, lay.amount):
        d = ws.column_dimensions[get_column_letter(j + shift + 1)]
        d.outline_level, d.hidden = 1, collapsed
    if collapsed:
        ws.column_dimensions[get_column_letter(lay.amount + shift + 1)].collapsed = True
    if lay.header > 1:  # yig'ilganda ham ko'rinsin — summa ustunida
        at = (lay.amount if collapsed else lay.qty) + shift + 1
        ws.cell(row=lay.header - 1, column=at, value=title).font = Font(bold=True, size=12)
    for r in range(lay.header, lay.header_end + 1):
        for j in range(lay.qty, lay.block_end + 1):
            s = ws.cell(row=r, column=j + 1)
            _put(ws.cell(row=r, column=j + shift + 1), s, s.value)
    for row in b.rows:
        kind, r = row["kind"], row["row"]
        if kind not in ("work", "free", "res", "svod") or (kind == "svod" and tail and r > tail):
            continue
        top = row["parent"] if kind == "res" else r
        done = kind == "svod" or top in tops_done
        for j in range(lay.qty, lay.block_end + 1):
            s = ws.cell(row=r, column=j + 1)
            v = s.value
            if isinstance(v, str) and v.startswith("="):
                if "!" in v:
                    continue  # boshqa varaqqa havola — ko'chmaydi
                v = move_refs(v, mover)
            elif (j, r) in over:
                v = over[(j, r)] if over[(j, r)] else None
                if v is not None and s.value is None and j == lay.qty and (lay.total, r) in over:
                    v = None  # faylda hajm faqat F da (E bo'sh) — blokda ham ikki marta yozilmaydi
            elif kind != "svod" and not done:
                v = None  # bajarilmagan ishning me'yori / narxi — blokda bo'sh (fayldagidek)
            if v is None and not s.has_style:
                continue
            _put(ws.cell(row=r, column=j + shift + 1), s, v)
    # ofyortadagi birlashtirilgan kataklar (sarlavha va ish qatorlaridagi «E:F» hajm) — blokda ham
    for m in list(ws.merged_cells.ranges):
        if m.min_col - 1 >= lay.qty and m.max_col - 1 <= lay.block_end and m.min_row >= lay.header \
                and (tail is None or m.max_row <= tail):
            _merge(ws, m.min_row, m.max_row, m.min_col + shift, m.max_col + shift)


def _clear_right(ws, col: int):
    """``col`` (1 dan) dan o'ngdagi hamma narsa — fayldagi eski qo'shimcha ustunlar (izohlar, eski bloklar) bloklar
    bilan aralashib, «########» bo'lib chiqmasin."""
    for m in list(ws.merged_cells.ranges):
        if m.max_col > col:
            ws.unmerge_cells(str(m))
    for key in [k for k in ws._cells if k[1] > col]:
        del ws._cells[key]
    for key, d in list(ws.column_dimensions.items()):
        if (d.min or column_index_from_string(key)) > col:
            del ws.column_dimensions[key]


def build_report(obj: SiteObject, collapsed=()) -> bytes:
    """Narxlangan ofyorta: bitta list — ofyorta, yonida (oraliqsiz) har dalolatnoma va «Остатка» bloklari.
    collapsed — saytda yig'ilgan bloklar («a1», «a3», «rest»): Excel'da ham yig'ilgan. (Ф-2 alohida — ``build_f2``.)"""
    b = sa.book(obj)
    wb, src = _source(obj)
    blocks = _blocks(obj, b)
    lay = b.lay
    width = lay.block_end - lay.qty + 1
    _split_cols(src, lay.block_end + 1 + len(blocks) * width + 1)
    _clear_right(src, lay.block_end + 1)
    ws = src
    ws.title = _title(wb, "Офёрта")
    starts = [lay.block_end + 1 + k * width for k in range(len(blocks))]
    last = ws.max_row
    for k, (start, (title, qty, act)) in enumerate(zip(starts, blocks)):
        _write_block(ws, b, start, title, qty, (f"a{act.number}" if act else "rest") in collapsed)
        # rang indeksi bir marta — har katakka ``.fill =`` uslublar ro'yxatini qayta xeshlaydi (sekin)
        fid = wb._fills.add(PatternFill("solid", fgColor=BLOCK_FILLS[k % len(BLOCK_FILLS)] if act else REST_FILL))
        for r in range(max(1, lay.header - 1), last + 1):
            for c in range(start + 1, start + width + 1):
                cell = ws.cell(row=r, column=c)
                if not cell._style:
                    cell._style = StyleArray()
                cell._style.fillId = fid
    _hide_types(ws, lay, [None] + starts)
    wb.active = 0
    return _save(wb)


def _title(wb, name: str) -> str:
    name = name.replace("/", "-")[:31]
    base, n = name, 2
    while name in wb.sheetnames:
        name = f"{base[:28]} {n}"
        n += 1
    return name


# ===================== Ф-2 №N =====================
def _keep_rows(b, qty: Dict[int, float]) -> List[int]:
    """Qoladigan qatorlar: sarlavha, bajarilgan ishlar (resurslari bilan), ularning tepasidagi matn qatorlari
    (bo'lim, lokal sarlavhasi), svodka va undan keyingisi (imzolar)."""
    done = {t for t in b.tops if qty.get(t)}
    keep, pending, after_item = [], [], False
    for row in b.rows:
        kind, r = row["kind"], row["row"]
        if kind == "head":
            keep.append(r)
        elif kind == "text":
            if after_item:
                pending, after_item = [], False
            pending.append(r)
        elif kind in ("work", "free", "res"):
            after_item = True
            top = row["parent"] if kind == "res" else r
            if top in done:
                keep.extend(pending)
                pending = []
                keep.append(r)
        else:  # svod
            keep.append(r)
    last = max(r["row"] for r in b.rows)
    if b.svod_rows:
        keep.extend(range(max(b.svod_rows) + 1, last + 1))
    return sorted(set(keep))


def _filter(ws, src, b, qty: Dict[int, float]):
    """``ws`` — ``src`` ning nusxasi: faqat qoladigan qatorlar yuqoriga suriladi, formulalar yangi qatorlarga moslanadi,
    hajmlar dalolatnomadagi."""
    keep = _keep_rows(b, qty)
    new_of = {r: i for i, r in enumerate(keep, start=1)}

    def first_at_or_after(r):
        for k in keep:
            if k >= r:
                return k
        return None

    def last_at_or_before(r):
        for k in reversed(keep):
            if k <= r:
                return k
        return None

    def mover(c, r, part):
        if r in new_of:
            return c, new_of[r]
        k = first_at_or_after(r) if part == "start" else last_at_or_before(r) if part == "end" else None
        return (c, new_of[k]) if k is not None else None

    over = _act_values(b, qty)
    max_col = ws.max_column
    heights = {r: src.row_dimensions[r].height for r in keep if r in src.row_dimensions}
    merges = [m for m in ws.merged_cells.ranges]
    for m in merges:
        ws.unmerge_cells(str(m))
    # yangi tartibda yozish: avval qiymatlar xotiraga (ustiga yozilib ketmasin)
    data = []
    for r in keep:
        row = b.by_row.get(r)
        kind = row["kind"] if row else "svod"
        cells = []
        for j in range(max_col):
            s = src.cell(row=r, column=j + 1)
            v = s.value
            if isinstance(v, str) and v.startswith("=") and "!" not in v and kind != "head":
                v = move_refs(v, mover)
            elif (j, r) in over:
                v = over[(j, r)] if over[(j, r)] else None
            cells.append((v, s._style if s.has_style else None))
        data.append(cells)
    for r in range(1, ws.max_row + 1):
        for j in range(max_col):
            c = ws.cell(row=r, column=j + 1)
            c.value = None
    for i, cells in enumerate(data, start=1):
        for j, (v, style) in enumerate(cells):
            c = ws.cell(row=i, column=j + 1)
            c.value = v
            if style is not None:
                c._style = copy(style)
    if ws.max_row > len(data):
        ws.delete_rows(len(data) + 1, ws.max_row - len(data))
    for r, h in heights.items():
        ws.row_dimensions[new_of[r]].height = h
    for m in merges:
        rows = list(range(m.min_row, m.max_row + 1))
        if all(r in new_of for r in rows) and new_of[rows[-1]] - new_of[rows[0]] == len(rows) - 1:
            ws.merge_cells(start_row=new_of[m.min_row], end_row=new_of[m.max_row], start_column=m.min_col,
                           end_column=m.max_col)


def build_f2(obj: SiteObject, act: Act) -> bytes:
    b = sa.book(obj)
    wb, src = _source(obj)
    qty = sa.all_qty(obj).get(act.pk, {})
    _split_cols(src, b.lay.block_end + 1)
    ws = wb.copy_worksheet(src)
    _filter(ws, src, b, qty)
    _hide_types(ws, b.lay)
    wb.remove(src)
    _first_sheet(wb)
    ws.title = _title(wb, f"Ф-2 {act.title}")
    return _save(wb)

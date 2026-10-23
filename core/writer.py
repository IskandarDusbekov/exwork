"""Lokal listga yozish (reja §2.8): G narx, H summa, I/J/K ajratish, ramka, sum qatori.

openpyxl faqat kerakli kataklarga yozadi; mavjud uslub, birlashtirish, ustun kengligi va
qator balandligiga tegmaydi. Birlashtirilgan katakka faqat chap-yuqori katakka yoziladi.
"""
import re
import shutil
import tempfile
import zipfile
from copy import copy
from pathlib import Path
from typing import Dict, Iterable, Optional

from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Border, PatternFill, Side
from openpyxl.styles.cell_style import StyleArray
from openpyxl.utils import column_index_from_string, coordinate_to_tuple, get_column_letter

from .classify import MACHINES, WORKERS

NUM_FMT = "#,##0"
PERCENT_FMT = "0.00%"
GRAY = "FFD9D9D9"
SUM_COLS = ["H", "I", "J", "K", "L", "M", "N", "O", "P", "Q"]

_THIN = Side(style="thin")


class SheetWriter:
    """Listga yozadi va nimalar yozilganini eslab qoladi (svodka hisoblash uchun)."""

    def __init__(self, ws):
        self.ws = ws
        self.written: Dict[str, object] = {}

    def cell(self, addr: str):
        """Katakni qaytaradi. Faylda yo'q katak yaratilsa, Excel kabi qator (yoki ustun)
        uslubini meros qiladi — aks holda openpyxl standart shriftni qo'yib yuboradi."""
        row, col = coordinate_to_tuple(addr)
        exists = (row, col) in self.ws._cells
        cell = self.ws.cell(row=row, column=col)
        if not exists:
            style = self._inherited_style(row, col)
            if style is not None:
                cell._style = copy(style)
        return cell

    def _inherited_style(self, row: int, col: int):
        rd = self.ws.row_dimensions.get(row)
        if rd is not None and rd.customFormat:
            return rd._style
        for cd in self.ws.column_dimensions.values():
            if cd.min and cd.max and cd.min <= col <= cd.max and cd.has_style:
                return cd._style
        return None

    def set(self, addr: str, value) -> bool:
        """Qiymat yoki formula yozadi; birlashmaning chap-yuqori bo'lmagan katagini o'tkazib yuboradi."""
        cell = self.cell(addr)
        if isinstance(cell, MergedCell):
            return False
        cell.value = value
        self.written[addr] = value
        return True

    def number_format(self, col: str, start: int, end: int, fmt: str = NUM_FMT):
        for r in range(start, end + 1):
            self.cell(f"{col}{r}").number_format = fmt

    def bold(self, addr: str):
        cell = self.cell(addr)
        font = copy(cell.font)
        font.b = True
        cell.font = font

    def _cells(self, start_row: int, end_row: int, start_col: str, end_col: str):
        c1, c2 = column_index_from_string(start_col), column_index_from_string(end_col)
        for r in range(start_row, end_row + 1):
            for c in range(c1, c2 + 1):
                yield self.cell(f"{get_column_letter(c)}{r}")

    def fill(self, start_row: int, end_row: int, start_col: str = "A", end_col: str = "H", color: str = GRAY):
        for cell in self._cells(start_row, end_row, start_col, end_col):
            cell.fill = PatternFill(fill_type="solid", fgColor=color)

    def frame(self, start_row: int, end_row: int, start_col: str = "A", end_col: str = "H"):
        """Tashqi va ichki barcha chiziqlar yupqa (Excel Borders 7..12).

        Excel umumiy chegarani bitta katakda saqlaydi: oraliq chetiga chiziq qo'yilganda
        qo'shni katakning qarama-qarshi chizig'i olib tashlanadi. Shuni takrorlaymiz.
        """
        for cell in self._cells(start_row, end_row, start_col, end_col):
            old = cell.border
            cell.border = Border(
                left=_THIN, right=_THIN, top=_THIN, bottom=_THIN,
                diagonal=old.diagonal, diagonalUp=old.diagonalUp, diagonalDown=old.diagonalDown,
                vertical=old.vertical, horizontal=old.horizontal, outline=old.outline,
            )
        c1, c2 = column_index_from_string(start_col), column_index_from_string(end_col)
        for c in range(c1, c2 + 1):
            self._clear_side(start_row - 1, c, "bottom")
            self._clear_side(end_row + 1, c, "top")
        for r in range(start_row, end_row + 1):
            self._clear_side(r, c1 - 1, "right")
            self._clear_side(r, c2 + 1, "left")

    def _clear_side(self, row: int, col: int, side: str):
        if row < 1 or col < 1 or (row, col) not in self.ws._cells:
            return
        cell = self.ws._cells[(row, col)]
        current = getattr(cell.border, side)
        if current is None or current.style is None:
            return
        border = copy(cell.border)
        setattr(border, side, Side())
        cell.border = border


def amount_formula(r: int) -> str:
    return f'=IF(F{r}="",E{r}*G{r},F{r}*G{r})'


def write_priced_rows(w: SheetWriter, start: int, end: int, g_values: Dict[int, object], kinds: Dict[int, str]):
    """G..K ustunlarini ``start..end`` oralig'ida to'ldiradi va A–H ramka chizadi."""
    for r in range(start, end + 1):
        w.set(f"G{r}", g_values.get(r))
    w.number_format("G", start, end)

    for r in range(start, end + 1):
        w.set(f"H{r}", amount_formula(r))
    w.number_format("H", start, end)

    for r in range(start, end + 1):
        kind = kinds[r]
        ref = f"=H{r}"
        w.set(f"I{r}", ref if kind == WORKERS else None)
        w.set(f"J{r}", ref if kind == MACHINES else None)
        w.set(f"K{r}", ref if kind not in (WORKERS, MACHINES) else None)
    for col in "IJK":
        w.number_format(col, start, end)

    w.frame(start, end, "A", "H")


def write_sum_row(w: SheetWriter, sum_row: int, start: int, end: int):
    for col in SUM_COLS:
        w.set(f"{col}{sum_row}", f"=SUM({col}{start}:{col}{end})")
        w.cell(f"{col}{sum_row}").number_format = NUM_FMT


# ===================== birlashgan kataklar uslubini tiklash =====================
# openpyxl yuklashda birlashmaning chap-yuqori bo'lmagan kataklarini yangi MergedCell bilan
# almashtiradi: asl uslub yo'qoladi, chegaraga faqat chap-yuqori katakdagi tomonlar ko'chadi.
# Masalan, «E15:F15» da o'ng chiziq faqat F15 da bo'lsa, u yo'qolardi. Asl uslub indeksini
# XML'dan o'qib qaytaramiz.

_S_ATTR_RE = re.compile(r'\bs="(\d+)"')


def _raw_style_ids(sheet_xml: str) -> Dict[tuple, int]:
    ids = {}
    for m in re.finditer(r"<c\b[^>]*>", sheet_xml):
        tag = m.group(0)
        ref = re.search(r'\br="([A-Z]+)(\d+)"', tag)
        if not ref:
            continue
        s = _S_ATTR_RE.search(tag)
        ids[(int(ref.group(2)), column_index_from_string(ref.group(1)))] = int(s.group(1)) if s else 0
    return ids


def restore_merged_styles(wb, path: Path) -> int:
    """``path`` dan yuklangan ``wb`` dagi MergedCell'larga asl uslubni qaytaradi."""
    restored = 0
    with zipfile.ZipFile(path) as z:
        parts = _sheet_parts(z)
        for ws in wb.worksheets:
            part = parts.get(ws.title)
            if not part or not ws.merged_cells.ranges:
                continue
            ids = _raw_style_ids(z.read(part).decode("utf-8"))
            for mcr in ws.merged_cells.ranges:
                for coord in mcr.cells:
                    cell = ws._cells.get(coord)
                    if not isinstance(cell, MergedCell):
                        continue
                    style_id = ids.get(coord, 0)
                    cell._style = copy(wb._cell_styles[style_id]) if style_id else StyleArray()
                    restored += 1
    return restored


# ===================== header/footer tiklash =====================
# openpyxl rus tilidagi kodli (&Л, &Ц ...) header/footer'ni o'qiy olmaydi va saqlashda
# tashlab yuboradi. Saqlangandan keyin uni asl fayldagi XML'dan qaytaramiz.

_HF_RE = re.compile(r"<headerFooter\b[^>]*/>|<headerFooter\b.*?</headerFooter>", re.S)
_AFTER_HF = (
    "rowBreaks", "colBreaks", "customProperties", "cellWatches", "ignoredErrors", "smartTags",
    "drawing", "legacyDrawing", "legacyDrawingHF", "drawingHF", "picture", "oleObjects",
    "controls", "webPublishItems", "tableParts", "extLst",
)


def _sheet_parts(z: zipfile.ZipFile) -> Dict[str, str]:
    """List nomi -> zip ichidagi XML yo'li."""
    wb_xml = z.read("xl/workbook.xml").decode("utf-8")
    rels_xml = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    targets = {}
    for m in re.finditer(r"<Relationship\b[^>]*>", rels_xml):
        tag = m.group(0)
        rid = re.search(r'\bId="([^"]+)"', tag)
        tgt = re.search(r'\bTarget="([^"]+)"', tag)
        if rid and tgt:
            t = tgt.group(1)
            targets[rid.group(1)] = t.lstrip("/") if t.startswith("/") else "xl/" + t
    parts = {}
    for m in re.finditer(r"<sheet\b[^>]*>", wb_xml):
        tag = m.group(0)
        name = re.search(r'\bname="([^"]*)"', tag)
        rid = re.search(r'\br:id="([^"]+)"', tag) or re.search(r'\bid="([^"]+)"', tag)
        if name and rid and rid.group(1) in targets:
            parts[_xml_unescape(name.group(1))] = targets[rid.group(1)]
    return parts


def _xml_unescape(s: str) -> str:
    return s.replace("&quot;", '"').replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _insert_header_footer(sheet_xml: str, hf: str) -> str:
    if _HF_RE.search(sheet_xml):
        return _HF_RE.sub(lambda _: hf, sheet_xml, count=1)
    for tag in _AFTER_HF:
        m = re.search(rf"<{tag}\b", sheet_xml)
        if m:
            return sheet_xml[:m.start()] + hf + sheet_xml[m.start():]
    i = sheet_xml.rindex("</worksheet>")
    return sheet_xml[:i] + hf + sheet_xml[i:]


def restore_header_footer(src_path: Path, out_path: Path, sheet_titles: Iterable[str]) -> int:
    """``src_path`` dagi listlarning headerFooter'ini ``out_path`` ga ko'chiradi. Nechta tiklanganini qaytaradi."""
    with zipfile.ZipFile(src_path) as zs:
        src_parts = _sheet_parts(zs)
        hf_by_title: Dict[str, str] = {}
        for title in sheet_titles:
            part = src_parts.get(title)
            if not part:
                continue
            m = _HF_RE.search(zs.read(part).decode("utf-8"))
            if m:
                hf_by_title[title] = m.group(0)
    if not hf_by_title:
        return 0

    with zipfile.ZipFile(out_path) as zo:
        out_parts = _sheet_parts(zo)
        replace: Dict[str, bytes] = {}
        for title, hf in hf_by_title.items():
            part = out_parts.get(title)
            if part:
                xml = zo.read(part).decode("utf-8")
                replace[part] = _insert_header_footer(xml, hf).encode("utf-8")
        if not replace:
            return 0
        fd, tmp = tempfile.mkstemp(suffix=out_path.suffix, dir=out_path.parent)
        with open(fd, "wb") as fh, zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as zn:
            for item in zo.infolist():
                data = replace.get(item.filename)
                zn.writestr(item, data if data is not None else zo.read(item.filename))
    shutil.move(tmp, out_path)
    return len(replace)


def read_header_footer(path: Path, title: str) -> Optional[str]:
    with zipfile.ZipFile(path) as z:
        part = _sheet_parts(z).get(title)
        if not part:
            return None
        m = _HF_RE.search(z.read(part).decode("utf-8"))
        return m.group(0) if m else None

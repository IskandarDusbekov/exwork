""".xls → .xlsx LibreOffice'сиз (xlrd): қийматлар, бирлашган катаклар, листлар кўринадими ва асосий кўриниш
(устун эни, қалин ёзув, текислаш, фон ранги, чегаралар).

Нарх базасига йиғиш учун: у ерда фақат қийматлар керак (номи, бирлиги, нархи) — формат эмас. LibreOffice ҳар файл
учун ишга тушиб-ўчади (бир неча сония, юзлаб МБ RAM); бу эса шу жараённинг ўзида, бир неча баравар тез.
Смета нархлашда (натижа Excel асл кўринишини сақлаши керак) ишлатилмайди — у ерда ``convert.ensure_xlsx``.

Ўқиб бўлмаса (бузуқ файл, «.xls» номли HTML/XML — баъзи дастурлар шундай чиқаради) — ``FastXlsError``:
чақирувчи LibreOffice'га қайтади (``to_xlsx_values``)."""
import datetime as dt
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


class FastXlsError(RuntimeError):
    pass


def available() -> bool:
    try:
        import xlrd  # noqa: F401
    except ImportError:
        return False
    return True


_HOR = {1: "left", 2: "center", 3: "right", 4: "fill", 5: "justify", 6: "centerContinuous", 7: "distributed"}
_VERT = {0: "top", 1: "center", 2: "bottom", 3: "justify", 4: "distributed"}
_THIN = Side(style="thin", color="FF000000")
_MEDIUM = Side(style="medium", color="FF000000")


def _value(xlrd, book, ctype, v):
    if ctype == xlrd.XL_CELL_TEXT:
        s = ILLEGAL_CHARACTERS_RE.sub("", v)
        return s if s != "" else None
    if ctype == xlrd.XL_CELL_NUMBER:
        if v == int(v) and abs(v) < 1e15:
            return int(v)  # LibreOffice ҳам бутун сонни бутун ёзади: «258», «258.0» эмас
        return v
    if ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate_as_datetime(v, book.datemode)
        except (ValueError, OverflowError, xlrd.xldate.XLDateError):
            return v
    if ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(v)
    if ctype == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(v, "#ERR!")
    return None


def _rgb(book, idx):
    rgb = (book.colour_map or {}).get(idx)
    if not rgb:
        return None
    return "FF%02X%02X%02X" % rgb


def _side(style):
    if not style:
        return Side()
    return _MEDIUM if style in (2, 5, 8, 10, 12, 13) else _THIN


class _Styles:
    """xlrd XF → openpyxl услублари (ҳар XF бир марта ҳисобланади)."""

    def __init__(self, book):
        self.book = book
        self.memo = {}

    def get(self, xf_index):
        if xf_index in self.memo:
            return self.memo[xf_index]
        book = self.book
        out = None
        try:
            xf = book.xf_list[xf_index]
            font = book.font_list[xf.font_index]
            st = {}
            if font.bold or font.italic:
                st["font"] = Font(bold=bool(font.bold), italic=bool(font.italic))
            al = xf.alignment
            hor, vert = _HOR.get(al.hor_align), _VERT.get(al.vert_align)
            if hor or al.text_wrapped or vert not in (None, "bottom"):
                st["alignment"] = Alignment(horizontal=hor, vertical=vert, wrap_text=bool(al.text_wrapped))
            bg = xf.background
            if bg.fill_pattern:
                color = _rgb(book, bg.pattern_colour_index)
                if color and color != "FFFFFFFF":
                    st["fill"] = PatternFill("solid", fgColor=color)
            b = xf.border
            if b.left_line_style or b.right_line_style or b.top_line_style or b.bottom_line_style:
                st["border"] = Border(left=_side(b.left_line_style), right=_side(b.right_line_style),
                                      top=_side(b.top_line_style), bottom=_side(b.bottom_line_style))
            out = st or None
        except (IndexError, AttributeError, KeyError):
            out = None
        self.memo[xf_index] = out
        return out


def _open(xlrd, path: Path):
    try:
        return xlrd.open_workbook(str(path), formatting_info=True, on_demand=True), True
    except NotImplementedError:
        pass  # формат маълумоти ўқилмайди — фақат қийматлар
    except Exception as e:  # noqa: BLE001 — xlrd хатолари турли-туман
        raise FastXlsError(str(e) or e.__class__.__name__) from e
    try:
        return xlrd.open_workbook(str(path), on_demand=True), False
    except Exception as e:  # noqa: BLE001
        raise FastXlsError(str(e) or e.__class__.__name__) from e


def _title(name, used):
    t = "".join("_" if ch in '[]:*?/\\' else ch for ch in str(name or "Лист"))[:31].strip("'") or "Лист"
    base, n = t, 1
    while t.lower() in used:
        n += 1
        suffix = f" ({n})"
        t = base[:31 - len(suffix)] + suffix
    used.add(t.lower())
    return t


def xls_to_xlsx(path: Path, out: Path) -> Path:
    """``path`` (.xls) → ``out`` (.xlsx). Ўқиб бўлмаса — ``FastXlsError``."""
    try:
        import xlrd
    except ImportError as e:
        raise FastXlsError("xlrd ўрнатилмаган") from e
    path, out = Path(path), Path(out)
    book, fmt = _open(xlrd, path)
    try:
        styles = _Styles(book) if fmt else None
        wb = Workbook()
        wb.remove(wb.active)
        used = set()
        for i in range(book.nsheets):
            sh = book.sheet_by_index(i)
            ws = wb.create_sheet(_title(sh.name, used))
            vis = getattr(sh, "visibility", 0)
            if vis == 1:
                ws.sheet_state = "hidden"
            elif vis == 2:
                ws.sheet_state = "veryHidden"
            for r in range(sh.nrows):
                types, values = sh.row_types(r), sh.row_values(r)
                for c, (ct, v) in enumerate(zip(types, values)):
                    val = _value(xlrd, book, ct, v)
                    st = styles.get(sh.cell_xf_index(r, c)) if styles else None
                    if val is None and not st:
                        continue
                    cell = ws.cell(row=r + 1, column=c + 1)
                    if val is not None:
                        cell.value = val
                        if isinstance(val, str) and val.startswith("="):
                            cell.data_type = "s"  # матн, формула эмас
                        elif isinstance(val, dt.datetime):
                            cell.number_format = "DD.MM.YYYY"
                    if st:
                        for k, s in st.items():
                            setattr(cell, k, s)
            for rlo, rhi, clo, chi in sh.merged_cells:
                if rhi - rlo > 1 or chi - clo > 1:
                    ws.merge_cells(start_row=rlo + 1, end_row=rhi, start_column=clo + 1, end_column=chi)
            if fmt:
                for c, info in (sh.colinfo_map or {}).items():
                    if c < 16384 and info.width:
                        ws.column_dimensions[get_column_letter(c + 1)].width = round(info.width / 256, 2)
                        if info.hidden:
                            ws.column_dimensions[get_column_letter(c + 1)].hidden = True
            book.unload_sheet(i)
        if not wb.worksheets:
            raise FastXlsError("листлар йўқ")
        out.parent.mkdir(parents=True, exist_ok=True)
        wb.save(out)
        wb.close()
    finally:
        book.release_resources()
    return out


def to_xlsx_values(path: Path, out_dir: Path, **kw) -> Path:
    """Нарх базаси учун: ``.xlsx``/``.xlsm`` — ўзи; ``.xls`` — аввал xlrd (тез, LibreOffice'сиз), ўқиб бўлмаса —
    одатдаги ``convert.ensure_xlsx`` (LibreOffice, кеш билан). ``kw`` — ``ensure_xlsx`` га."""
    from . import convert
    path = Path(path)
    if path.suffix.lower() != ".xls" or not available():
        return convert.ensure_xlsx(path, out_dir, **kw)
    out = Path(out_dir) / (path.stem + ".xlsx")
    try:
        xls_to_xlsx(path, out)
        convert.check_xlsx(out)
        return out
    except Exception:  # noqa: BLE001 — xlrd ўқий олмади: LibreOffice тушунади
        out.unlink(missing_ok=True)
        return convert.ensure_xlsx(path, out_dir, **kw)

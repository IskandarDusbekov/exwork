"""Ofyorta listining ko'rinishi — saytda Excel'dagidek chiqishi uchun: katak uslublari (fon, shrift rangi, qalin,
tekislash, chegara, son formati), ustun kengliklari (yashirin ustunlar), qator balandliklari va birlashtirilgan
kataklar.

Natija JSON (obyekt papkasida ``style.json``):

* ``styles`` — ``[css, fmt]`` ro'yxati; 0 — uslubsiz katak. ``fmt``: ``null`` (General) yoki
  ``[kasr xonalar, minglik ajratgich, foiz]``;
* ``pats`` / ``rows`` — qator uslublari naqshlari (ko'p qator bir xil) va ``{qator: naqsh}``;
* ``cols`` — ustun kengligi pikselda (0 — yashirin), ``heights`` — ``{qator: px}``, ``merges`` — ``[r1, c1, r2, c2]``
  (qator 1 dan, ustun 0 dan).
"""
import colorsys
import posixpath
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional
from xml.etree.ElementTree import iterparse, fromstring

from openpyxl import load_workbook
from openpyxl.styles.colors import COLOR_INDEX
from openpyxl.utils import column_index_from_string, range_boundaries
from openpyxl.styles.numbers import BUILTIN_FORMATS

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PNS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
ANS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
VERSION = 1
DEFAULT_WIDTH = 8.43
CELL = re.compile(r"([A-Z]+)(\d+)")


def col_px(width: float) -> int:
    """Excel ustun kengligi (belgilar) -> piksel (Calibri 11, 100%)."""
    return int(round(width * 7 + 5)) if width > 0 else 0


# ===================== ranglar =====================
def _theme_colors(wb) -> List[str]:
    xml = getattr(wb, "loaded_theme", None)
    if not xml:
        return []
    try:
        root = fromstring(xml)
    except Exception:  # noqa: BLE001
        return []
    scheme = root.find(f".//{ANS}clrScheme")
    if scheme is None:
        return []
    out = []
    for el in scheme:
        c = el.find(f"{ANS}srgbClr")
        if c is not None:
            out.append(c.get("val"))
            continue
        c = el.find(f"{ANS}sysClr")
        out.append(c.get("lastClr", "000000") if c is not None else "000000")
    if len(out) >= 4:  # Excel tartibi: lt1, dk1, lt2, dk2, accent…
        out[0], out[1], out[2], out[3] = out[1], out[0], out[3], out[2]
    return out


def _tint(hex6: str, tint: float) -> str:
    if not tint:
        return hex6
    r, g, b = (int(hex6[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = l * (1 + tint) if tint < 0 else l * (1 - tint) + tint
    r, g, b = colorsys.hls_to_rgb(h, max(0, min(1, l)), s)
    return "".join(f"{round(x * 255):02x}" for x in (r, g, b))


def _color(c, theme: List[str], default: Optional[str] = None) -> Optional[str]:
    if c is None:
        return default
    try:
        t = c.type
        if t == "rgb" and isinstance(c.rgb, str):
            v = c.rgb[-6:]
        elif t == "theme" and theme and c.theme is not None and c.theme < len(theme):
            v = theme[c.theme]
        elif t == "indexed" and c.indexed is not None:
            if c.indexed >= 64:
                return default
            v = COLOR_INDEX[c.indexed][-6:]
        else:
            return default
        return "#" + _tint(v, c.tint or 0).lower()
    except (AttributeError, ValueError, IndexError, TypeError):
        return default


# ===================== uslub -> CSS =====================
_BORDER = {"thin": "1px solid", "hair": "1px solid", "dotted": "1px dotted", "dashed": "1px dashed",
           "dashDot": "1px dashed", "dashDotDot": "1px dashed", "medium": "2px solid", "mediumDashed": "2px dashed",
           "mediumDashDot": "2px dashed", "mediumDashDotDot": "2px dashed", "slantDashDot": "2px dashed",
           "thick": "3px solid", "double": "3px double"}
_H = {"center": "center", "centerContinuous": "center", "right": "right", "left": "left", "justify": "justify",
      "distributed": "center", "fill": "left"}
_V = {"top": "top", "center": "middle", "bottom": "bottom", "justify": "middle", "distributed": "middle"}


def _numfmt(code: str):
    """Son formati -> [kasr xonalar, minglik, foiz]; ``None`` — General (sayt o'zi chiroyli ko'rsatadi)."""
    if not code or code.lower() == "general" or code == "@":
        return None
    sec = code.split(";")[0]
    sec = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.', "", sec)
    if not re.search(r"[0#?]", sec):
        return None
    dec = re.search(r"\.([0#?]+)", sec)
    return [len(dec.group(1)) if dec else 0, "," in sec, "%" in sec]


def _css(st, theme: List[str], fonts: Dict[str, int]) -> str:
    out = []
    fill = st.fill
    if fill is not None and getattr(fill, "fill_type", None) == "solid":
        bg = _color(fill.fgColor, theme)
        if bg and bg != "#ffffff":
            out.append(f"background:{bg}")
    elif fill is not None and getattr(fill, "fill_type", None) not in (None, "none"):
        bg = _color(fill.fgColor, theme) or _color(fill.bgColor, theme)
        if bg and bg != "#ffffff":
            out.append(f"background:{bg}")
    f = st.font
    if f is not None:
        col = _color(f.color, theme)
        if col and col != "#000000":
            out.append(f"color:{col}")
        if f.b:
            out.append("font-weight:700")
        if f.i:
            out.append("font-style:italic")
        if f.u and f.u != "none":
            out.append("text-decoration:underline")
        if f.strike:
            out.append("text-decoration:line-through")
        if f.sz and abs(float(f.sz) - 11) > 0.01:
            out.append(f"font-size:{float(f.sz) * 4 / 3:.1f}px")
        if f.name and f.name.lower() not in ("calibri",):
            fonts[f.name] = fonts.get(f.name, 0) + 1
            generic = "'Times New Roman',serif" if "times" in f.name.lower() else "sans-serif"
            out.append(f"font-family:'{f.name}',{generic}")
    a = st.alignment
    if a is not None:
        if a.horizontal in _H:
            out.append(f"text-align:{_H[a.horizontal]}")
        out.append(f"vertical-align:{_V.get(a.vertical, 'bottom')}")
        if a.wrap_text or a.horizontal in ("justify", "distributed") or a.vertical in ("justify", "distributed"):
            out.append("white-space:pre-wrap")
        if a.indent:
            out.append(f"padding-left:{3 + int(a.indent) * 9}px")
        if a.textRotation == 90:
            out.append("writing-mode:vertical-rl;transform:rotate(180deg)")
        elif a.textRotation in (180, 255):
            out.append("writing-mode:vertical-rl")
    else:
        out.append("vertical-align:bottom")
    b = st.border
    if b is not None:
        for side, name in (("left", "left"), ("right", "right"), ("top", "top"), ("bottom", "bottom")):
            s = getattr(b, side, None)
            if s is not None and s.style in _BORDER:
                out.append(f"border-{name}:{_BORDER[s.style]} {_color(s.color, theme, '#000000')}")
    return ";".join(out)


# ===================== o'qish =====================
def _sheet_part(z: zipfile.ZipFile, sheet: str) -> str:
    wbx = fromstring(z.read("xl/workbook.xml"))
    rid = next((s.get(f"{RNS}id") for s in wbx.iter(f"{NS}sheet") if s.get("name") == sheet), None)
    rels = fromstring(z.read("xl/_rels/workbook.xml.rels"))
    for r in rels.iter(f"{PNS}Relationship"):
        if r.get("Id") == rid:
            t = r.get("Target")
            return t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join("xl", t))
    raise KeyError(sheet)


def read_style(path: Path, sheet: str, width: int, last_row: int) -> dict:
    """Listning ko'rinishi (uslublar, kengliklar, balandliklar, birlashmalar) — qiymatlarga tegmaydi."""
    wb = load_workbook(path, read_only=True, keep_links=False)
    try:
        theme = _theme_colors(wb)
        xfs, fonts = wb._cell_styles, {}
        cache: Dict[int, int] = {}
        styles: List[list] = [["", None]]
        index: Dict[tuple, int] = {("", None): 0}
        formats = dict(BUILTIN_FORMATS)
        formats.update({i + 164: f for i, f in enumerate(getattr(wb, "_number_formats", []))})

        def style_of(sid: int) -> int:
            if sid in cache:
                return cache[sid]
            k = 0
            if 0 < sid < len(xfs):
                xf = xfs[sid]

                pick = lambda seq, i: seq[i] if i < len(seq) else None  # noqa: E731
                st = SimpleNamespace(font=pick(wb._fonts, xf.fontId), fill=pick(wb._fills, xf.fillId),
                                     border=pick(wb._borders, xf.borderId), alignment=pick(wb._alignments, xf.alignmentId))
                key = (_css(st, theme, fonts), _numfmt(formats.get(xf.numFmtId, "General")))
                key = (key[0], tuple(key[1]) if key[1] else None)
                if key not in index:
                    index[key] = len(styles)
                    styles.append([key[0], list(key[1]) if key[1] else None])
                k = index[key]
            cache[sid] = k
            return k

        default_w, default_h, cols, heights, merges = DEFAULT_WIDTH, None, {}, {}, []
        pats: List[list] = []
        pat_index: Dict[tuple, int] = {}
        rows: Dict[int, int] = {}
        with zipfile.ZipFile(path) as z, z.open(_sheet_part(z, sheet)) as fh:
            row_no, row_s, cur = 0, [], None
            for ev, el in iterparse(fh, events=("start", "end")):
                tag = el.tag[len(NS):] if el.tag.startswith(NS) else el.tag
                if ev == "start":
                    if tag == "row":
                        row_no = int(el.get("r") or row_no + 1)
                        cur = [0] * width
                        rs = int(el.get("s") or 0) if el.get("customFormat") in ("1", "true") else 0
                        if rs:
                            k = style_of(rs)
                            cur = [k] * width
                        ht = el.get("ht")
                        if ht and row_no <= last_row:
                            heights[row_no] = round(float(ht) * 4 / 3)
                    continue
                if tag == "c" and cur is not None:
                    m = CELL.match(el.get("r") or "")
                    j = column_index_from_string(m.group(1)) - 1 if m else None
                    if j is not None and j < width:
                        cur[j] = style_of(int(el.get("s") or 0))
                    el.clear()
                elif tag == "row":
                    if cur is not None and row_no <= last_row:
                        t = tuple(cur)
                        if any(t):
                            if t not in pat_index:
                                pat_index[t] = len(pats)
                                pats.append(list(t))
                            rows[row_no] = pat_index[t]
                    cur = None
                    el.clear()
                elif tag == "sheetFormatPr":
                    if el.get("defaultColWidth"):
                        default_w = float(el.get("defaultColWidth"))
                    elif el.get("baseColWidth"):
                        default_w = float(el.get("baseColWidth")) + 0.71
                    if el.get("defaultRowHeight"):
                        default_h = round(float(el.get("defaultRowHeight")) * 4 / 3)
                elif tag == "col":
                    lo, hi = int(el.get("min")), int(el.get("max"))
                    w = float(el.get("width") or default_w)
                    hidden = el.get("hidden") in ("1", "true")
                    for c in range(lo - 1, min(hi, width)):
                        cols[c] = 0 if hidden else col_px(w)
                elif tag == "mergeCell":
                    try:
                        c1, r1, c2, r2 = range_boundaries(el.get("ref"))
                    except (TypeError, ValueError):
                        continue
                    if r1 <= last_row and c1 - 1 < width:
                        merges.append([r1, c1 - 1, min(r2, last_row), min(c2, width) - 1])
                elif tag == "sheetData":
                    el.clear()
    finally:
        wb.close()
    return {"v": VERSION, "styles": styles, "pats": pats, "rows": {str(k): v for k, v in rows.items()},
            "cols": [cols.get(j, col_px(default_w)) for j in range(width)], "heights": {str(k): v for k, v in heights.items()},
            "default_h": default_h or 20, "merges": merges,
            "font": max(fonts, key=fonts.get) if fonts else ""}

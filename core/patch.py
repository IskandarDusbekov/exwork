"""Natija faylini qayta narxlamasdan yamash: smetachi narx yozdi, bekor qildi, L/M ga o'tkazdi, «ТОПИЛМАДИ»ni
olib tashladi, svodka foizini o'zgartirdi — butun smeta (20 000 qator, bir necha daqiqa) qaytadan narxlanmaydi,
Excel faylining o'zida o'sha kataklar yoziladi.

Formulalar faylda turadi — Excel ularni o'zi hisoblaydi. Lekin faylda formulaning **natijasi** ham saqlanadi
(saytdagi ko'rinish, telefon, «Защищённый просмотр» shuni o'qiydi), shuning uchun o'zgargan natijalar ham
yoziladi. Ular to'liq narxlashdagi o'sha baholovchi (``core.svodka``) bilan, o'sha qiymatlardan (``calc_kit``:
biz yozgan kataklar va formulalar o'qigan asl qiymatlar) hisoblanadi — natija to'liq qayta narxlash bilan bir xil
chiqadi (testlarda solishtiriladi).

Qaysi katakka nima yozilishini bu modul hal qilmaydi — chaqiruvchi beradi (``jobs/fastpatch.py`` qatorni
``core.pipeline.row_outcome`` bilan qayta tuzadi). Yamab bo'lmasa — ``NotPatchable``: to'liq hisobga o'tiladi.
"""
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string

from .log_sheet import excel_value
from .svodka import make_evaluator
from .writer import MARKED_FILL, UNPRICED_FILL, _cached_xml, _sheet_parts, number_text

# LOG listi ustunlari (log_sheet.HEADER + note): D — status, J — yozilgan qiymat, K — izoh
LOG_STATUS, LOG_WRITTEN, LOG_NOTE = "D", "J", "K"

_CELL_RE = re.compile(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)
_T_ATTR = re.compile(r'\s+t="[^"]*"')
_S_ATTR = re.compile(r'\s+s="\d+"')
_F_RE = re.compile(r"<f\b[^>]*>[^<]*</f>|<f\b[^>]*/>")


class NotPatchable(Exception):
    """Faylni yamab bo'lmaydi — to'liq qayta hisoblash kerak."""


@dataclass
class Patched:
    kit: dict                      # yangilangan to'plam (keyingi yamash shundan)
    values: Dict[str, object]      # formulalar natijasi (barchasi)
    totals: Dict[str, object]      # svodka summalari
    widened: List[str] = field(default_factory=list)


def _formula(v) -> bool:
    return isinstance(v, str) and v.startswith("=")


def _num_xml(v) -> str:
    return repr(float(v)) if isinstance(v, float) else str(v)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _row_of(addr: str) -> int:
    return int(coordinate_from_string(addr)[1])


def apply_cells(path: Path, kit: dict, cells: Dict[str, object], drop: Iterable[str] = (),
                log: Optional[Dict[int, tuple]] = None, cf: Optional[Dict[str, List[int]]] = None) -> Patched:
    """``cells`` {manzil: qiymat yoki "=formula"} — yoziladi; ``drop`` — katak bo'shatiladi (biz endi yozmaymiz);
    ``log`` {qator: (status, yozilgan, izoh)} — LOG qatori; ``cf`` {"blank": qatorlar, "yellow": qatorlar} — G dagi
    «narx kutyapti» qizil / sariq ro'yxati. Bog'liq formulalar natijasi qayta hisoblanadi.
    Fayl joyida almashtiriladi (avval vaqtinchalik nusxa — xato bo'lsa asl fayl buzilmaydi)."""
    path = Path(path)
    start, end = int(kit["start"]), int(kit["end"])
    written = dict(kit["written"])
    drop = set(drop)
    for addr in drop:
        written.pop(addr, None)
    written.update(cells)

    looked = kit["looked"]
    ev = make_evaluator(written, looked.get)
    old_cached = kit["cached"]
    # Qator formulalari faqat o'z qatoriga qaraydi (H = E/F × G, I…M = H): o'zgarmagan qatorlar natijasi o'sha.
    # Qayta hisoblanadi: o'zgargan qatorlar va jadvaldan tashqaridagilar (jami qator, svodka)
    touched = {_row_of(a) for a in list(cells) + list(drop)}
    cached = {a: v for a, v in old_cached.items() if a in written and _formula(written[a])}
    for addr, v in written.items():
        if _formula(v) and (addr not in cached or _row_of(addr) in touched or not start <= _row_of(addr) <= end):
            cached[addr] = ev.value(addr)
    changed = {a: v for a, v in cached.items() if a not in old_cached or old_cached[a] != v or a in cells}

    with zipfile.ZipFile(path) as z:
        parts = _sheet_parts(z)
        sheet_part = parts.get(kit["sheet"])
        if not sheet_part:
            raise NotPatchable("натижа листи файлда йўқ")
        replace: Dict[str, str] = {}
        xml = _patch_sheet(z.read(sheet_part).decode("utf-8"), cells, drop, changed)
        if cf is not None:
            styles = z.read("xl/styles.xml").decode("utf-8")
            xml, styles2 = _patch_cf(xml, styles, cf)
            if styles2 != styles:
                replace["xl/styles.xml"] = styles2
        values = {**{a: v for a, v in cells.items() if not _formula(v)}, **{a: cached[a] for a in changed}}
        xml, widened = _fit_widths(xml, values, kit.get("ratios") or {})
        replace[sheet_part] = xml
        log_part = parts.get(kit.get("log_title") or "")
        if log_part and log:
            replace[log_part] = _patch_log(z, log_part, kit, log)
        fd, tmp = tempfile.mkstemp(suffix=path.suffix, dir=path.parent)
        try:
            with open(fd, "wb") as fh, zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as zn:
                for item in z.infolist():
                    data = replace.get(item.filename)
                    zn.writestr(item, z.read(item.filename) if data is None else data.encode("utf-8"))
        except BaseException:
            os.unlink(tmp)
            raise
    shutil.move(tmp, path)

    return Patched(kit=dict(kit, written=written, cached=cached), values=cached,
                   totals={key: ev.value(addr) for key, addr in kit["svodka"].items()}, widened=widened)


def apply_prices(path: Path, kit: dict, prices: Dict[int, float], log_notes: Optional[Dict[int, str]] = None) -> Patched:
    """Faqat G ga narx (qolgani o'zgarmaydi) — ``apply_cells`` ning qisqa yo'li."""
    start, end = int(kit["start"]), int(kit["end"])
    for r in prices:
        if not start <= r <= end or f"G{r}" not in kit["written"] or not _formula(kit["written"].get(f"H{r}")):
            raise NotPatchable(f"{r}-қатор нархланадиган қатор эмас")
    log = {r: ("MANUAL", p, (log_notes or {}).get(r, "")) for r, p in prices.items()} if log_notes is not None else None
    return apply_cells(path, kit, {f"G{r}": p for r, p in prices.items()}, log=log)


# ---------------- smeta listi ----------------
def _cell_xml(ref: str, attrs: str, v, cached=None) -> str:
    attrs = _T_ATTR.sub("", attrs)
    if _formula(v):
        f = f"<f>{_esc(v[1:])}</f>"
        return _cached_xml(ref, attrs, f, cached) or f'<c r="{ref}"{attrs}>{f}<v></v></c>'
    return _value_cell(ref, attrs, v)


def _patch_sheet(xml: str, cells: Dict[str, object], drop: set, changed: Dict[str, object]) -> str:
    todo = set(cells) | drop | set(changed)
    done = set()

    def fix(m):
        ref = m.group(1) + m.group(2)
        if ref not in todo:
            return m.group(0)
        done.add(ref)
        attrs = m.group(3) or ""
        if ref in drop:
            return f'<c r="{ref}"{_T_ATTR.sub("", attrs)}/>'
        if ref in cells:
            return _cell_xml(ref, attrs, cells[ref], changed.get(ref))
        f = _F_RE.search(m.group(4) or "")
        if not f:
            raise NotPatchable(f"{ref}: формула файлда йўқ")
        attrs = _T_ATTR.sub("", attrs)
        return _cached_xml(ref, attrs, f.group(0), changed[ref]) or f'<c r="{ref}"{attrs}>{f.group(0)}<v></v></c>'

    xml = _CELL_RE.sub(fix, xml)
    missing = (set(cells) | set(changed)) - done
    if not missing:
        return xml
    add: Dict[int, List[tuple]] = {}
    donors: Dict[str, Optional[str]] = {}
    for ref in missing:
        if ref not in cells:
            raise NotPatchable(f"{ref}: формула файлда йўқ")
        col, row = coordinate_from_string(ref)
        if col not in donors:
            donors[col] = _donor_style(xml, col)
        attrs = f' s="{donors[col]}"' if donors[col] else ""
        add.setdefault(row, []).append((col, _cell_xml(ref, attrs, cells[ref], changed.get(ref))))
    return _insert_cells(xml, add)


def _donor_style(xml: str, col: str) -> Optional[str]:
    """Shu ustunda «=H…» formulali katak uslubi — yangi L/M katagi o'shanday ko'rinsin (son formati)."""
    m = re.search(rf'<c r="{col}\d+"[^>]*?\bs="(\d+)"[^>]*><f>H\d+</f>', xml)
    return m.group(1) if m else None


# ---------------- «narx kutyapti» rangi (shartli format) ----------------
_CF_RE = re.compile(r'<conditionalFormatting sqref="([^"]*)">(.*?)</conditionalFormatting>', re.S)
_DXF_RE = re.compile(r"<dxf>(.*?)</dxf>|<dxf/>", re.S)
_CF_COLORS = {"blank": UNPRICED_FILL, "yellow": MARKED_FILL}


def _patch_cf(xml: str, styles: str, want: Dict[str, List[int]]):
    dxfs = [m.group(1) or "" for m in _DXF_RE.finditer(styles[styles.find("<dxfs"):styles.find("</dxfs>")])] \
        if "<dxfs" in styles else []
    color_of = [(re.search(r'<bgColor rgb="([^"]+)"', d) or [None, None])[1] for d in dxfs]
    blocks = list(_CF_RE.finditer(xml))
    found = {}
    priorities = [0]
    for b in blocks:
        rule = b.group(2)
        priorities += [int(p) for p in re.findall(r'priority="(\d+)"', rule)]
        f = re.search(r"<formula>([^<]*)</formula>", rule)
        dxf = re.search(r'dxfId="(\d+)"', rule)
        if not (f and dxf and f.group(1).startswith("NOT(ISNUMBER(G")) or "<cfRule" not in rule or rule.count("<cfRule") != 1:
            continue
        color = color_of[int(dxf.group(1))] if int(dxf.group(1)) < len(color_of) else None
        for kind, c in _CF_COLORS.items():
            if color == c and kind not in found:
                found[kind] = b
    edits = []  # (boshi, oxiri, yangi matn)
    for kind, rows in want.items():
        rows = sorted(set(rows))
        b = found.get(kind)
        if b is not None:
            if b.group(1) == " ".join(f"G{r}" for r in rows):
                continue
            if not rows:
                edits.append((b.start(), b.end(), ""))
                continue
            rule = re.sub(r"<formula>[^<]*</formula>", f"<formula>NOT(ISNUMBER(G{rows[0]}))</formula>", b.group(2))
            edits.append((b.start(), b.end(), f'<conditionalFormatting sqref="{" ".join(f"G{r}" for r in rows)}">{rule}'
                                              "</conditionalFormatting>"))
            continue
        if not rows:
            continue
        if not blocks or "<dxfs" not in styles:
            raise NotPatchable("шартли формат йўқ")
        dxf_id = len(dxfs)
        dxfs.append("new")
        styles = re.sub(r'<dxfs count="(\d+)"', lambda m: f'<dxfs count="{int(m.group(1)) + 1}"', styles, count=1)
        styles = styles.replace("</dxfs>", f'<dxf><fill><patternFill><bgColor rgb="{_CF_COLORS[kind]}"/></patternFill>'
                                           "</fill></dxf></dxfs>", 1)
        priorities.append(max(priorities) + 1)
        at = blocks[-1].end()
        edits.append((at, at, f'<conditionalFormatting sqref="{" ".join(f"G{r}" for r in rows)}"><cfRule type="expression" '
                              f'priority="{priorities[-1]}" dxfId="{dxf_id}" stopIfTrue="1"><formula>NOT(ISNUMBER(G{rows[0]}))'
                              "</formula></cfRule></conditionalFormatting>"))
    for s, e, text in sorted(edits, key=lambda x: (x[0], x[1]), reverse=True):
        xml = xml[:s] + text + xml[e:]
    return xml, styles


# ---------------- ustun eni ----------------
_COLS_RE = re.compile(r"<cols>(.*?)</cols>", re.S)
_COL_RE = re.compile(r"<col\b([^>]*?)/>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')


def _fit_widths(xml: str, values: Dict[str, object], ratios: Dict[str, float]):
    """To'liq narxlashdagi ``fit_number_columns`` kabi: yangi son sig'masa — G…Q ustuni kengayadi (toraymaydi)."""
    lo, hi = column_index_from_string("G"), column_index_from_string("Q")
    need: Dict[int, float] = {}
    for addr, v in values.items():
        text = number_text(v)
        if not text:
            continue
        letter, _r = coordinate_from_string(addr)
        c = column_index_from_string(letter)
        if lo <= c <= hi:
            need[c] = max(need.get(c, 0), len(text) * ratios.get(letter, 1.0) * 1.1 + 2)
    if not need:
        return xml, []
    m = _COLS_RE.search(xml)
    cols = [dict(_ATTR.findall(c.group(1))) for c in _COL_RE.finditer(m.group(1))] if m else []
    widened = []
    for c, width in sorted(need.items()):
        hit = next((d for d in cols if int(d["min"]) <= c <= int(d["max"])), None)
        current = float(hit["width"]) if hit and hit.get("width") else 8.43
        if width <= current + 0.01:
            continue
        width = round(width, 2)
        widened.append(get_column_letter(c))
        if hit is None:
            cols.append({"min": str(c), "max": str(c), "width": str(width), "customWidth": "1"})
            continue
        lo_, hi_ = int(hit["min"]), int(hit["max"])
        cols.remove(hit)
        if lo_ < c:
            cols.append(dict(hit, max=str(c - 1)))
        cols.append(dict(hit, min=str(c), max=str(c), width=str(width), customWidth="1"))
        if c < hi_:
            cols.append(dict(hit, min=str(c + 1)))
    if not widened:
        return xml, []
    cols.sort(key=lambda d: int(d["min"]))
    body = "".join("<col " + " ".join(f'{k}="{v}"' for k, v in d.items()) + "/>" for d in cols)
    if m:
        xml = xml[:m.start(1)] + body + xml[m.end(1):]
    else:
        i = xml.index("<sheetData")
        xml = xml[:i] + f"<cols>{body}</cols>" + xml[i:]
    return xml, widened


# ---------------- LOG listi ----------------
_SI_RE = re.compile(r"<si>(.*?)</si>", re.S)
_T_RE = re.compile(r"<t[^>]*>([^<]*)</t>")


def _status_styles(z: zipfile.ZipFile, log_xml: str, statuses: set) -> Dict[str, Dict[str, str]]:
    """LOG'da shu statusli qator bo'lsa — uning kataklari uslubi (rangi). Topilmagan status — rang o'zgarmaydi."""
    sst = {}
    if "xl/sharedStrings.xml" in z.namelist():
        for i, m in enumerate(_SI_RE.finditer(z.read("xl/sharedStrings.xml").decode("utf-8"))):
            text = "".join(_T_RE.findall(m.group(1)))
            if text in statuses and text not in sst:
                sst[text] = i
    out = {}
    for status in statuses:
        hit = None
        if status in sst:
            hit = re.search(rf'<c r="{LOG_STATUS}(\d+)"[^>]*\bt="s"[^>]*><v>{sst[status]}</v></c>', log_xml)
        hit = hit or re.search(rf'<c r="{LOG_STATUS}(\d+)"[^>]*\bt="inlineStr"[^>]*><is><t[^>]*>{re.escape(status)}</t></is></c>', log_xml)
        if not hit:
            continue
        row, styles = hit.group(1), {}
        for m in re.finditer(rf'<c r="([A-Z]+){row}"([^>]*?)(?:/>|>)', log_xml):
            s = re.search(r'\bs="(\d+)"', m.group(2))
            if s:
                styles[m.group(1)] = s.group(1)
        out[status] = styles
    return out


def _patch_log(z: zipfile.ZipFile, part: str, kit: dict, log: Dict[int, tuple]) -> str:
    xml = z.read(part).decode("utf-8")
    log_rows = kit.get("log_rows") or {}
    want: Dict[str, object] = {}
    row_status: Dict[str, str] = {}
    for r, (status, value, note) in log.items():
        lr = log_rows.get(str(r)) or log_rows.get(r)
        if not lr:
            continue
        row_status[str(lr)] = status
        want[f"{LOG_STATUS}{lr}"] = status
        want[f"{LOG_WRITTEN}{lr}"] = excel_value(value)
        want[f"{LOG_NOTE}{lr}"] = note or None
    if not want:
        return xml
    styles = _status_styles(z, xml, set(row_status.values()))
    done = set()

    def fix(m):
        col, row = m.group(1), m.group(2)
        if row not in row_status:
            return m.group(0)
        ref = col + row
        attrs = m.group(3) or ""
        style = styles.get(row_status[row]) or {}
        if col in style:  # qator rangi — shu statusdagi qatorniki
            attrs = _S_ATTR.sub("", attrs) + f' s="{style[col]}"'
        if ref not in want:
            body = m.group(4)
            return f'<c r="{ref}"{attrs}' + ("/>" if body is None else f">{body}</c>")
        done.add(ref)
        return _value_cell(ref, _T_ATTR.sub("", attrs), want[ref])

    xml = _CELL_RE.sub(fix, xml)
    for ref in sorted(set(want) - done, key=lambda a: column_index_from_string(coordinate_from_string(a)[0])):
        if want[ref] is None:
            continue
        col, row = coordinate_from_string(ref)
        style = styles.get(row_status[str(row)]) or {}
        xml = _insert_cell(xml, row, col, _value_cell(ref, f' s="{style[col]}"' if col in style else "", want[ref]))
    return xml


def _value_cell(ref: str, attrs: str, v) -> str:
    if v is None or v == "":
        return f'<c r="{ref}"{attrs}/>'
    if isinstance(v, bool):
        return f'<c r="{ref}"{attrs} t="b"><v>{int(v)}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"{attrs}><v>{_num_xml(v)}</v></c>'
    return f'<c r="{ref}"{attrs} t="inlineStr"><is><t xml:space="preserve">{_esc(str(v))}</t></is></c>'


_ROW_RE = re.compile(r'<row r="(\d+)"([^>]*?)(?:/>|>(.*?)</row>)', re.S)


def _insert_cells(xml: str, add: Dict[int, List[tuple]]) -> str:
    """``{qator: [(ustun, katak xml), …]}`` — kataklar qatorga ustunlar tartibida qo'shiladi (bitta o'tishda).
    Qator faylda yo'q bo'lsa — ``NotPatchable``."""
    done = set()

    def fix(m):
        row = int(m.group(1))
        if row not in add:
            return m.group(0)
        done.add(row)
        parts = [(column_index_from_string(cm.group(1)), cm.group(0)) for cm in _CELL_RE.finditer(m.group(3) or "")]
        parts += [(column_index_from_string(col), cell) for col, cell in add[row]]
        parts.sort(key=lambda p: p[0])
        return f'<row r="{row}"{m.group(2)}>' + "".join(c for _i, c in parts) + "</row>"

    xml = _ROW_RE.sub(fix, xml)
    if set(add) - done:
        raise NotPatchable(f"{min(set(add) - done)}-қатор файлда йўқ")
    return xml


def _insert_cell(xml: str, row: int, col: str, cell: str) -> str:
    return _insert_cells(xml, {int(row): [(col, cell)]})

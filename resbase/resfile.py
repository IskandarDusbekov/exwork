"""RES (ресурс) Excel faylini o'qish: bazaga to'g'ridan-to'g'ri yuklash uchun (smetasiz).

Qo'llanadigan listlar — narx ustuni bor resurs jadvallari: «ЛОКАЛЬНЫЙ РЕСУРСНЫЙ СМЕТНЫЙ РАСЧЕТ» (``_РС``),
«СВОДНО-РЕСУРСНАЯ ВЕДОМОСТЬ», «РЕСУРС», namuna «RES». Bitta listda bir nechta obyekt bo'lishi mumkin — har
jadval sarlavhasi alohida blok. Lokal ro'yxat (ЛРВ: norma shifrlari, narxsiz) resurs emas — list tashlab
ketiladi. Olinmaydigan qatorlar: ИТОГО/ВСЕГО, takroriy sarlavha, birligi son (ustun surilgan), narxsiz yoki 0.

``parse(path) -> ResFile``; Django'ga bog'liq emas (``python -m resbase.resfile fayl.xls`` — tekshirish uchun).
"""
import hashlib
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from openpyxl import load_workbook

from core import convert
from core.classify import KIND_COLUMN, row_kind, section_kind
from core.columns import num
from core.detect import _NAME_RE, _UNIT_RE, _is_numbering_row, cell_value, header_texts
from core.normalize import norm_code, norm_name, unit_key

from . import normbook

SKIP_LABELS = {
    "total": "ИТОГО / ВСЕГО қатори",
    "header": "такрорий сарлавҳа",
    "bad_unit": "бирлиги сон (устун сурилган)",
    "no_price": "нарх йўқ («--», матн)",
    "zero_price": "нарх 0",
    "duplicate": "шу файлда такрор",
}

_CODE_TAIL = re.compile(r"\s+[СC]$")          # «258 С» — ресурс коди (3.xlsx «РЕСУРС» листи)
_CODE_HEAD = re.compile(r"^(?:АВС|ABC)\s*")    # «АВС 000001»
_NORM_CODE = re.compile(r"^(?:Е|E|Ц|ГЭСН|ФЕР|ТЕР)\s?\d{1,4}[-\s]")  # «Е0101-197-02», «Ц08-03-599-7» — норма шифри
_TOTAL = re.compile(r"^(итого|всего)")
_OBJECT = re.compile(r"^объект\s*[-–—:]\s*(.+)$", re.I)
_PRICE_WORDS = ("цена", "стоимост", "баҳо", "бахо", "нарх")
_TOTAL_WORDS = ("общ", "сумм", "қиймат", "киймат", "всего")
_PER_UNIT = re.compile(r"на\s*\.?\s*ед|на единицу|1\s*бирлик")
_NUMBERING = re.compile(r"^(№|n\s*п|т/р|п/п|пп\b|n\b)")
_UNIT_UZ = re.compile(r"(?:^|\s)бирл")  # «Бирл.», «Бирлиги» (core.detect faqat ruscha va «ўлчов»)
LOCAL_MIN_NORMS = 3  # listda shuncha norma shifri bo'lsa — bu lokal ro'yxat (ЛРВ), resurs emas


def res_code(raw) -> str:
    """Milliy resurs kodi: «258», «000258», «258 С», «АВС 000001» → «258» / «1»; «С583», norma shifri → ""."""
    s = str(raw if raw is not None else "").strip().upper()
    s = _CODE_HEAD.sub("", _CODE_TAIL.sub("", s))
    return norm_code(s)


def name_key(name, unit) -> str:
    """Kodsiz resurs kaliti: nom + birlik («n:» bilan — kod bilan aralashmaydi)."""
    base = f"{norm_name(name)}|{unit_key(unit)}"
    return "n:" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


@dataclass
class ResRow:
    sheet: str
    row: int
    code: str          # normallashtirilgan («258»); kodsiz — ""
    raw_code: str      # faylda qanday yozilgan
    name: str
    unit: str
    qty: Optional[float]
    price: float
    category: str      # I J K L M
    obj: str = ""      # blok obyekti («МАФ»), bo'lsa

    @property
    def key(self) -> str:
        return self.code or name_key(self.name, self.unit)

    @property
    def unit_key(self) -> str:
        return unit_key(self.unit)


@dataclass
class WorkRes:
    """ЛРВ: ish ichidagi resurs — ``norma`` 1 birlik ishga, ``qty`` — jami. ``extra`` — normada yo'q, ish
    ortidan alohida yozilgan material («ПЕСЧАНО-ГРАВИЙНАЯ СМЕСЬ 88,2 м3»): normasi yo'q, faqat miqdori."""
    row: int
    code: str
    raw_code: str
    name: str
    unit: str
    norma: Optional[float]
    qty: Optional[float]
    extra: bool = False


@dataclass
class Work:
    """ЛРВ: bitta ish — norma shifri («Е0101-197-02»), to'plami («Е01»), nomi, birligi, hajmi va resurslari."""
    sheet: str
    row: int
    shifr: str
    raw: str
    book: str
    name: str
    unit: str
    qty: Optional[float]
    section: str = ""
    resources: List[WorkRes] = field(default_factory=list)


@dataclass
class ResFile:
    rows: List[ResRow] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    sheets: List[str] = field(default_factory=list)          # o'qilgan resurs listlari
    ignored: List[tuple] = field(default_factory=list)       # (list, sabab)
    warnings: List[str] = field(default_factory=list)
    works: List[Work] = field(default_factory=list)          # ЛРВ: ishlar (norma shifri bilan)
    lrv_sheets: List[str] = field(default_factory=list)      # o'qilgan ЛРВ listlari

    @property
    def fingerprint(self) -> str:
        """Mazmun izi: nomi boshqa, lekin resurslari, narxlari va ishlari aynan bir xil fayl — bir xil iz."""
        items = sorted(f"{r.key}|{r.unit_key}|{r.price:.2f}" for r in self.rows)
        items += sorted(f"w|{w.shifr}|{w.qty}|" + ",".join(f"{x.code}:{x.norma}:{x.qty}" for x in w.resources)
                        for w in self.works)
        return hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()


@dataclass
class _Block:
    header: int
    data_start: int
    name_col: int
    unit_col: int
    price_col: int
    code_col: Optional[int]
    qty_col: Optional[int]
    numbering_cols: tuple = ()  # «№ п/п», «Т/р» — tartib raqami, kod emas


def _is_unit(t: str) -> bool:
    return bool(_UNIT_RE.search(t) or _UNIT_UZ.search(t))


def _price_col(texts: dict, skip: tuple) -> Optional[int]:
    """Narx ustuni: «цена», «баҳо», «стоимость … на ед.»; «общая», «сумма», «қиймати» — emas. Odatda nomdan
    keyin, lekin namuna RES da nomdan oldin ham bo'ladi."""
    best, best_score = None, 0
    for c in sorted(texts):
        t = texts[c]
        if c in skip or not any(w in t for w in _PRICE_WORDS) or any(w in t.rsplit("|", 1)[-1] for w in _TOTAL_WORDS):
            continue
        score = 3 if ("цена" in t or "баҳо" in t or "бахо" in t or "нарх" in t) else 2 if _PER_UNIT.search(t) else 1
        if score > best_score:
            best, best_score = c, score
    return best


def _block_at(ws, r: int, max_col: int) -> Optional[_Block]:
    texts = [str(cell_value(ws, r, c) or "").lower().replace("ё", "е") for c in range(1, max_col + 1)]
    if not (any(_NAME_RE.search(t) for t in texts) and any(_is_unit(t) for t in texts)):
        return None
    numbering = next((x for x in range(r + 1, min(r + 6, ws.max_row) + 1) if _is_numbering_row(ws, x)), None)
    cols = header_texts(ws, r, (numbering - 1) if numbering else r + 2)
    name_col = next((c for c in sorted(cols) if _NAME_RE.search(cols[c])), None)
    unit_col = next((c for c in sorted(cols) if c != name_col and _is_unit(cols[c])), None)
    if name_col is None or unit_col is None:
        return None
    price = _price_col(cols, (name_col, unit_col))
    if price is None:
        return None
    code = next((c for c in sorted(cols) if c < name_col and re.search(r"шифр|код|обоснован", cols[c])),
                name_col - 1 if name_col > 1 else None)
    qty = next((c for c in sorted(cols) if c > unit_col and re.search(r"количеств|кол-?во", cols[c])), None)
    nums = tuple(c for c in cols if _NUMBERING.match(cols[c]))
    return _Block(r, (numbering or r) + 1, name_col, unit_col, price, code, qty, nums)


def _best_code_col(ws, b: _Block, stop: int) -> Optional[int]:
    """Kod ustuni — nomdan chapdagi, eng ko'p milliy resurs kodi turgan ustun. Sarlavha ishonchsiz: RES.xls da
    «РЕСУРС» (000258) va «ОБОСНОВАНИЕ» (С203-101 — katalog shifri) yonma-yon. Kod topilmasa — sarlavha bo'yicha."""
    best, best_n = b.code_col, 0
    rows = range(b.data_start, min(stop, b.data_start + 400))
    for c in range(1, b.name_col):
        if c in (b.price_col, b.unit_col, b.qty_col) or c in b.numbering_cols:
            continue
        codes = [int(k) for k in (res_code(cell_value(ws, x, c)) for x in rows if cell_value(ws, x, b.unit_col)) if k]
        # sarlavhasiz raqamlash (1, 2, 3 …, bo'lim boshida yana 1) — kod emas
        steps = sum(1 for a, z in zip(codes, codes[1:]) if z in (a + 1, a - 1, 1))
        if len(codes) > 1 and steps >= 0.6 * (len(codes) - 1):
            continue
        if len(codes) > best_n:
            best, best_n = c, len(codes)
    return best


def _text(v) -> str:
    return "" if v is None else re.sub(r"\s+", " ", str(v).replace("\xa0", " ")).strip()


def _row_text(ws, r: int, max_col: int) -> str:
    return " ".join(t for t in (_text(cell_value(ws, r, c)) for c in range(1, max_col + 1)) if t)


def _parse_sheet(ws, out: ResFile):
    max_col = min(ws.max_column, 30)
    blocks = []
    r = 1
    while r <= ws.max_row:
        b = _block_at(ws, r, max_col)
        if b:
            blocks.append(b)
            r = b.data_start
        else:
            r += 1
    norms = sum(1 for b in blocks for x in range(b.data_start, ws.max_row + 1)
                if b.code_col and _NORM_CODE.match(_text(cell_value(ws, x, b.code_col)).upper()))
    if not blocks or norms >= LOCAL_MIN_NORMS:  # narx jadvali emas — ЛРВ (ishlar va normalar) bo'lishi mumkin
        if not _parse_lrv(ws, out, max_col):
            out.ignored.append((ws.title, "нарх устуни бор ресурс жадвали ҳам, норма шифрлари ҳам топилмади"))
        return
    out.sheets.append(ws.title)

    obj = ""
    # blok sarlavhasidan oldingi «объект - …» qatori ham shu blokka tegishli
    for i, b in enumerate(blocks):
        stop = blocks[i + 1].header if i + 1 < len(blocks) else ws.max_row + 1
        b.code_col = _best_code_col(ws, b, stop)
        for x in range(max(1, b.header - 8), b.header):
            m = _OBJECT.match(_row_text(ws, x, max_col))
            if m:
                obj = m.group(1).strip()[:200]
        category = ""
        for x in range(b.data_start, stop):
            name = _text(cell_value(ws, x, b.name_col))
            unit = _text(cell_value(ws, x, b.unit_col))
            raw_code = _text(cell_value(ws, x, b.code_col)) if b.code_col else ""
            line = _row_text(ws, x, max_col)
            if not line:
                continue
            m = _OBJECT.match(line)
            if m:
                obj = m.group(1).strip()[:200]
                continue
            low = line.lower()
            if _TOTAL.match(norm_name(name)) or _TOTAL.match(norm_name(raw_code)) or _TOTAL.match(low) \
                    or unit.lower().strip(".") == "сум":
                out.skipped["total"] += 1
                continue
            if not unit or num(unit) is not None and not name:
                kind = section_kind(line)
                if kind:
                    category = KIND_COLUMN[kind]
                continue
            if not name:
                continue
            if _NAME_RE.search(name.lower()):
                out.skipped["header"] += 1
                continue
            if num(unit) is not None or num(name) is not None:
                out.skipped["bad_unit"] += 1
                continue
            p = cell_value(ws, x, b.price_col)
            price = num(p)
            if price is None:
                out.skipped["no_price"] += 1
                continue
            if price <= 0:
                out.skipped["zero_price"] += 1
                continue
            qty = num(cell_value(ws, x, b.qty_col)) if b.qty_col else None
            out.rows.append(ResRow(ws.title, x, res_code(raw_code), raw_code[:40], name[:500], unit[:40], qty,
                                   round(price, 4), category or KIND_COLUMN[row_kind(name, unit)], obj))


# ===================== ЛРВ: ишлар (норма шифри) ва уларнинг ресурслари =====================
_TOTAL_QTY = re.compile(r"проектн|всего|жами|общ|итого")


def _lrv_name(t: str) -> bool:
    """Nom ustuni sarlavhasi: «Наименование работ и затрат», namunadagi «Иш ва ресурслар»."""
    return bool(_NAME_RE.search(t) or re.search(r"иш ва ресурс|ресурс(?:лар)? номи", t))


def _lrv_cols(ws, r: int, max_col: int) -> Optional[dict]:
    """ЛРВ jadval sarlavhasi: nom, birlik, shifr, «на ед.» (norma) va «по проектным данным» (jami miqdor)."""
    texts = [str(cell_value(ws, r, c) or "").lower().replace("ё", "е") for c in range(1, max_col + 1)]
    if not (any(_lrv_name(t) for t in texts) and any(_is_unit(t) for t in texts)):
        return None
    numbering = next((x for x in range(r + 1, min(r + 6, ws.max_row) + 1) if _is_numbering_row(ws, x)), None)
    cols = header_texts(ws, r, (numbering - 1) if numbering else r + 2)
    name = next((c for c in sorted(cols) if _lrv_name(cols[c])), None)
    unit = next((c for c in sorted(cols) if c != name and _is_unit(cols[c])), None)
    if name is None or unit is None:
        return None
    after = [c for c in sorted(cols) if c > unit and not any(w in cols[c] for w in _PRICE_WORDS)]
    norm = next((c for c in after if _PER_UNIT.search(cols[c])), None)
    total = next((c for c in after if c != norm and _TOTAL_QTY.search(cols[c].rsplit("|", 1)[-1])), None)
    if total is None:
        total = next((c for c in after if c != norm and "количеств" in cols[c]), None)
    coded = [c for c in sorted(cols) if c != name and re.search(r"шифр|код|обоснован", cols[c])]
    code = next((c for c in coded if c < name), coded[0] if coded else (name - 1 if name > 1 else None))
    num_col = next((c for c in sorted(cols) if c < name and _NUMBERING.match(cols[c])), 1)
    return {"header": r, "start": (numbering or r) + 1, "name": name, "unit": unit, "norm": norm, "total": total,
            "code": code, "num": num_col}


def _is_sub(v) -> bool:
    """«1.1», «12.3» — ish ichidagi resurs raqami (Excel'da son 1.1 bo'lib keladi)."""
    if isinstance(v, float):
        return not v.is_integer()
    return isinstance(v, str) and bool(re.match(r"^\d+\.\d+$", v.strip()))


def _parse_lrv(ws, out: ResFile, max_col: int) -> bool:
    heads = []
    r = 1
    while r <= ws.max_row:
        h = _lrv_cols(ws, r, max_col)
        if h and h["code"]:
            heads.append(h)
            r = h["start"]
        else:
            r += 1
    if not heads:
        return False
    found = []
    for i, h in enumerate(heads):
        stop = heads[i + 1]["header"] if i + 1 < len(heads) else ws.max_row + 1
        work, section = None, ""
        for x in range(h["start"], stop):
            name = _text(cell_value(ws, x, h["name"]))
            unit = _text(cell_value(ws, x, h["unit"]))
            raw = _text(cell_value(ws, x, h["code"]))
            line = _row_text(ws, x, max_col)
            if not line:
                continue
            if _TOTAL.match(norm_name(name)) or _TOTAL.match(norm_name(raw)) or _TOTAL.match(line.lower()):
                work = None
                continue
            if not unit:  # «РАЗДЕЛ 2. ЗЕМЛЯНЫЕ РАБОТЫ», «объект - МАФ» — bo'lim
                section, work = line[:300], None
                continue
            if not name or _NAME_RE.search(name.lower()) or num(unit) is not None:
                continue
            norm_v = num(cell_value(ws, x, h["norm"])) if h["norm"] else None
            total_v = num(cell_value(ws, x, h["total"])) if h["total"] else None
            shifr = normbook.parse(raw)
            if shifr:
                work = Work(ws.title, x, shifr[0], raw[:120], shifr[1], name[:500], unit[:40],
                            total_v if total_v is not None else norm_v, section)
                found.append(work)
            elif work is not None:
                sub = _is_sub(cell_value(ws, x, h["num"])) or (norm_v is not None and total_v is not None)
                work.resources.append(WorkRes(x, res_code(raw), raw[:40], name[:500], unit[:40],
                                              norm_v if sub else None, total_v if sub else (total_v or norm_v),
                                              extra=not sub))
    if not found:
        return False
    out.works += found
    out.lrv_sheets.append(ws.title)
    return True


def _dedupe(out: ResFile):
    """Bir faylda bir resurs bir marta (birinchisi) — bir nechta obyekt blokida takrorlanadi."""
    seen, rows = set(), []
    for r in out.rows:
        k = (r.key, r.unit_key)
        if k in seen:
            out.skipped["duplicate"] += 1
            continue
        seen.add(k)
        rows.append(r)
    out.rows = rows


def parse(path, soffice: Optional[str] = None) -> ResFile:
    path = Path(path)
    out = ResFile()
    with tempfile.TemporaryDirectory(prefix="resfile_") as tmp:
        xlsx = convert.ensure_xlsx(path, Path(tmp), soffice)
        wb = load_workbook(xlsx, data_only=True, keep_links=False)
        try:
            for ws in wb.worksheets:
                if ws.sheet_state == "visible" or len(wb.worksheets) == 1:
                    _parse_sheet(ws, out)
        finally:
            wb.close()
    _dedupe(out)
    if not out.rows and not out.works:
        out.warnings.append("Файлда нархли ресурс ҳам, норма шифрли иш ҳам топилмади")
    return out


def file_hash(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":  # pragma: no cover
    for p in sys.argv[1:]:
        res = parse(p)
        print(p, "→", len(res.rows), "ресурс;", dict(res.skipped), "листлар:", res.sheets, "четда:", res.ignored)
        for r in res.rows[:15]:
            print(f"  {r.sheet}!{r.row} {r.category} {r.code or '—':>6} {r.name[:50]:50} {r.unit:10} {r.price:>14,.2f}")
        print("  ЛРВ:", res.lrv_sheets, len(res.works), "иш")
        for w in res.works[:6]:
            print(f"  {w.sheet}!{w.row} {w.book:4} {w.shifr:16} {w.name[:45]:45} {w.unit:12} {w.qty}")
            for x in w.resources[:4]:
                print(f"      {x.code or '—':>6} {x.name[:40]:40} {x.unit:8} норма={x.norma} жами={x.qty}{' +' if x.extra else ''}")

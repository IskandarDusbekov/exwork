"""Ofyorta listi «qanday bo'lsa shunday»: hamma qatorlar va ustunlar fayldagidek saqlanadi va ko'rsatiladi —
bo'limlarga ajratilmaydi, summalar qayta yozilmaydi.

Tizim faqat ikkita narsani biladi:

* **qator turi** (A ustunidagi raqamdan): ish / mustaqil — «12», resurs — «12.3» (yoki «12,3»), qolgani matn;
  oxirgi ish qatoridan keyingisi — svodka;
* **ustunlar** sarlavhadan: «Количество» (E — ish hajmi / resurs me'yori), «по проектным данным» (F — resurs jami).

Dalolatnoma (№1, №2 …) — xuddi shu list, faqat ish hajmlari dalolatnomadagi bilan almashtirilgan: resurs jami
(F) ish hajmiga mutanosib, qolgan hamma katak (summa, turlar, svodka foizlari, QQS) — **faylning o'z formulasi**
bilan qayta hisoblanadi (``formula``). «Хисобот»dagi №1 / №2 / Остатка bloklari shu.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from openpyxl import load_workbook

from .formula import Err, Evaluator, compile_formula, deps, refs

NUM = re.compile(r"^\s*(\d+)(?:\s*[.,]\s*(\d+))?\s*$")  # «3. 1» (bo'shliq bilan) ham
MAX_COLS = 80  # undan o'ngdagi ustunlar (tasodifiy yozuvlar) olinmaydi
PREVIEW = 12
READER = 6  # o'qish qoidalari o'zgarsa oshiriladi — eski obyektlar o'zi qayta o'qiladi


class SheetError(Exception):
    pass


def _text(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""


def _low(v) -> str:
    return _text(v).lower().replace("ё", "е")


def _cell_value(v):
    """Excel qiymati -> JSON: son, matn yoki None (sana — matn)."""
    if v is None or isinstance(v, (int, float, str)):
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and not v.strip():
            return None
        return v
    return str(v)


def item_num(v) -> Optional[Tuple[str, Optional[str]]]:
    """«12» -> ("12", None), «12.3» / «12,3» -> ("12", "3"); boshqasi — None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return str(v), None
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v)), None
        m = NUM.match(repr(v))  # «12.3» son bo'lib yozilgan (12.10 bo'lsa Excel uni 12.1 qilgan — farqlab bo'lmaydi)
        return (m.group(1), m.group(2)) if m else None
    m = NUM.match(str(v))
    return (m.group(1), m.group(2)) if m else None


# ===================== sarlavha va ustunlar =====================
@dataclass
class Layout:
    header: int            # sarlavha qatori (1 dan)
    header_end: int        # sarlavhaning oxirgi qatori («1 2 3 …» raqamlar qatori bilan)
    num: int = 0           # ustun indekslari (0 dan)
    name: int = 2
    unit: int = 3
    qty: int = 4           # E: ish hajmi / resurs me'yori
    total: int = 5         # F: resurs jami (по проектным данным)
    price: int = 6
    amount: int = 7
    width: int = 12        # listdagi ustunlar soni
    block_end: int = 11    # dalolatnoma bloki: qty .. block_end (fayldagi «Хисобот»dagidek)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _is_header(row) -> bool:
    texts = [_low(v) for v in row if v is not None]
    if not any("наименование" in t for t in texts):
        return False
    return any(("единиц" in t or "шифр" in t or "кол" in t) for t in texts)


def _is_numbering(row) -> bool:
    """«1 | 2 | 3 | 4 …» — sarlavha ostidagi ustun raqamlari."""
    vals = [v for v in row if v is not None and _text(v)]
    if len(vals) < 3:
        return False
    try:
        nums = [float(_text(v)) for v in vals]
    except ValueError:
        return False
    return all(n.is_integer() and 0 < n < 100 for n in nums) and nums[:3] == sorted(nums[:3])


def find_layout(rows: List[tuple]) -> Optional[Layout]:
    h = next((i for i, r in enumerate(rows[:200]) if _is_header(r)), None)
    if h is None:
        return None
    lay = Layout(header=h + 1, header_end=h + 1)
    found = {}
    for i in range(h, min(h + 3, len(rows))):
        for j, v in enumerate(rows[i]):
            t = _low(v)
            if not t:
                continue
            for key, needles in (("num", ("№", "n п", "п/п", "п.п")), ("name", ("наименование",)),
                                 ("unit", ("единиц", "ед.изм", "ед. изм", "ед изм", "ед.")),
                                 ("qty", ("количество", "кол-во", "кол.")), ("total", ("по проект",)),
                                 ("price", ("стоимость", "цена")), ("amount", ("сумма",))):
                if key == "unit" and t.startswith("на "):  # «на единицу измерения» — me'yor ustuni, birlik emas
                    continue
                if key not in found and any(n in t for n in needles):
                    found[key] = j
                    break
    for key, j in found.items():
        setattr(lay, key, j)
    if "total" not in found:
        lay.total = lay.qty + 1
    if "num" not in found:
        lay.num = 0
    end = h
    for i in range(h + 1, min(h + 4, len(rows))):
        r = rows[i]
        texts = [_low(v) for v in r if v is not None and _text(v)]
        if _is_numbering(r) or any(k in t for t in texts for k in ("на ед", "на. ед", "по проект", "сумма", "измерения")):
            end = i
        elif texts:
            break
    lay.header_end = end + 1
    return lay


# ===================== listlar (yuklagach tanlash uchun) =====================
_GOOD = ("офер", "оферт", "ofyorta", "oferta", "офёрт")
_BAD = ("ф-2", "ф2", "ф-3", "ф3", "остат", "хисобот", "ҳисобот", "свод", "акт", "f-2", "f-3", "buyurtmachi")


_SVOD_NAME = re.compile(r"^(JAMI\b|ИТОГО\b|ВСЕГО\b|ЗАРПЛАТА|ЗАРАБОТНАЯ|QURUVCHI-ISHCHILAR|ISH\s*HAQQI|ИШ\s*ҲАҚИ)", re.I)
_MANUAL = re.compile(r"\)\s*[-+]\s*\d|[A-Z]\$?\d+\s*[-+]\s*\d+(?:[.,]\d+)?\s*$", re.I)  # «SUM(…)+124,25», «…+H4573-1»
_TOTAL = re.compile(r"итог|всего|jami|жами|ҳаммаси|hammasi|qiymati|қиймати")
_VAT = re.compile(r"ндс|qqs|ққс|ккс")
_SUM = re.compile(r"SUM\(\$?[A-Z]{1,3}\$?(\d+):\$?[A-Z]{1,3}\$?(\d+)\)", re.I)


def svod_start(rows: List[tuple], lay: Layout, formulas: Optional[List[dict]] = None) -> Optional[int]:
    """Svodka boshlanadigan qator (1 dan): ishlar jami — butun jadvalni oladigan SUM (SUM(H11:H1250)) yoki
    «JAMI:», «ЗАРПЛАТА», «… ISH HAQQI» sarlavhasi. Svodka qatorlarida ham A da 1, 2, 3 … bo'lishi mumkin."""
    for i in range(lay.header_end + 1, len(rows) + 1):
        r = rows[i - 1]
        if item_num(r[lay.num] if lay.num < len(r) else None) and _text(r[lay.unit] if lay.unit < len(r) else None):
            continue  # ish qatori o'z resurslarini yig'ishi mumkin («=SUM(H11:H21)») — bu svodka emas
        for f in (formulas[i - 1].values() if formulas and i - 1 < len(formulas) else ()):
            for a, b in _SUM.findall(f):
                a, b = int(a), int(b)
                if b >= i - 3 and a <= lay.header_end + max(20, (i - lay.header_end) // 10) and b - a > 2:
                    return i
        r = rows[i - 1]
        name = _text(r[lay.name]) if lay.name < len(r) else ""
        unit = _text(r[lay.unit]) if lay.unit < len(r) else ""
        if name and _SVOD_NAME.match(name) and unit.upper() not in ("ЧЕЛ.-Ч", "ЧЕЛ-Ч.", "ЧЕЛ.-Ч.", "ЧЕЛ-Ч"):
            return i
    return None


def _classify(rows: List[tuple], lay: Layout, formulas: Optional[List[dict]] = None):
    """Qator turlari: {qator (1 dan): (tur, raqam, ota qator)}. Tur: head | text | work | free | res | svod.
    Svodka ichidagi «12 | Художественный элемент | м² | 1024,7» kabi qator (birligi va hajmi bor) — alohida
    ish (free): dalolatnomada uning hajmi ham yoziladi."""
    start = svod_start(rows, lay, formulas) or len(rows) + 1
    tail = False
    kinds: Dict[int, list] = {}
    last_top: Dict[str, int] = {}
    prev_top = None
    for i, r in enumerate(rows, start=1):
        if i <= lay.header_end:
            kinds[i] = ["head", "", None]
            continue
        cell = lambda j: r[j] if j < len(r) else None  # noqa: E731
        num = item_num(cell(lay.num))
        name = _text(cell(lay.name))
        if i >= start and (tail or (i > start and _is_header(r))):  # keyingi jadval (Ф-2 nusxasi) — ofyorta tugadi
            tail = True
            kinds[i] = ["text", "", None]
            continue
        if num is None and _unnumbered_item(r, lay, formulas[i - 1] if formulas and i - 1 < len(formulas) else {}, i):
            kinds[i] = ["free", "", None]  # raqami yozilmagan material («ГОТОВАЯ ПЕРЕКЛАДИНА | ШТ | 2 | … =E*G»)
            continue
        if i >= start:
            # bo'limlar ketma-ket: har birining o'z svodkasi, keyin yana ishlar (raqamlash 1 dan) — ular ham ish
            if not _is_item(r, lay, num, name, formulas[i - 1] if formulas and i - 1 < len(formulas) else {}, i):
                kinds[i] = ["svod", "", None]
                continue
        if num is None or not name or name.replace(".", "").isdigit():
            kinds[i] = ["text", "", None]
            continue
        top, sub = num
        if sub is None:
            kinds[i] = ["free", top, None]
            last_top[top] = i
            prev_top = i
            continue
        parent = prev_top if prev_top and kinds[prev_top][1] == top else None if i >= start else last_top.get(top)
        if parent is None:  # tepasida ishi yo'q resurs — alohida material sifatida
            kinds[i] = ["free", f"{top}.{sub}", None]
            continue
        kinds[i] = ["res", f"{top}.{sub}", parent]
        kinds[parent][0] = "work"
    last_item = max((i for i, k in kinds.items() if k[0] in ("work", "free", "res") and i < start), default=0)
    for i, k in kinds.items():
        if max(last_item, lay.header_end) < i < start:
            k[0] = "svod"
    last_item = max((i for i, k in kinds.items() if k[0] in ("work", "free", "res")), default=0)
    return kinds, last_item


_MONEY_UNITS = ("СУМ", "СЎМ", "SO'M", "SUM", "РУБ", "%")
# ish shifri (me'yor kodi): «E1605-001-01», «Е47-2-99-1», «E22-8-1-3 ШНК.ДОП.3», «Ц1803-001-04ДОП.14» — bitta-ikki harf,
# keyin «raqam-raqam(-raqam)». Resurs kodi faqat raqam («000001», «43113»), material — «С», «ЦЕНА», «СЦЕНА-72».
_SHIFR = re.compile(r"^\s*[A-ZА-ЯЁ]{1,2}\s?\d{1,5}(?:\s?[-–.]\s?\d{1,4}){1,4}", re.I)


def _shifr(r, lay: Layout) -> bool:
    """Qatorda ish shifri bormi — raqam va nom ustunlari orasida (odatda B)."""
    for j in range(lay.num + 1, min(lay.name, len(r))):
        if _SHIFR.match(_text(r[j])):
            return True
    return False


def _unnumbered_item(r, lay: Layout, fmls: dict, i: int) -> bool:
    """A da raqami yo'q, lekin ish / material: nomi, birligi, hajmi va shu qatordagi «hajm × narx» formulasi bor."""
    from .formula import col_index
    name = _text(r[lay.name] if lay.name < len(r) else None)
    if not name or _SVOD_NAME.match(name) or not _is_item(r, lay, ("", None), name):
        return False
    return _qty_formula(fmls, lay, i)


def _qty_formula(fmls: dict, lay: Layout, i: int) -> bool:
    """Shu qatorda «hajm × narx» formulasi bormi (``=E14*G14``, ``=IF(F14="",E14*G14,F14*G14)``)."""
    from .formula import col_index
    qcols = {lay.qty, lay.total}
    for f in (fmls or {}).values():
        if "*" in f:
            cols = {col_index(c) for c, rr in _REF.findall(f.upper()) if int(rr) == i}
            if cols & qcols and cols - qcols:
                return True
    return False


def _is_item(r, lay: Layout, num, name: str, fmls: dict = None, i: int = 0) -> bool:
    """Svodkadan keyingi qator ish / resursmi: raqami, nomi, birligi (pul / foiz emas) va E yoki F da soni bor.
    Birligi yozilmagan bo'lsa — shu qatordagi «hajm × narx» formulasi bo'yicha (``fmls`` berilganda)."""
    if num is None or not name or _SVOD_NAME.match(name):
        return False
    if num[0] and _shifr(r, lay):
        return True  # me'yor shifri bor — aniq ish (birligi yozilmagan bo'lsa ham)
    unit = _text(r[lay.unit] if lay.unit < len(r) else None).upper()
    if not unit and not (num[0] and _qty_formula(fmls, lay, i)):
        return False
    if unit in _MONEY_UNITS or unit.replace(".", "").replace(",", "").isdigit():
        return False
    return any(_num(r[j], text=True) is not None for j in (lay.qty, lay.total) if j < len(r))


_REF = re.compile(r"\$?([A-Z]{1,3})\$?(\d+)")


def _money_cols(values: List[tuple], formulas: List[dict], kinds: dict, lay: Layout):
    """Narx va summa ustunlari ish qatorlarining o'zidan: sarlavha har xil («Цена | Стоимость», «сумма | обший»),
    lekin summa doim «hajm × narx». Formula bo'lsa — «E*G» / «IF(F="",E*G,F*G)» qaysi ustunda turgani; formulasiz
    (qiymat qilib ko'chirilgan) fayl — qaysi ustun aniq «hajm × boshqa ustun» ga teng. Topilmasa — sarlavhadagidek."""
    from collections import Counter

    from .formula import col_index
    qcols = {lay.qty, lay.total}
    amount, price, flat = Counter(), Counter(), Counter()
    rows = [i for i, k in kinds.items() if k[0] in ("work", "free", "res")][:3000]
    for i in rows:
        for j, f in (formulas[i - 1].items() if i - 1 < len(formulas) else ()):
            if j in qcols or "*" not in f:
                continue
            refs = {col_index(c) for c, r in _REF.findall(f.upper()) if int(r) == i}
            if lay.total != lay.qty and refs == qcols:
                flat[j] += 1  # «D*E»: ikkinchi «hajm» ustuni aslida narx (oddiy narx ro'yxati / Ф-2)
            elif refs & qcols and refs - qcols - {j}:
                amount[j] += 1
                for k in refs - qcols - {j}:
                    price[(j, k)] += 1
    if flat and sum(flat.values()) > sum(amount.values()):
        j, n = flat.most_common(1)[0]
        if n >= 3:
            # sarlavhadagi summa o'ngroqda va o'zi ham formula bo'lsa («F*12%», «G+F» — QQS bilan) — o'sha qoladi
            fml = sum(1 for i in rows if i - 1 < len(formulas) and lay.amount in formulas[i - 1])
            if not (lay.amount > j and fml >= n):
                lay.amount = j
            lay.price, lay.total = lay.total, lay.qty
        return
    if not amount:
        for i in rows:
            r = values[i - 1]
            g = lambda j: _num(r[j], text=True) if j < len(r) else None  # noqa: E731
            q = g(lay.total) if kinds[i][0] == "res" or not g(lay.qty) else g(lay.qty)
            if not q:
                continue
            for k in range(lay.total + 1, min(len(r), lay.total + 6)):
                p = g(k)
                if p is None or p == 0:
                    continue
                for j in range(k + 1, min(len(r), k + 4)):
                    a = g(j)
                    if a is not None and abs(a - p * q) <= max(0.01, abs(a) * 1e-6):
                        amount[j] += 1
                        price[(j, k)] += 1
    if not amount:
        return
    j, n = amount.most_common(1)[0]
    if n < 3:
        return
    lay.amount = j
    k = max((pk for pk in price if pk[0] == j), key=lambda pk: price[pk], default=None)
    if k:
        lay.price = k[1]


def list_sheets(path: Path) -> List[dict]:
    """Har bir list: nomi, qatorlar, ishlar soni, boshidagi ishlar (ko'rib tanlash uchun) va «ofyorta bo'lishi mumkin»
    bahosi. Eng yuqori bahoga ega bo'lgani — taklif (foydalanuvchi tasdiqlaydi yoki boshqasini tanlaydi)."""
    wb = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    out = []
    try:
        for ws in wb.worksheets:
            if getattr(ws, "sheet_state", "visible") != "visible" and len(wb.worksheets) > 1:
                hidden = True
            else:
                hidden = False
            rows = [tuple(r[:MAX_COLS]) for r in ws.iter_rows(values_only=True)]
            lay = find_layout(rows)
            info = {"name": ws.title, "rows": len(rows), "items": 0, "works": 0, "preview": [], "hidden": hidden,
                    "header": bool(lay)}
            if lay:
                kinds, _last = _classify(rows, lay)
                info["items"] = sum(1 for k in kinds.values() if k[0] in ("work", "free"))
                info["works"] = sum(1 for k in kinds.values() if k[0] == "work")
                info["shifr"] = sum(1 for i, k in kinds.items() if k[0] in ("work", "free") and _shifr(rows[i - 1], lay))
                # narxlanganmi: ish / resurs qatorlarida hajm ustunlaridan o'ngda pul soni bor («ЕДИН» kabi narxsiz
                # lokal smetada faqat hajm — ofyorta emas)
                info["priced"] = any(_num(rows[i - 1][j], text=True) for i, k in kinds.items()
                                     if k[0] in ("work", "free", "res")
                                     for j in range(max(lay.qty, lay.total) + 1, len(rows[i - 1])))
                for i, k in kinds.items():
                    if k[0] in ("work", "free") and len(info["preview"]) < PREVIEW:
                        r = rows[i - 1]
                        g = lambda j: _cell_value(r[j]) if j < len(r) else None  # noqa: E731
                        info["preview"].append([k[1], _text(g(lay.name))[:140], _text(g(lay.unit)), g(lay.qty)])
            low = ws.title.lower().replace("ё", "е")
            # resurslari bor ish va shifrli qator — ofyortaning asosiy belgisi: «Сводная ресурсная ведомость» (1964 ta
            # resurs, ishi yo'q) ofyortadan (577 ta ish) ustun chiqmasin
            score = info["items"] + 5 * info["works"] + 5 * info.get("shifr", 0) + (10 ** 6 if lay else 0)
            if any(g in low for g in _GOOD):
                score += 10 ** 5
            if any(b in low for b in _BAD):
                score -= 5 * 10 ** 5
            if hidden:
                score -= 10 ** 6
            if lay and not info.get("priced"):
                score -= 9 * 10 ** 5
            info["score"] = score
            out.append(info)
    finally:
        wb.close()
    if out:
        best = max(out, key=lambda s: s["score"])
        for s in out:
            s["guess"] = s is best and s["header"] and s["items"] > 0
    return out


# ===================== listni o'qish =====================
@dataclass
class Grid:
    sheet: str
    layout: Layout
    rows: List[dict] = field(default_factory=list)  # {"row", "kind", "num", "parent", "vals", "fmls"}
    warnings: List[dict] = field(default_factory=list)
    title: str = ""


def read(path: Path, sheet: str, progress: Callable[[int, int], None] = None) -> Grid:
    """Listni ikki marta o'qiydi: qiymatlar (Excel saqlagan) va formulalar. Hech narsa o'zgartirilmaydi."""
    wb = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        if sheet not in wb.sheetnames:
            raise SheetError(f"Файлда «{sheet}» листи йўқ")
        ws = wb[sheet]
        total = ws.max_row or 0
        values = []
        for i, r in enumerate(ws.iter_rows(values_only=True), start=1):
            values.append(tuple(_cell_value(v) for v in r[:MAX_COLS]))
            if progress and i % 500 == 0:
                progress(i, total)
    finally:
        wb.close()
    wb = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        formulas = []
        for r in wb[sheet].iter_rows(values_only=True):
            formulas.append({j: v for j, v in enumerate(r[:MAX_COLS]) if isinstance(v, str) and v.startswith("=")})
    finally:
        wb.close()
    lay = find_layout(values)
    if lay is None:
        raise SheetError("Бу листда офёрта жадвали топилмади: «Наименование работ…» сарлавҳали қатор йўқ")
    kinds, last_item = _classify(values, lay, formulas)
    _money_cols(values, formulas, kinds, lay)
    if not last_item:
        raise SheetError("Листда иш қатори топилмади (А устунида 1, 2, 3 … ва 1.1, 1.2 … рақамлар, ёнида номи)")
    # bo'sh oxirgi qatorlar va ustunlar
    while values and not any(v is not None for v in values[-1]) and not (formulas and formulas[len(values) - 1]):
        values.pop()
    width = max((max((j for j, v in enumerate(r) if v is not None), default=-1) for r in values), default=-1) + 1
    width = max(width, max((max(f, default=-1) for f in formulas[:len(values)]), default=-1) + 1)
    lay.width = max(width, lay.qty + 1)
    item_cols = [j for i, r in enumerate(values, start=1) if kinds[i][0] in ("work", "free", "res")
                 for j, v in enumerate(r) if v is not None] + \
                [j for i in range(1, len(values) + 1) if kinds[i][0] in ("work", "free", "res") for j in formulas[i - 1]]
    lay.block_end = max([lay.amount, lay.total] + item_cols)
    grid = Grid(sheet=sheet, layout=lay)
    for i, r in enumerate(values, start=1):
        k = kinds[i]
        vals = list(r[:lay.width]) + [None] * (lay.width - len(r))
        while vals and vals[-1] is None:
            vals.pop()
        fmls = {j: f for j, f in formulas[i - 1].items() if j < lay.width} if i - 1 < len(formulas) else {}
        grid.rows.append({"row": i, "kind": k[0], "num": k[1], "parent": k[2], "vals": vals, "fmls": fmls})
    _restore_formulas(grid)
    _check(grid)
    _fix_cache(grid)
    texts = [_text(v) for r in values[:lay.header - 1] for v in r if _text(v)]
    grid.title = next((t for t in texts if len(t) > 15 and not t.startswith("(")), "")
    return grid


def _restore_formulas(grid: Grid):
    """Formulasiz fayl (qiymat sifatida ko'chirilgan): ish qatorlaridagi summa va tur ustunlari dalolatnomada qayta
    hisoblanishi uchun formula tiklanadi — faqat qiymat aniq shunga teng bo'lsa (narx × miqdor; tur = summa)."""
    from .formula import col_letter
    lay = grid.layout
    A = col_letter
    for row in grid.rows:
        if row["kind"] not in ("work", "free", "res"):
            continue
        v, f, r = row["vals"], row["fmls"], row["row"]
        get = lambda j: v[j] if j < len(v) and isinstance(v[j], (int, float)) and not isinstance(v[j], bool) else None  # noqa: E731
        amount = get(lay.amount)
        if amount is not None and lay.amount not in f:
            base = lay.total if row["kind"] == "res" else lay.qty
            price, q = get(lay.price), get(base)
            if price is not None and q is not None and abs(price * q - amount) <= max(0.5, abs(amount) * 1e-6):  # faylda yaxlitlangan
                f[lay.amount] = f"={A(lay.price)}{r}*{A(base)}{r}"
        if amount is None and lay.amount not in f:
            continue
        for j in range(lay.amount + 1, lay.block_end + 1):
            x = get(j)
            if j not in f and x is not None and x != 0 and amount is not None and abs(x - amount) <= max(0.01, abs(amount) * 1e-9):
                f[j] = f"={A(lay.amount)}{r}"
            # tur ustuni summa o'rniga narxni olgan («=G558», hajm 1 bo'lgani uchun faylda teng) — summaga bog'laymiz
            elif j in f and amount is not None and x is not None and abs(x - amount) <= max(0.01, abs(amount) * 1e-9) \
                    and re.fullmatch(rf"=\+?\$?{A(lay.price)}\$?{r}", f[j].replace(" ", ""), re.I):
                f[j] = f"={A(lay.amount)}{r}"
        if row["kind"] == "res" and lay.total not in f:
            pass  # resurs jami — dalolatnomada ish hajmiga mutanosib (``Book.override``)
    _relink_svod(grid)


def _relink_svod(grid: Grid):
    """Svodkada qo'lda ko'chirilgan son (tepadagi «SUM(I16:I190)» natijasi qiymat bo'lib qo'yilgan) — dalolatnomada
    o'zgarmay qolib, hajm 0 bo'lsa ham pul chiqaradi. Tepadagi 1–2 qatorda shu ustunda formula bo'lsa va son unga
    teng bo'lsa (yaxlitlash farqi bilan), son o'sha katakka havola qilinadi."""
    from .formula import col_letter
    lay = grid.layout
    rows = {r["row"]: r for r in grid.rows}
    for row in grid.rows:
        if row["kind"] != "svod":
            continue
        v, f, r = row["vals"], row["fmls"], row["row"]
        for j in range(lay.amount, len(v)):
            x = v[j]
            if j in f or not isinstance(x, (int, float)) or isinstance(x, bool) or abs(x) < 1:
                continue
            for up in (r - 1, r - 2):
                u = rows.get(up)
                if not u or u["kind"] != "svod" or j not in u["fmls"] or "!" in u["fmls"][j]:
                    continue
                y = u["vals"][j] if j < len(u["vals"]) else None
                if isinstance(y, (int, float)) and not isinstance(y, bool) and abs(x - y) <= max(0.01, abs(y) * 1e-6):
                    f[j] = f"={col_letter(j)}{up}"
                    break


_ERR = re.compile(r"^#(VALUE!|NAME\?|REF!|DIV/0!|N/A|NUM!|NULL!)$")


def _fix_cache(grid: Grid):
    """Faylda saqlangan formula natijasi xato (``#VALUE!``, ``#NAME?`` …) — odatda .xls ni o'girgan dastur
    («0,2016» kabi matn-sonni tushunmay) qayta hisoblab buzgan. Ofyortada shu xato ko'rinmasin: o'rniga tizim hisobi
    (shartnoma hajmlari bilan — fayldagi bilan bir xil). Tizim ham hisoblay olmasa — fayldagidek qoladi."""
    bad = [(r, j) for r in grid.rows if r["kind"] != "head" for j in r["fmls"]
           if j < len(r["vals"]) and isinstance(r["vals"][j], str) and _ERR.match(r["vals"][j].strip())]
    if not bad:
        return
    b = Book(grid.layout.as_dict(), grid.rows)
    ev = b.evaluate({t: b.contract_qty(t) for t in b.tops})
    fixed, first = 0, bad[0][0]["vals"][bad[0][1]].strip()
    for r, j in bad:
        v = ev(j, r["row"])
        if isinstance(v, (int, float)) and not isinstance(v, bool) and not isinstance(v, Err):
            r["vals"][j] = v
            fixed += 1
    if fixed:
        grid.warnings.append({"row": bad[0][0]["row"], "title": "Файлда хато қийматлар бор эди",
                              "text": f"{fixed} та катакда файлда «{first}» каби хато "
                                      "сақланган эди (одатда .xls очилганда матн кўринишидаги сонлар туфайли). "
                                      "Тизим уларни формула бўйича қайта ҳисоблади."})


def _check(grid: Grid):
    lay = grid.layout
    svod_f = sum(len(r["fmls"]) for r in grid.rows if r["kind"] == "svod")
    if not svod_f and any(r["kind"] == "svod" and any(v is not None for v in r["vals"]) for r in grid.rows):
        grid.warnings.append({"row": 0, "title": "Сводкада формула йўқ",
                              "text": "Офёрта пастидаги сводка (иш ҳақи, фоизлар, ҚҚС) сон сифатида ёзилган. "
                                      "Далолатномаларда сводка қаторлари ҳисобланмайди — фақат иш қаторлари."})
    manual = [(r["row"], f) for r in grid.rows if r["kind"] == "svod" for f in r["fmls"].values() if _MANUAL.search(f)]
    if manual:
        grid.warnings.append({"row": manual[0][0], "title": "Сводка формуласида қўлда қўшилган сон бор",
                              "text": f"{len(manual)} та формулада ҳисобга қўлда сон қўшилган ёки айрилган (масалан "
                                      f"{manual[0][0]}-қатор: {manual[0][1][:60]}). Тизим уни файлдагидек қолдиради, шунинг учун бу сон "
                                      "ҳар бир далолатномага тушади. Смета тузувчи билан аниқлаштиринг."})
    other = sorted({r["row"] for r in grid.rows for f in r["fmls"].values() if "!" in f})
    if other:
        grid.warnings.append({"row": other[0], "title": "Бошқа листга ҳавола қилувчи формулалар бор",
                              "text": f"{len(other)} та қаторда формула бошқа листдаги катакни олади "
                                      f"(биринчиси {other[0]}-қатор). Бундай катакларда файлда сақланган қиймат олинади (далолатномада ўзгармайди)."})


# ===================== dalolatnoma hisobi =====================
def _num(v, text=False) -> Optional[float]:
    """Son; ``text`` — «0,0028» kabi matn bo'lib yozilgan son ham (Excel uni formulada son deb oladi)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if text and isinstance(v, str):
        try:
            return float(v.replace(" ", "").replace(" ", "").replace(",", "."))
        except ValueError:
            return None
    return None


class Book:
    """Saqlangan list (``RawRow``lar) ustida hisob: dalolatnoma hajmlari bilan hamma formulalar qayta hisoblanadi."""

    def __init__(self, layout: dict, rows: List[dict]):
        self.lay = Layout(**layout)
        self.rows = rows
        self.by_row = {r["row"]: r for r in rows}
        self.values: Dict[Tuple[int, int], object] = {}
        self.formulas: Dict[Tuple[int, int], Callable] = {}
        for r in rows:
            for j, v in enumerate(r["vals"]):
                if v is not None:
                    self.values[(j, r["row"])] = v
            if r["kind"] != "head":  # sarlavhadagi formulalar (sana va h.k.) — fayldagi qiymati
                for j, f in r["fmls"].items():
                    if "!" not in f:  # boshqa varaq / fayl (VLOOKUP('[1]F-6'!…)) — fayldagi qiymati o'zgarmas
                        self.formulas[(int(j), r["row"])] = compile_formula(f)
        self.tops = [r["row"] for r in rows if r["kind"] in ("work", "free")]
        self.children: Dict[int, List[int]] = {}
        for r in rows:
            if r["kind"] == "res":
                self.children.setdefault(r["parent"], []).append(r["row"])
        self.calc_rows = [r["row"] for r in rows if r["kind"] in ("work", "free", "res", "svod")]
        # faylda resurs summasi «narx × me'yor» (E) — «× jami» (F) emas: dalolatnomada E ham hajmga mutanosib
        lay = self.lay
        self.norm_money = set()
        for r in rows:
            f = r["fmls"].get(lay.amount) if r["kind"] == "res" else None
            if f and (lay.qty, r["row"]) not in self.formulas:
                rs = set(refs(f))
                if (lay.qty, r["row"]) in rs and (lay.total, r["row"]) not in rs:
                    self.norm_money.add(r["row"])
        self.svod_rows = [r["row"] for r in rows if r["kind"] == "svod"]

    def contract_qty(self, row: int) -> float:
        """Shartnoma hajmi: E; E bo'sh bo'lsa — F («по проектным данным»): ko'p ofyortada ish hajmi faqat F da yoziladi."""
        lay = self.lay
        e = _num(self.values.get((lay.qty, row)), text=True)
        if e:
            return e
        f = _num(self.values.get((lay.total, row)), text=True)
        return f if f else (e or 0.0)

    def override(self, qty: Dict[int, float]) -> Dict[Tuple[int, int], float]:
        """Ish / mustaqil: E = dalolatnoma hajmi; F da formulasiz son bo'lsa — u ham hajmga mutanosib (summa
        «IF(F="",E*G,F*G)» kabi F dan olinadi). Resurs: F formulasi bo'lsa — o'zi hisoblanadi (E ga tayanadi),
        formulasiz bo'lsa — F × (hajm / shartnoma hajmi), shartnomada 0 bo'lsa — me'yor × hajm."""
        lay, out = self.lay, {}
        for top in self.tops:
            q = qty.get(top, 0.0)
            out[(lay.qty, top)] = q
            base = self.contract_qty(top)
            if lay.total != lay.qty and (lay.total, top) not in self.formulas:
                tf = _num(self.values.get((lay.total, top)), text=True)
                if tf is not None:
                    out[(lay.total, top)] = tf * q / base if base else q
            for ch in self.children.get(top, ()):
                norm = _num(self.values.get((lay.qty, ch)), text=True) or 0.0
                if ch in self.norm_money:
                    out[(lay.qty, ch)] = norm * q / base if base else 0.0
                if (lay.total, ch) in self.formulas:
                    continue
                f = _num(self.values.get((lay.total, ch)), text=True)
                out[(lay.total, ch)] = (f * q / base) if (base and f is not None) else norm * q
        return out

    def evaluate(self, qty: Dict[int, float]) -> Evaluator:
        return Evaluator(self.values, self.formulas, self.override(qty)).run_all()

    def block(self, ev: Evaluator, rows=None) -> Dict[int, list]:
        """Blok qiymatlari (E..oxirgi ustun) — ish, resurs va svodka qatorlari uchun; bo'sh qator yuborilmaydi."""
        lay = self.lay
        out = {}
        for r in (rows if rows is not None else self.calc_rows):
            vals = [ev(j, r) for j in range(lay.qty, lay.block_end + 1)]
            if any(v is not None for v in vals):
                while vals and vals[-1] is None:
                    vals.pop()
                out[r] = [_json(v) for v in vals]
        return out

    def fixed_money(self) -> List[dict]:
        """Hajm 0 bo'lsa ham pul chiqadigan ish qatorlari (masalan, resursda «нарх × меъёр» — «× жами» o'rniga):
        fayldagidek hisoblanadi, lekin bu pul har bir dalolatnomaga tushadi — ogohlantirish."""
        ev = self.evaluate({})
        lay, by_top = self.lay, {}
        for r in self.calc_rows:
            row = self.by_row[r]
            if row["kind"] not in ("res", "free", "work"):
                continue
            v = ev(lay.amount, r)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) > 0.005:
                top = row["parent"] if row["kind"] == "res" else r
                by_top.setdefault(top, [0, 0.0])
                by_top[top][0] += 1
                by_top[top][1] += v
        out = []
        for top, (n, s) in by_top.items():
            t = self.by_row[top]
            name = next((str(v) for v in t["vals"][lay.name:lay.name + 1] if v), "")[:70]
            out.append({"row": top, "title": f"{t['num']}-иш «{name}»: ҳажмга боғлиқ бўлмаган {s:,.2f} сўм".replace(",", " "),
                        "text": f"Excel формуласида {n} та қатор суммаси ҳажмга боғланмаган (масалан, «нарх × меъёр», "
                                "«нарх × жами» ўрнига). Тизим файлдагидек ҳисоблайди, шунинг учун бу пул ҳар бир "
                                "далолатномада чиқади. Смета тузувчи билан аниқлаштиринг."})
        return out

    def final_cell(self) -> Optional[Tuple[int, int]]:
        """Ofyortaning jami summasi: svodkada «ИТОГО / ВСЕГО / JAMI …» yozilgan qator (QQS bilangisi afzal); bir nechta
        bo'lim bo'lsa (har birida o'z «Итого с НДС»i) — eng kattasi (umumiy). Nomsiz oxirgi son (tekshiruv ayirmasi,
        ostidagi boshqa jadval) olinmaydi; bunday qator bo'lmasa — None (jami ish qatorlaridan yig'iladi)."""
        if hasattr(self, "_final"):
            return self._final
        lay, best = self.lay, None
        for r in self.svod_rows:
            if not ((lay.amount, r) in self.formulas or _num(self.values.get((lay.amount, r))) is not None):
                continue
            label = " ".join(_low(v) for v in self.by_row[r]["vals"][:lay.amount] if isinstance(v, str))
            if not _TOTAL.search(label):
                continue
            rank = (bool(_VAT.search(label)), _num(self.values.get((lay.amount, r))) or 0.0, r)
            if best is None or rank > best[0]:
                best = (rank, r)
        self._final = (lay.amount, best[1]) if best else None
        return self._final

    def contract_total(self) -> float:
        """Shartnoma bo'yicha jami — to'liq hajm bilan qayta hisoblanadi (bir marta)."""
        if not hasattr(self, "_contract"):
            self._contract = self.grand(self.evaluate({r: self.contract_qty(r) for r in self.tops}))
        return self._contract

    def total_mismatch(self) -> List[dict]:
        """Fayldagi «ВСЕГО» tizim hisobidan farq qilsa — ogohlantirish (odatda svodkada qo'lda yozilgan son)."""
        fc = self.final_cell()
        file_v = _num(self.values.get(fc)) if fc else None
        calc = self.contract_total()
        if file_v is None or abs(file_v - calc) <= max(1.0, abs(file_v) * 1e-7):
            return []
        f = lambda x: f"{x:,.2f}".replace(",", " ")  # noqa: E731
        return [{"row": fc[1], "title": f"Файлдаги жами {f(file_v)}, тизим ҳисоби {f(calc)} (фарқ {f(calc - file_v)})",
                 "text": "Тизим ҳамма формулаларни қайта ҳисоблади. Фарқ одатда сводкада қўлда ёзилган ёки эскирган "
                         "сондан бўлади — смета тузувчи билан текширинг. Далолатномалар тизим ҳисоби бўйича."}]

    def self_check(self) -> dict:
        """O'qilgan ofyortani o'zi tekshiradi — hisob ishonchlimi:
        1) hajmsiz dalolatnoma ≈ 0 (pul hajmga bog'langan); 2) yarim hajm ≈ 50%; 3) to'liq hajm ≈ fayldagi jami.
        Natija: {"ok", "full", "empty", "half", "file", "fails": [{code, row, title, text}]}."""
        f = lambda x: f"{x:,.2f}".replace(",", " ")  # noqa: E731
        full = self.contract_total()
        ev0 = self.evaluate({})
        empty = self.grand(ev0)
        half = self.grand(self.evaluate({r: self.contract_qty(r) / 2 for r in self.tops}))
        fc = self.final_cell()
        file_v = _num(self.values.get(fc)) if fc else None
        fails = []
        if not self.tops or abs(full) < 1:
            fails.append({"code": "zero", "row": 0, "title": "Офёрта жами 0 чиқди",
                          "text": "Тизим иш қаторлари ёки уларнинг суммасини топа олмади (устунлар бошқача жойлашган бўлиши "
                                  "мумкин). Далолатнома суммалари нотўғри бўлади."})
        else:
            if abs(empty) > max(1000.0, abs(full) * 1e-6):
                src = self._empty_sources(ev0)
                where = ", ".join(f"{r}-қатор" for r, _v in src[:3])
                fails.append({"code": "empty", "row": src[0][0] if src else 0,
                              "title": f"Ҳажм ёзилмаса ҳам {f(empty)} сўм чиқяпти",
                              "text": "Файлда айрим суммалар ҳажмга боғланмаган (формула ўрнига сон ёки бошқа устунга ҳавола)"
                                      + (f": {where}" if where else "") + ". Бу пул ҳар бир далолатномага тушади."})
            ratio = half / full
            if abs(ratio - 0.5) > 0.002:
                fails.append({"code": "half", "row": 0, "title": f"Ярим ҳажмда жами {ratio * 100:.1f}% чиқди (50% бўлиши керак)",
                              "text": "Суммаларнинг бир қисми ҳажмга мутаносиб эмас — далолатнома фоизи нотўғри кўрсатилади."})
            if file_v is not None and abs(file_v - full) > max(1000.0, abs(file_v) * 1e-3):
                fails.append({"code": "file", "row": fc[1], "title": f"Тизим ҳисоби {f(full)}, файлда {f(file_v)}",
                              "text": "Тўлиқ ҳажмда ҳам жами файлдагидан фарқ қиляпти — формулаларнинг бир қисми тушунилмаган."})
        return {"ok": not fails, "full": full, "empty": empty, "half": half, "file": file_v, "fails": fails}

    def _empty_sources(self, ev0: Evaluator) -> List[Tuple[int, float]]:
        """Hajmsiz dalolatnomada jamiga pul olib keladigan boshlang'ich kataklar (qatori, summasi): jami katakdan
        orqaga — qiymati bor havolalar bo'ylab — hisobga tayanmaydigan son yoki formulagacha."""
        fc = self.final_cell()
        start = [fc] if fc else [(self.lay.amount, r) for r in self.tops]
        seen, stack, out = set(), list(start), {}
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) >= 0.5  # noqa: E731
        while stack and len(seen) < 200000:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            v = ev0(*key)
            if not num(v):
                continue
            row = self.by_row.get(key[1])
            fm = row["fmls"].get(key[0]) if row else None
            ds = [d for d in deps(fm) if d not in seen] if fm else []
            nonzero = [d for d in ds if num(ev0(*d))]
            if fm is None or not deps(fm) or not nonzero and not any(isinstance(ev0(*d), str) for d in ds):
                out[key[1]] = max(out.get(key[1], 0.0), abs(v))
            stack.extend(nonzero)
        return sorted(out.items(), key=lambda kv: -kv[1])

    def grand(self, ev: Evaluator) -> float:
        """Jami summa: svodkadagi «ВСЕГО» katagi; u bo'lmasa — ish qatorlari summasi (ish qatori o'z resurslari
        yig'indisini takrorlasa — bir marta)."""
        fc = self.final_cell()
        if fc:
            v = ev(*fc)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
        amt = lambda r: (lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0)(ev(self.lay.amount, r))  # noqa: E731
        total = 0.0
        for top in self.tops:
            own, kids = amt(top), sum(amt(c) for c in self.children.get(top, ()))
            total += kids + (0.0 if kids and abs(own - kids) <= max(0.5, abs(kids) * 1e-9) else own)
        return total


def _json(v):
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return "#NUM!"
        return v
    if isinstance(v, (int, str, bool)) or v is None:
        return v
    return str(v)


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

from .formula import Evaluator, compile_formula

NUM = re.compile(r"^\s*(\d+)(?:[.,](\d+))?\s*$")
MAX_COLS = 80  # undan o'ngdagi ustunlar (tasodifiy yozuvlar) olinmaydi
PREVIEW = 12
READER = 2  # o'qish qoidalari o'zgarsa oshiriladi — eski obyektlar o'zi qayta o'qiladi


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
            for key, needles in (("num", ("№", "n п", "п/п", "п.п")), ("name", ("наименование",)), ("unit", ("единиц",)),
                                 ("qty", ("количество",)), ("total", ("по проект",)), ("price", ("стоимость", "цена")),
                                 ("amount", ("сумма",))):
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
_SUM = re.compile(r"SUM\(\$?[A-Z]{1,3}\$?(\d+):\$?[A-Z]{1,3}\$?(\d+)\)", re.I)


def svod_start(rows: List[tuple], lay: Layout, formulas: Optional[List[dict]] = None) -> Optional[int]:
    """Svodka boshlanadigan qator (1 dan): ishlar jami — butun jadvalni oladigan SUM (SUM(H11:H1250)) yoki
    «JAMI:», «ЗАРПЛАТА», «… ISH HAQQI» sarlavhasi. Svodka qatorlarida ham A da 1, 2, 3 … bo'lishi mumkin."""
    for i in range(lay.header_end + 1, len(rows) + 1):
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
        if i >= start:
            q = cell(lay.qty)
            extra = num and num[1] is None and _text(cell(lay.unit)) and isinstance(q, (int, float)) and not isinstance(q, bool)
            kinds[i] = ["free", num[0], None] if extra else ["svod", "", None]
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
        parent = prev_top if prev_top and kinds[prev_top][1] == top else last_top.get(top)
        if parent is None:  # tepasida ishi yo'q resurs — alohida material sifatida
            kinds[i] = ["free", f"{top}.{sub}", None]
            continue
        kinds[i] = ["res", f"{top}.{sub}", parent]
        kinds[parent][0] = "work"
    last_item = max((i for i, k in kinds.items() if k[0] in ("work", "free", "res") and i < start), default=0)
    for i, k in kinds.items():
        if last_item < i < start:
            k[0] = "svod"
    return kinds, last_item


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
                for i, k in kinds.items():
                    if k[0] in ("work", "free") and len(info["preview"]) < PREVIEW:
                        r = rows[i - 1]
                        g = lambda j: _cell_value(r[j]) if j < len(r) else None  # noqa: E731
                        info["preview"].append([k[1], _text(g(lay.name))[:140], _text(g(lay.unit)), g(lay.qty)])
            low = ws.title.lower().replace("ё", "е")
            score = info["items"] + (10 ** 6 if lay else 0)
            if any(g in low for g in _GOOD):
                score += 10 ** 5
            if any(b in low for b in _BAD):
                score -= 5 * 10 ** 5
            if hidden:
                score -= 10 ** 6
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
            if price is not None and q is not None and abs(price * q - amount) <= max(0.01, abs(amount) * 1e-9):
                f[lay.amount] = f"={A(lay.price)}{r}*{A(base)}{r}"
        if amount is None and lay.amount not in f:
            continue
        for j in range(lay.amount + 1, lay.block_end + 1):
            x = get(j)
            if j not in f and x is not None and x != 0 and amount is not None and abs(x - amount) <= max(0.01, abs(amount) * 1e-9):
                f[j] = f"={A(lay.amount)}{r}"
        if row["kind"] == "res" and lay.total not in f:
            pass  # resurs jami — dalolatnomada ish hajmiga mutanosib (``Book.override``)


def _check(grid: Grid):
    lay = grid.layout
    svod_f = sum(len(r["fmls"]) for r in grid.rows if r["kind"] == "svod")
    if not svod_f and any(r["kind"] == "svod" and any(v is not None for v in r["vals"]) for r in grid.rows):
        grid.warnings.append({"row": 0, "title": "Сводкада формула йўқ",
                              "text": "Офёрта пастидаги сводка (иш ҳақи, фоизлар, ҚҚС) сон сифатида ёзилган. "
                                      "Далолатномаларда сводка қаторлари ҳисобланмайди — фақат иш қаторлари."})
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
        self.svod_rows = [r["row"] for r in rows if r["kind"] == "svod"]

    def contract_qty(self, row: int) -> float:
        return _num(self.values.get((self.lay.qty, row)), text=True) or 0.0

    def override(self, qty: Dict[int, float]) -> Dict[Tuple[int, int], float]:
        """Ish / mustaqil: E = dalolatnoma hajmi. Resurs: F formulasi bo'lsa — o'zi hisoblanadi (E ga tayanadi),
        formulasiz bo'lsa — F × (hajm / shartnoma hajmi), shartnomada 0 bo'lsa — me'yor × hajm."""
        lay, out = self.lay, {}
        for top in self.tops:
            q = qty.get(top, 0.0)
            out[(lay.qty, top)] = q
            base = self.contract_qty(top)
            for ch in self.children.get(top, ()):
                if (lay.total, ch) in self.formulas:
                    continue
                f = _num(self.values.get((lay.total, ch)), text=True)
                norm = _num(self.values.get((lay.qty, ch)), text=True) or 0.0
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
        """Svodkaning oxirgi summasi («ВСЕГО» / «Ob'ekt qiymati QQS bilan»): summa ustunidagi oxirgi son."""
        lay = self.lay
        for r in reversed(self.svod_rows):
            if (lay.amount, r) in self.formulas or _num(self.values.get((lay.amount, r))) is not None:
                return lay.amount, r  # keshda qiymati yo'q formula ham (Excel'siz saqlangan fayl)
        return None


def _json(v):
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return "#NUM!"
        return v
    if isinstance(v, (int, str, bool)) or v is None:
        return v
    return str(v)


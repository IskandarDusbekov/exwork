"""Svodka bloki (reja §2.9) va uning summalarini Python'da hisoblash.

Summalar yozilgan formulalarning o'zini Excel qoidalari bo'yicha baholab topiladi:
bo'sh katak = 0, raqam bo'lmagan matn arifmetikada ``#VALUE!``, SUM matnni o'tkazib
yuboradi, xato esa yuqoriga uzatiladi. Shu tufayli natija Excel ko'rsatadigani bilan bir xil.
"""
import re
from typing import Callable, Dict, List, Optional, Tuple, Union

from openpyxl.utils import column_index_from_string, get_column_letter

from .writer import PERCENT_FMT, SheetWriter

# (kalit, C ustundagi matn, D foiz yoki None)
SVODKA_ROWS: List[Tuple[str, str, Optional[float]]] = [
    ("zarplata", "Зарплата", None),
    ("mashina", "Эксплуата-ция машин и механизм", None),
    ("materialy", "Строитель-ные материалы и конструкции", None),
    ("transport_mat", "Трансп. услуги из мат.", 0.03),
    ("kabel", "Кабельно-проводни-ковый продукции", None),
    ("transport_kabel", "Трансп. услуги кабельн. прод.", 0.02),
    ("oborudovanie", "Оборудова ния", None),
    ("transport_obor", "Трансп. услуги обор.", 0.02),
    ("pryamye", "Всего прямых  затрат", None),
    ("prochie", "Прочие затраты подрядчикa", 0.19),
    ("strahovanie", "Затраты на страхов. объекта", 0.0032),
    ("bez_nds", "Всего стоимость без НДС", None),
    ("s_nds", "Всего с НДС      12 %", 0.12),
]

# Standart foizlar (eski dasturdagi). Smart rejimda o'zgartirish mumkin; kalit = svodka qatori.
DEFAULT_PERCENTS: Dict[str, float] = {key: pct for key, _text, pct in SVODKA_ROWS if pct is not None}
PERCENT_LABELS: Dict[str, str] = {
    "transport_mat": "Трансп. услуги из мат.",
    "transport_kabel": "Трансп. услуги кабельн. прод.",
    "transport_obor": "Трансп. услуги обор.",
    "prochie": "Прочие затраты подрядчика",
    "strahovanie": "Затраты на страхов. объекта",
    "s_nds": "НДС",
}


def resolve_percents(percents: Optional[Dict[str, float]]) -> Dict[str, float]:
    """Standartlar + berilganlari (ulush: 0.19 = 19%). Noto'g'ri kalit yoki qiymat — ValueError."""
    out = dict(DEFAULT_PERCENTS)
    for key, value in (percents or {}).items():
        if key not in out:
            raise ValueError(f"Noma'lum foiz: {key}. Bor foizlar: {', '.join(out)}")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            raise ValueError(f"{key}: foiz 0 dan 100% gacha bo'lishi kerak, berildi {value!r}")
        out[key] = float(value)
    return out


def percent_text(fraction: float) -> str:
    """0.12 -> «12», 0.125 -> «12,5» (svodka matni uchun)."""
    v = round(fraction * 100, 4)
    return (f"{v:.4f}".rstrip("0").rstrip(".")).replace(".", ",")


def svodka_layout(extra: Optional[List[Tuple[str, str]]] = None) -> List[Tuple[str, str, Optional[float]]]:
    """Svodka qatorlari. ``extra`` — smetachi qo'shgan ustunlar ``[(harf, nom)]``: ular
    «Всего прямых затрат» dan oldin alohida qator bo'lib tushadi (kalit ``extra_N``)."""
    rows = list(SVODKA_ROWS)
    i = next(k for k, (key, _t, _p) in enumerate(rows) if key == "pryamye")
    return rows[:i] + [(f"extra_{col}", name or f"Устун {col}", None) for col, name in (extra or [])] + rows[i:]


def svodka_formulas(s: int, sum_row: int, nds_from_cell: bool = False,
                    extra: Optional[List[Tuple[str, str]]] = None) -> List[str]:
    """``nds_from_cell`` (smart): НДС D katakdan olinadi — Excel'da foizni o'zgartirsa ham hisob yangilanadi.
    compat: eski dasturdagidek ``*1.12``. ``extra`` ustunlar «Всего прямых затрат» ga qo'shiladi."""
    if extra:
        n = len(extra)
        base = svodka_formulas(s, sum_row, nds_from_cell)
        p = s + 8 + n  # «Всего прямых затрат»
        return base[:8] + [f"={col}{sum_row}" for col, _name in extra] + [
            f"=SUM(H{s}:H{p - 1})",
            f"=(H{p}-H{s + 7}-H{s + 6})*D{p + 1}",
            f"=(H{p + 1}+H{p})*D{p + 2}",
            f"=H{p + 2}+H{p + 1}+H{p}",
            f"=H{p + 3}*(1+D{p + 4})" if nds_from_cell else f"=H{p + 3}*1.12",
        ]
    return [
        f"=I{sum_row}",
        f"=J{sum_row}",
        f"=K{sum_row}",
        f"=H{s + 2}*D{s + 3}",
        f"=L{sum_row}",
        f"=H{s + 4}*D{s + 5}",
        f"=M{sum_row}",
        f"=H{s + 6}*D{s + 7}",
        f"=SUM(H{s}:H{s + 7})",
        f"=(H{s + 8}-H{s + 7}-H{s + 6})*D{s + 9}",
        f"=(H{s + 9}+H{s + 8})*D{s + 10}",
        f"=H{s + 10}+H{s + 9}+H{s + 8}",
        f"=H{s + 11}*(1+D{s + 12})" if nds_from_cell else f"=H{s + 11}*1.12",
    ]


def write_svodka(w: SheetWriter, start_row: int, sum_row: int,
                 percents: Optional[Dict[str, float]] = None, smart: bool = False,
                 extra: Optional[List[Tuple[str, str]]] = None) -> Dict[str, str]:
    """Blokni yozadi; ``{kalit: H katak manzili}`` qaytaradi.

    compat: foizlar va matnlar eski dasturdagidek. smart: ``percents`` qo'llanadi, НДС formulasi
    D katakka bog'lanadi va «Всего с НДС … %» matnidagi foiz ham mos yoziladi."""
    layout = svodka_layout(extra if smart else None)
    end_row = start_row + len(layout) - 1
    formulas = svodka_formulas(start_row, sum_row, nds_from_cell=smart, extra=extra if smart else None)
    pcts = resolve_percents(percents) if smart else DEFAULT_PERCENTS
    cells = {}
    for i, (key, text, pct) in enumerate(layout):
        r = start_row + i
        if smart and key == "s_nds":
            text = f"Всего с НДС      {percent_text(pcts[key])} %"
        w.set(f"C{r}", text)
        w.bold(f"C{r}")
        if pct is not None:
            w.set(f"D{r}", pcts[key])
            w.bold(f"D{r}")
        w.set(f"H{r}", formulas[i])
        w.bold(f"H{r}")  # НДС va boshqa yakuniy/oraliq qatorlar — summalari ham qalin
        cells[key] = f"H{r}"
    w.number_format("D", start_row, end_row, PERCENT_FMT)
    w.number_format("H", start_row, end_row)
    w.fill(start_row, end_row, "A", "H")
    w.frame(start_row, end_row, "A", "H")
    return cells


# ===================== Excel'ga o'xshash baholovchi =====================
class XlError(str):
    """Excel xato qiymati, masalan ``#VALUE!``."""


VALUE = XlError("#VALUE!")
DIV0 = XlError("#DIV/0!")
_ERROR_CODES = {"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A"}

Number = Union[int, float]
_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(\"(?:[^\"]|\"\")*\")|([A-Z]+[0-9]+)|([A-Z]+)\(|(.))")


def as_error(v) -> Optional[XlError]:
    if isinstance(v, XlError):
        return v
    if isinstance(v, str) and v in _ERROR_CODES:
        return XlError(v)
    return None


# Matn ko'rinishidagi son: «50,2741706», «1 234,5», «4614.23», «12%». Excel arifmetikada
# bunday matnni o'zi songa aylantiradi (rus lokalida vergul — o'nlik ajratgich). Smetalarda
# E/F ustunlari ko'pincha aynan shunday matn bo'lib keladi.
_TEXT_NUMBER = re.compile(r"[+-]?(?:\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,]\d+)?(?:[eE][+-]?\d+)?%?")


def parse_text_number(s: str) -> Optional[float]:
    t = s.strip()
    if not _TEXT_NUMBER.fullmatch(t):
        return None
    pct = t.endswith("%")
    t = t.rstrip("%").replace(" ", "").replace(" ", "").replace(",", ".")
    value = float(t)
    return value / 100 if pct else value


def to_number(v):
    """Arifmetika uchun: bo'sh -> 0, raqamli matn -> son, boshqa matn -> #VALUE!."""
    err = as_error(v)
    if err:
        return err
    if v is None or v == "":
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    n = parse_text_number(str(v))
    return VALUE if n is None else n


class Evaluator:
    """Faqat biz yozadigan formulalar uchun: + - * /, qavslar, IF(x="",a,b), ISNUMBER(x), IFERROR(x,y), SUM(diapazon)."""

    def __init__(self, raw: Callable[[str], object]):
        self.raw = raw  # manzil -> formula ("=...") yoki qiymat
        self._memo: Dict[str, object] = {}

    def value(self, addr: str):
        if addr in self._memo:
            return self._memo[addr]
        self._memo[addr] = XlError("#REF!")  # aylana havolalardan himoya
        raw = self.raw(addr)
        if isinstance(raw, str) and raw.startswith("="):
            result = self._eval_formula(raw[1:])
        else:
            result = as_error(raw) or raw
        self._memo[addr] = result
        return result

    # --- parser ---
    def _eval_formula(self, text: str):
        # Havola orqali boshqa formula ichkarida hisoblanadi: tashqi holatni saqlab qo'yamiz.
        saved = getattr(self, "_toks", None), getattr(self, "_i", 0)
        self._toks = [m for m in _TOKEN.finditer(text) if m.group(0).strip()]
        self._i = 0
        try:
            result = self._comparison()
            if self._i != len(self._toks):
                raise ValueError(f"Formula tushunilmadi: ={text}")
        finally:
            self._toks, self._i = saved
        if result is None:
            return 0  # =A1 bo'sh katakka -> 0
        return result

    def _peek(self) -> Optional[str]:
        if self._i < len(self._toks):
            return self._toks[self._i].group(0).strip()
        return None

    def _take(self):
        tok = self._toks[self._i]
        self._i += 1
        return tok

    def _expect(self, s: str):
        tok = self._take()
        if tok.group(0).strip() != s:
            raise ValueError(f"'{s}' kutilgan edi, '{tok.group(0)}' keldi")

    def _comparison(self):
        left = self._expr()
        if self._peek() == "=":
            self._take()
            right = self._expr()
            for v in (left, right):
                if as_error(v):
                    return as_error(v)
            return _xl_equal(left, right)
        return left

    def _expr(self):
        left = self._term()
        while self._peek() in ("+", "-"):
            op = self._take().group(0).strip()
            right = self._term()
            left = _arith(op, left, right)
        return left

    def _term(self):
        left = self._factor()
        while self._peek() in ("*", "/"):
            op = self._take().group(0).strip()
            right = self._factor()
            left = _arith(op, left, right)
        return left

    def _factor(self):
        tok = self._take()
        num, string, ref, func, other = tok.groups()
        if num is not None:
            return float(num) if any(c in num for c in ".eE") else int(num)
        if string is not None:
            return string[1:-1].replace('""', '"')
        if ref is not None:
            return self.value(ref)
        if func is not None:
            return self._call(func)
        if other == "-":
            return _arith("*", -1, self._factor())
        if other == "(":
            v = self._comparison()
            self._expect(")")
            return v
        raise ValueError(f"Kutilmagan belgi: {tok.group(0)!r}")

    def _call(self, name: str):
        if name == "SUM":
            total: Number = 0
            error: Optional[XlError] = None
            while True:
                first = self._take().group(3)
                if self._peek() == ":":
                    self._take()
                    last = self._take().group(3)
                    values = [self.value(a) for a in _range_addrs(first, last)]
                else:
                    values = [self.value(first)]
                for v in values:
                    err = as_error(v)
                    if err:
                        error = error or err
                    elif isinstance(v, (int, float)) and not isinstance(v, bool):
                        total += v
                if self._peek() == ",":
                    self._take()
                    continue
                self._expect(")")
                return error or total
        if name == "IF":
            cond = self._comparison()
            self._expect(",")
            a = self._comparison()
            self._expect(",")
            b = self._comparison()
            self._expect(")")
            if as_error(cond):
                return as_error(cond)
            if not isinstance(cond, bool):
                cond = to_number(cond)
                if as_error(cond):
                    return cond
            return a if cond else b
        if name == "IFERROR":
            v = self._comparison()
            self._expect(",")
            fallback = self._comparison()
            self._expect(")")
            return fallback if as_error(v) else v
        if name == "ISNUMBER":
            v = self._comparison()
            self._expect(")")
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        raise ValueError(f"Qo'llanmaydigan funksiya: {name}")


def _xl_equal(a, b) -> bool:
    a = "" if a is None else a
    b = "" if b is None else b
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return False


def _arith(op: str, a, b):
    a, b = to_number(a), to_number(b)
    for v in (a, b):
        if as_error(v):
            return v
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if b == 0:
        return DIV0
    return a / b


def _split(addr: str) -> Tuple[int, int]:
    m = re.fullmatch(r"([A-Z]+)(\d+)", addr)
    return column_index_from_string(m.group(1)), int(m.group(2))


def _range_addrs(first: str, last: str) -> List[str]:
    c1, r1 = _split(first)
    c2, r2 = _split(last)
    return [f"{get_column_letter(c)}{r}" for r in range(min(r1, r2), max(r1, r2) + 1)
            for c in range(min(c1, c2), max(c1, c2) + 1)]


def compute_totals(written: Dict[str, object], cached_value: Callable[[str], object],
                   cells: Dict[str, str]) -> Dict[str, object]:
    """Svodka summalari: son yoki ``XlError`` (masalan ``#VALUE!``).

    ``written`` — biz yozgan kataklar, ``cached_value`` — qolgan kataklarning asl
    (Excel saqlagan) qiymati.
    """
    ev = make_evaluator(written, cached_value)
    return {key: ev.value(addr) for key, addr in cells.items()}


def make_evaluator(written: Dict[str, object], cached_value: Callable[[str], object]) -> Evaluator:
    """Biz yozgan kataklar formulasi bilan, qolganlari asl qiymati bilan baholanadi."""
    def raw(addr: str):
        if addr in written:
            return written[addr]
        return cached_value(addr)

    return Evaluator(raw)

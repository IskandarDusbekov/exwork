"""Excel formulalari: ofyortaning o'z formulalarini (H = G × F, I = H, SUM(H11:H1250), H1257 × F1258 …) dalolatnoma
hajmlari bilan qayta hisoblash va Excel'ga yozishda havolalarni ko'chirish.

Faqat smetada uchraydigan narsalar: sonlar, kataklar va oraliqlar, + − × ÷ ^ %, taqqoslash, & va
SUM / ROUND / ROUNDUP / ROUNDDOWN / ABS / MIN / MAX / AVERAGE / INT / IF. Boshqa varaqqa havola yoki noma'lum
funksiya — xato qiymati (``Err``), hisob to'xtamaydi: katakda «#REF!» kabi ko'rinadi.
"""
import math
import re
from typing import Callable, Dict, List, Optional, Tuple


class Err(str):
    """Excel xato qiymati (#REF!, #DIV/0!, #VALUE!, #NAME?)."""


REF, DIV0, VALUE, NAME = Err("#REF!"), Err("#DIV/0!"), Err("#VALUE!"), Err("#NAME?")


def col_index(letters: str) -> int:
    """«A» -> 0, «AA» -> 26."""
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


_CELL = r"\$?[A-Za-z]{1,3}\$?\d+"
_TOKEN = re.compile(
    r"(?P<ws>\s+)"
    r"|(?P<str>\"(?:[^\"]|\"\")*\")"
    r"|(?P<err>#(?:REF!|DIV/0!|N/A|NAME\?|VALUE!|NUM!|NULL!))"
    rf"|(?P<xref>(?:'(?:[^']|'')+'|[A-Za-z_Ѐ-ӿ][\w.Ѐ-ӿ]*)!{_CELL}(?::{_CELL})?)"
    rf"|(?P<range>{_CELL}:{_CELL})(?![\w(])"
    rf"|(?P<cell>{_CELL})(?![\w(])"
    r"|(?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
    r"|(?P<func>[A-Za-z_][\w.]*)(?=\s*\()"
    r"|(?P<bool>TRUE|FALSE)\b"
    r"|(?P<op><>|<=|>=|[-+*/^&%=<>(),;:])"
)
_PARTS = re.compile(r"(\$?)([A-Za-z]{1,3})(\$?)(\d+)")


def tokens(formula: str) -> List[Tuple[str, str, int, int]]:
    """(turi, matni, boshi, oxiri). Formula «=» siz beriladi."""
    out, i = [], 0
    while i < len(formula):
        m = _TOKEN.match(formula, i)
        if not m:
            raise ValueError(f"формула ўқилмади: {formula!r}")
        if m.lastgroup != "ws":
            out.append((m.lastgroup, m.group(), m.start(), m.end()))
        i = m.end()
    return out


def _cell(text: str) -> Tuple[int, int]:
    """«$H$12» -> (7, 12): ustun indeksi (0 dan), qator raqami (Excel'dagidek, 1 dan)."""
    m = _PARTS.fullmatch(text)
    return col_index(m.group(2)), int(m.group(4))


# ===================== hisoblash =====================
Env = Callable[[int, int], object]  # (ustun, qator) -> qiymat


def _n(v):
    """Arifmetika uchun son: bo'sh — 0, mantiqiy — 0/1, sonli matn — son, boshqasi — #VALUE!."""
    if isinstance(v, Err):
        return v
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ".").replace("\xa0", "").replace(" ", ""))
    except ValueError:
        return VALUE


def _arith(op):
    def f(a, b):
        a, b = _n(a), _n(b)
        if isinstance(a, Err):
            return a
        if isinstance(b, Err):
            return b
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return DIV0 if b == 0 else a / b
        try:
            return float(a ** b)
        except (OverflowError, ZeroDivisionError, ValueError):
            return Err("#NUM!")
    return f


def _cmp(op):
    def f(a, b):
        if isinstance(a, Err):
            return a
        if isinstance(b, Err):
            return b
        # bo'sh katak: matn bilan solishtirilsa — "" (F41="" → TRUE), son bilan — 0 (Excel kabi)
        a = ("" if isinstance(b, str) else 0.0) if a is None else a
        b = ("" if isinstance(a, str) else 0.0) if b is None else b
        if isinstance(a, str) and isinstance(b, str):
            a, b = a.lower(), b.lower()  # Excel matnni katta-kichik harfga qaramay solishtiradi
        elif isinstance(a, str) != isinstance(b, str):
            a, b = str(a), str(b)
        return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
    return f


def _flat(args) -> list:
    out = []
    for a in args:
        if isinstance(a, _Range):
            out.extend(a.values())
        else:
            out.append(a)
    return out


def _nums(args):
    """SUM / MIN / MAX uchun: oraliqdagi matn va bo'sh kataklar tashlanadi, xato — o'tadi."""
    vals = []
    for a in args:
        if isinstance(a, _Range):
            for v in a.values():
                if isinstance(v, Err):
                    return v
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    vals.append(float(v))
        else:
            v = _n(a)
            if isinstance(v, Err):
                return v
            vals.append(v)
    return vals


def _round(mode):
    def f(x, digits=0.0):
        x, digits = _n(x), _n(digits)
        if isinstance(x, Err):
            return x
        if isinstance(digits, Err):
            return digits
        k = 10 ** int(digits)
        v = abs(x) * k
        if mode == "round":
            v = math.floor(v + 0.5 + 1e-9)
        elif mode == "up":
            v = math.ceil(v - 1e-9)
        else:
            v = math.floor(v + 1e-9)
        return math.copysign(v / k, x)
    return f


def _agg(fn):
    def f(*args):
        vals = _nums(args)
        if isinstance(vals, Err):
            return vals
        return fn(vals)
    return f


def _if(cond, a=True, b=False):
    if isinstance(cond, Err):
        return cond
    c = _n(cond) if not isinstance(cond, str) else bool(cond)
    if isinstance(c, Err):
        return c
    return a if c else b


FUNCS = {
    "SUM": _agg(sum),
    "MIN": _agg(lambda v: min(v) if v else 0.0),
    "MAX": _agg(lambda v: max(v) if v else 0.0),
    "AVERAGE": _agg(lambda v: sum(v) / len(v) if v else DIV0),
    "ROUND": _round("round"), "ROUNDUP": _round("up"), "ROUNDDOWN": _round("down"),
    "ABS": lambda x: (lambda v: v if isinstance(v, Err) else abs(v))(_n(x)),
    "INT": lambda x: (lambda v: v if isinstance(v, Err) else float(math.floor(v)))(_n(x)),
    "IF": _if,
}


def _subtotal(code, *args):
    """SUBTOTAL(9, M2882:M2959): 1 — o'rtacha, 2 — sonlar soni, 3 — bo'sh emaslar, 4 — max, 5 — min, 9 — yig'indi
    (101…109 — yashirin qatorlarsiz; bu yerda qatorlar yashirilmaydi — bir xil)."""
    c = _n(code)
    if isinstance(c, Err):
        return c
    c = int(c) % 100
    if c == 3:
        return float(sum(1 for v in _flat(args) if v is not None and v != ""))
    vals = _nums(args)
    if isinstance(vals, Err):
        return vals
    fn = {1: lambda v: sum(v) / len(v) if v else DIV0, 2: lambda v: float(len(v)), 4: lambda v: max(v) if v else 0.0,
          5: lambda v: min(v) if v else 0.0, 9: sum}.get(c)
    return fn(vals) if fn else VALUE


FUNCS["SUBTOTAL"] = _subtotal


def _one(v):
    """Bitta katak kutilgan joyga oraliq berilsa — birinchi katagi."""
    if isinstance(v, _Range):
        vals = v.values()
        return vals[0] if vals else None
    return v


def _bool(v):
    """AND / OR / NOT uchun: son — 0 dan farqlimi, mantiqiy — o'zi; matn — #VALUE! (Excel kabi)."""
    if isinstance(v, (Err, bool)):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    t = str(v).strip().upper()
    return True if t == "TRUE" else False if t == "FALSE" else VALUE


def _logic(fn):
    def f(*args):
        vals = []
        for v in _flat(args):
            b = _bool(v)
            if isinstance(b, Err):
                return b
            vals.append(b)
        return fn(vals)
    return f


def _iferror(v, alt=0.0):
    return alt if isinstance(_one(v), Err) else v


def _product(*args):
    vals = _nums(args)
    if isinstance(vals, Err):
        return vals
    return math.prod(vals) if vals else 0.0


def _trunc(x, digits=0.0):
    x, digits = _n(x), _n(digits)
    if isinstance(x, Err):
        return x
    if isinstance(digits, Err):
        return digits
    k = 10 ** int(digits)
    return math.trunc(x * k) / k


FUNCS.update({
    # ofyortalarda uchraydi: «=IF(ISNUMBER(G20),IF(F20="",E20*G20,F20*G20),0)», «=IFERROR(H5/E5,0)»
    "ISNUMBER": lambda v: (lambda x: isinstance(x, (int, float)) and not isinstance(x, bool))(_one(v)),
    "ISTEXT": lambda v: isinstance(_one(v), str) and not isinstance(_one(v), Err),
    "ISBLANK": lambda v: _one(v) is None,
    "ISERROR": lambda v: isinstance(_one(v), Err),
    "ISERR": lambda v: isinstance(_one(v), Err) and _one(v) != "#N/A",
    "IFERROR": _iferror,
    "AND": _logic(lambda v: all(v) if v else VALUE),
    "OR": _logic(lambda v: any(v) if v else VALUE),
    "NOT": lambda v: (lambda b: b if isinstance(b, Err) else not b)(_bool(_one(v))),
    "COUNT": lambda *a: float(sum(1 for v in _flat(a) if isinstance(v, (int, float)) and not isinstance(v, bool))),
    "COUNTA": lambda *a: float(sum(1 for v in _flat(a) if v is not None and v != "")),
    "PRODUCT": _product,
    "VALUE": lambda v: _n(_one(v)),
    "N": lambda v: (lambda x: x if isinstance(x, Err) else float(x) if isinstance(x, (int, float)) else 0.0)(_one(v)),
    "TRUNC": _trunc,
})


class _Range:
    def __init__(self, env: Env, c1, r1, c2, r2):
        self.env, self.c1, self.r1, self.c2, self.r2 = env, min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2)

    def values(self):
        env = self.env
        return [env(c, r) for r in range(self.r1, self.r2 + 1) for c in range(self.c1, self.c2 + 1)]


class _Parser:
    """Tokenlardan yopiq funksiya (closure): ``f(env) -> qiymat``. Ustuvorlik Excel'dagidek."""

    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None, 0, 0)

    def take(self, text=None):
        tok = self.peek()
        if text is not None and tok[1] != text:
            raise ValueError(f"«{text}» кутилган")
        self.i += 1
        return tok

    def parse(self):
        f = self.compare()
        if self.i != len(self.t):
            raise ValueError("ортиқча белги")
        return f

    def compare(self):
        f = self.concat()
        while self.peek()[1] in ("=", "<>", "<", ">", "<=", ">="):
            op = _cmp(self.take()[1])
            g = self.concat()
            f = (lambda f, g, op: lambda e: op(f(e), g(e)))(f, g, op)
        return f

    def concat(self):
        f = self.additive()
        while self.peek()[1] == "&":
            self.take()
            g = self.additive()
            f = (lambda f, g: lambda e: _join(f(e), g(e)))(f, g)
        return f

    def additive(self):
        f = self.term()
        while self.peek()[1] in ("+", "-"):
            op = _arith(self.take()[1])
            g = self.term()
            f = (lambda f, g, op: lambda e: op(f(e), g(e)))(f, g, op)
        return f

    def term(self):
        f = self.power()
        while self.peek()[1] in ("*", "/"):
            op = _arith(self.take()[1])
            g = self.power()
            f = (lambda f, g, op: lambda e: op(f(e), g(e)))(f, g, op)
        return f

    def power(self):
        f = self.unary()
        while self.peek()[1] == "^":
            self.take()
            g = self.unary()
            f = (lambda f, g: lambda e: _arith("^")(f(e), g(e)))(f, g)
        return f

    def unary(self):
        if self.peek()[1] in ("-", "+"):
            neg = self.take()[1] == "-"
            g = self.unary()
            return (lambda e: _arith("-")(0.0, g(e))) if neg else (lambda e: _arith("+")(0.0, g(e)))
        f = self.atom()
        while self.peek()[1] == "%":
            self.take()
            f = (lambda f: lambda e: _arith("/")(f(e), 100.0))(f)
        return f

    def atom(self):
        kind, text, _a, _b = self.take()
        if kind == "num":
            v = float(text)
            return lambda e: v
        if kind == "str":
            v = text[1:-1].replace('""', '"')
            return lambda e: v
        if kind == "bool":
            v = text == "TRUE"
            return lambda e: v
        if kind == "err":
            v = Err(text)
            return lambda e: v
        if kind == "xref":
            return lambda e: REF  # boshqa varaq — hisoblanmaydi
        if kind == "cell":
            c, r = _cell(text)
            return lambda e: e(c, r)
        if kind == "range":
            a, b = text.split(":")
            (c1, r1), (c2, r2) = _cell(a), _cell(b)
            return lambda e: _Range(e, c1, r1, c2, r2)
        if kind == "func":
            name = text.upper().replace("_XLFN.", "")
            self.take("(")
            args = []
            if self.peek()[1] != ")":
                args.append(self.compare())
                while self.peek()[1] in (",", ";"):
                    self.take()
                    args.append(self.compare())
            self.take(")")
            fn = FUNCS.get(name)
            if fn is None:
                return lambda e: NAME
            return (lambda fn, args: lambda e: _call(fn, [a(e) for a in args]))(fn, args)
        if text == "(":
            f = self.compare()
            self.take(")")
            return f
        raise ValueError(f"кутилмаган белги: {text!r}")


def _join(a, b):
    if isinstance(a, Err):
        return a
    if isinstance(b, Err):
        return b
    s = lambda v: "" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))  # noqa: E731
    return s(a) + s(b)


def _call(fn, args):
    try:
        v = fn(*args)
    except TypeError:
        return VALUE
    if isinstance(v, _Range):
        return VALUE
    return v


def compile_formula(formula: str):
    """«=G14*F14» -> ``f(env)``. O'qib bo'lmasa — doim #NAME? qaytaradigan funksiya."""
    body = formula[1:] if formula.startswith("=") else formula
    try:
        f = _Parser(tokens(body)).parse()
    except (ValueError, IndexError, AttributeError):
        return lambda e: NAME

    def run(e):
        v = f(e)
        return VALUE if isinstance(v, _Range) else v
    return run


# ===================== havolalarni ko'chirish (Excel'ga yozishda) =====================
Mover = Callable[[int, int, str], Optional[Tuple[int, int]]]  # (ustun, qator, «cell»|«start»|«end») -> yangi joy


def _ref_text(m, c: int, r: int) -> str:
    return f"{m.group(1)}{col_letter(c)}{m.group(3)}{r}"


def move_refs(formula: str, mover: Mover) -> str:
    """Formuladagi kataklarni ``mover`` bo'yicha ko'chiradi (boshqa varaqqa havola tegilmaydi). ``None`` -> #REF!."""
    body = formula[1:] if formula.startswith("=") else formula
    try:
        toks = tokens(body)
    except ValueError:
        return formula
    out, last = [], 0
    for kind, text, a, b in toks:
        if kind not in ("cell", "range"):
            continue
        out.append(body[last:a])
        if kind == "cell":
            m = _PARTS.fullmatch(text)
            new = mover(*_cell(text), "cell")
            out.append(_ref_text(m, *new) if new else "#REF!")
        else:
            s, e = text.split(":")
            ms, me = _PARTS.fullmatch(s), _PARTS.fullmatch(e)
            ns, ne = mover(*_cell(s), "start"), mover(*_cell(e), "end")
            out.append(f"{_ref_text(ms, *ns)}:{_ref_text(me, *ne)}" if ns and ne else "#REF!")
        last = b
    out.append(body[last:])
    return "=" + "".join(out)


def refs(formula: str) -> List[Tuple[int, int]]:
    """Formuladagi yakka kataklar (oraliqlarsiz) — formulasiz faylda formulani tiklashni tekshirish uchun."""
    try:
        return [_cell(t) for k, t, _a, _b in tokens(formula.lstrip("=")) if k == "cell"]
    except ValueError:
        return []


def deps(formula: str, limit: int = 20000) -> List[Tuple[int, int]]:
    """Formula tayanadigan hamma kataklar — oraliqlar ochilgan holda (SUM(H16:H190) → H16 … H190)."""
    out = []
    try:
        toks = tokens(formula.lstrip("="))
    except ValueError:
        return out
    for k, t, _a, _b in toks:
        if k == "cell":
            out.append(_cell(t))
        elif k == "range":
            (c1, r1), (c2, r2) = (_cell(x) for x in t.split(":"))
            for r in range(min(r1, r2), max(r1, r2) + 1):
                for c in range(min(c1, c2), max(c1, c2) + 1):
                    out.append((c, r))
                    if len(out) >= limit:
                        return out
    return out


class Evaluator:
    """Bitta hisob: ``values`` — fayldagi (keshdagi) qiymatlar, ``formulas`` — {(ustun, qator): yopiq funksiya},
    ``override`` — dalolatnoma hajmlari (shu kataklar formulasidan ustun). Natija xotirada (memo)."""

    def __init__(self, values: Dict[Tuple[int, int], object], formulas: Dict[Tuple[int, int], Callable],
                 override: Dict[Tuple[int, int], float]):
        self.values, self.formulas, self.override = values, formulas, override
        self.memo: Dict[Tuple[int, int], object] = {}
        self.busy = set()

    def __call__(self, c: int, r: int):
        key = (c, r)
        if key in self.override:
            return self.override[key]
        if key in self.memo:
            return self.memo[key]
        f = self.formulas.get(key)
        if f is None:
            return self.values.get(key)
        if key in self.busy:  # aylanma havola
            return REF
        self.busy.add(key)
        try:
            v = f(self)
        finally:
            self.busy.discard(key)
        self.memo[key] = v
        return v

    def run_all(self):
        """Hamma formulani qator tartibida (yuqoridan) — uzun zanjirlar chuqur rekursiyaga tushmaydi."""
        for key in sorted(self.formulas, key=lambda k: (k[1], k[0])):
            self(*key)
        return self

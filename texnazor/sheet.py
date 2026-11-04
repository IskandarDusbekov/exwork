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
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from openpyxl import load_workbook

from .formula import Err, Evaluator, compile_formula, deps, refs

NUM = re.compile(r"^\s*(\d+)(?:\s*[.,]\s*(\d+))?\s*\.?\s*$")  # «3. 1» (bo'shliq bilan), «26.7.» (oxirida nuqta) ham
MAX_COLS = 80  # undan o'ngdagi ustunlar (tasodifiy yozuvlar) olinmaydi
PREVIEW = 12
READER = 9  # o'qish qoidalari o'zgarsa oshiriladi — eski obyektlar o'zi qayta o'qiladi


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
    final: int = 0         # jami summa qatori (1 dan) — foydalanuvchi ko'rsatgan; 0 — avtomatik («ИТОГО … НДС»)
    start: int = 0         # birinchi ish qatori (1 dan) — foydalanuvchi belgilagan; 0 — sarlavhadan keyin
    end: int = 0           # oxirgi ish qatori (1 dan) — undan keyingisi svodka; 0 — avtomatik
    hide: list = field(default_factory=list)  # [[dan, gacha], …] — foydalanuvchi «keraksiz» deb yig'gan qatorlar (1 dan)
    svod: list = field(default_factory=list)  # [[dan, gacha], …] — foydalanuvchi «сводка» degan qatorlar (bo'lim jamlari)
    hide_cols: list = field(default_factory=list)  # [ustun (0 dan), …] — saytda va Excel'da yig'ilgan ustunlar (faqat ko'rinish)
    # foydalanuvchi tasdiqlagan: boshqa qatordagi hajmga ko'paytirilgan resurs jami («=E4880*$E$149») — ofyortada
    # fayldagi son, dalolatnomada o'z ishi hajmiga mutanosib (formula o'rniga)
    prop_res: bool = False
    # foydalanuvchi tasdiqlagan: o'sha resurslar formulasi tuzatiladi («=E4880*$E$149» → «=E4880*E4879») — ofyorta
    # jami ham o'zgaradi (taklifda oldindan ko'rsatiladi); Excel hisobotga ham tuzatilgan formula yoziladi
    relink: bool = False
    # foydalanuvchi tasdiqlagan: svodka formulasiga qo'lda qo'shilgan son («SUM(…)+124,25») dalolatnomalarda olinmaydi —
    # ofyorta va «Остатка» fayldagidek (son bilan) qoladi
    drop_const: bool = False

    def as_dict(self) -> dict:
        return dict(self.__dict__)


# foydalanuvchi ko'rsatadigan ustunlar (tizim adashganda yoki sarlavhani topa olmaganda) — shablon bo'yicha eslab qolinadi
COL_KEYS = ("num", "name", "unit", "qty", "total", "price", "amount")
# qatorlar — faqat shu faylga tegishli (boshqa faylga ko'chirilmaydi)
ROW_KEYS = ("header", "header_end", "start", "end", "final")
MANUAL_KEYS = COL_KEYS + ROW_KEYS


def _apply_manual(lay: Layout, manual: Optional[dict]):
    for k in MANUAL_KEYS:
        v = (manual or {}).get(k)
        if isinstance(v, int) and not isinstance(v, bool) and v >= (1 if k in ("header", "header_end") else 0):
            setattr(lay, k, v)
    lay.header_end = max(lay.header_end, lay.header)
    lay.hide = hide_ranges((manual or {}).get("hide"), lay.header_end) or lay.hide
    lay.svod = hide_ranges((manual or {}).get("svod"), lay.header_end) or lay.svod
    for k in ("prop_res", "relink", "drop_const"):
        if isinstance((manual or {}).get(k), bool):
            setattr(lay, k, manual[k])
    cols = (manual or {}).get("hide_cols")
    if isinstance(cols, list):
        lay.hide_cols = sorted({c for c in cols if isinstance(c, int) and not isinstance(c, bool) and c >= 0})


def hide_ranges(raw, after: int = 0) -> list:
    """Yig'ilgan qatorlar: [[dan, gacha], …] tartiblangan, ustma-ust tushganlari birlashtirilgan; sarlavha yig'ilmaydi."""
    out = []
    for x in raw or ():
        try:
            a, b = int(x[0]), int(x[1])
        except (TypeError, ValueError, IndexError):
            continue
        a, b = max(min(a, b), after + 1), max(a, b)
        if a <= b:
            out.append([a, b])
    out.sort()
    merged = []
    for a, b in out:
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def hidden_set(lay: Layout) -> set:
    return {r for a, b in (lay.hide or ()) for r in range(a, b + 1)}


def svod_set(lay: Layout) -> set:
    return {r for a, b in (lay.svod or ()) for r in range(a, b + 1)}


def signature(rows: List[tuple], lay: Layout) -> str:
    """Jadval «shabloni»: sarlavha qatorlari matni ustun joylari bilan. Bir dastur / bir tashkilot fayllari bir xil —
    bir marta ko'rsatilgan ustunlar keyingi shunday faylga o'zi qo'llanadi."""
    text = "\n".join("|".join(_low(v) for v in rows[i][:MAX_COLS]).rstrip("|")
                     for i in range(lay.header - 1, min(lay.header_end, len(rows))))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


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


def find_layout(rows: List[tuple], at: Optional[int] = None) -> Optional[Layout]:
    """Sarlavha va ustunlar. ``at`` — foydalanuvchi ko'rsatgan sarlavha qatori (0 dan): «Наименование» shart emas."""
    h = at if at is not None and 0 <= at < len(rows) else next((i for i, r in enumerate(rows[:200]) if _is_header(r)), None)
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
_NUM = r"\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?"
_CONST_PAREN = re.compile(rf"\)\s*[-+]\s*{_NUM}(?=\s*(?:[-+]|$))")  # «SUM(…)+124,25» (keyin * / , kelsa — tegilmaydi)
_CONST_END = re.compile(rf"([A-Z]\$?\d+)\s*[-+]\s*{_NUM}\s*$", re.I)  # «…+H4573-1»


def strip_const(f: str) -> str:
    """Svodka formulasidan qo'lda qo'shilgan / ayirilgan sonni olib tashlaydi: «=SUM(L13:L403)+124.25» → «=SUM(L13:L403)»."""
    prev = None
    while prev != f:
        prev = f
        f = _CONST_PAREN.sub(")", f, count=1)
        f = _CONST_END.sub(r"\1", f, count=1)
    return f


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
    # birinchi svodka (bo'lim jami ham — «JAMI:», keyin «1 QURUVCHI-ISHCHILAR ISH HAQQI …»); undan keyin bo'limlar
    # ketma-ket: ishlar / svodka. Foydalanuvchi belgilagan oxirgi ish — undan keyingisi faqat svodka, lekin undan
    # oldingi bo'lim svodkalari baribir topiladi (aks holda ular «hajmsiz ish» bo'lib qoladi)
    auto = svod_start(rows, lay, formulas)
    start = min(auto or len(rows) + 1, lay.end + 1) if lay.end else auto or len(rows) + 1
    tail = False
    kinds: Dict[int, list] = {}
    last_top: Dict[str, int] = {}
    prev_top = None
    hidden, marked = hidden_set(lay), svod_set(lay)
    for i, r in enumerate(rows, start=1):
        if i <= lay.header_end:
            kinds[i] = ["head", "", None]
            continue
        if i in hidden:  # foydalanuvchi «keraksiz» degan — ish ham, svodka ham emas (formulalar fayldagidek qoladi)
            kinds[i] = ["text", "", None]
            continue
        if i in marked:  # foydalanuvchi «сводка» degan (bo'lim jami va h.k.) — ish emas, formulalari hisoblanadi
            kinds[i] = ["svod", "", None]
            continue
        if lay.start and i < lay.start:  # sarlavha bilan birinchi ish orasi (izoh, bo'lim nomi) — foydalanuvchi ko'rsatgan
            kinds[i] = ["text", "", None]
            continue
        if lay.end and i > lay.end:
            kinds[i] = ["svod", "", None]
            continue
        cell = lambda j: r[j] if j < len(r) else None  # noqa: E731
        num = item_num(cell(lay.num))
        name = _text(cell(lay.name))
        if i >= start and not (lay.end and i <= lay.end) and (tail or (i > start and _is_header(r))):  # keyingi jadval (Ф-2 nusxasi) — ofyorta tugadi
            tail = True
            kinds[i] = ["text", "", None]
            continue
        if num is None and _unnumbered_item(r, lay, formulas[i - 1] if formulas and i - 1 < len(formulas) else {}, i):
            kinds[i] = ["free", "", None]  # raqami yozilmagan material («ГОТОВАЯ ПЕРЕКЛАДИНА | ШТ | 2 | … =E*G»)
            continue
        # ishning o'z resursi pul birligida («92.2 | ПРОЧИЕ МАТЕРИАЛЫ | СУМ») — svodka emas, resurs (aks holda uning
        # summasi o'zgarmas son bo'lib har bir dalolatnomaga tushadi)
        own_res = bool(num and num[1] is not None and name and prev_top and kinds[prev_top][1] == num[0]
                       and i - prev_top <= 200 and all(kinds[k][0] in ("res", "work", "free") for k in range(prev_top, i)))
        if i >= start and not own_res:
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
        if max(last_item, lay.header_end) < i < start and i not in hidden and k[0] != "head":
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


def _sheet_facts(rows, lay: Layout, kinds: dict) -> dict:
    """Tanlash sahifasi uchun: resurslar, svodka, sarlavha va ishlar oralig'i, ustunlar, fayldagi jami summa."""
    from .formula import col_letter
    tops = [i for i, k in kinds.items() if k[0] in ("work", "free")]
    svod = [i for i, k in kinds.items() if k[0] == "svod"]
    total = label = None
    for i in svod:  # «ВСЕГО … С НДС» — bo'lmasa svodkaning oxirgi «ИТОГО / ВСЕГО» qatori
        r = rows[i - 1]
        text = " ".join(_text(v) for v in r[:max(lay.amount, lay.name + 1)] if isinstance(v, str)).strip()
        v = _num(r[lay.amount], text=True) if lay.amount < len(r) else None
        if v and re.search(r"всего|итого|жами|jami", text.lower()) and (total is None or v > total):  # eng kattasi — odatda ҚҚС bilan
            total, label = v, text
    return {"res": sum(1 for k in kinds.values() if k[0] == "res"), "svod": len(svod),
            "head": [lay.header, lay.header_end], "span": [tops[0], tops[-1]] if tops else None,
            "cols": {"qty": col_letter(lay.qty), "total": col_letter(lay.total), "price": col_letter(lay.price),
                     "amount": col_letter(lay.amount)},
            "total": total, "total_label": (label or "")[:80]}


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
                        shifr = next((_text(r[j]) for j in range(lay.num + 1, min(lay.name, len(r))) if _SHIFR.match(_text(r[j]))), "")
                        amt, n_res = _num(g(lay.amount), text=True), 0
                        for i2 in range(i + 1, len(rows) + 1):  # ish summasi resurslarida bo'lsa — ularning yig'indisi
                            k2 = kinds.get(i2)
                            if k2 and k2[0] != "res":
                                break
                            if k2:
                                n_res += 1
                                v2 = _num(rows[i2 - 1][lay.amount], text=True) if lay.amount < len(rows[i2 - 1]) else None
                                if v2 and not _num(g(lay.amount), text=True):
                                    amt = (amt or 0) + v2
                        info["preview"].append([k[1], _text(g(lay.name))[:140], _text(g(lay.unit)), g(lay.qty), shifr, i, amt, n_res])
                info.update(_sheet_facts(rows, lay, kinds))
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


# ===================== qo'lda belgilash (tizim tushunmaganda) =====================
def sheet_values(path: Path, sheet: str) -> List[tuple]:
    """Listning qiymatlari (Excel saqlagani) — belgilash sahifasida ko'rsatish uchun; oxiridagi bo'sh qatorlarsiz."""
    wb = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        if sheet not in wb.sheetnames:
            raise SheetError(f"Файлда «{sheet}» листи йўқ")
        rows = [tuple(_cell_value(v) for v in r[:MAX_COLS]) for r in wb[sheet].iter_rows(values_only=True)]
    finally:
        wb.close()
    while rows and not any(v is not None for v in rows[-1]):
        rows.pop()
    return rows


def mark_guess(rows: List[tuple]) -> dict:
    """Belgilash sahifasida oldindan tanlanadigan joylar — tizim topgani (topmasa — bo'sh): sarlavha, birinchi va oxirgi
    ish qatori, ustunlar."""
    lay = find_layout(rows)
    if lay is None:
        return {}
    kinds, last = _classify(rows, lay)
    items = [i for i, k in kinds.items() if k[0] in ("work", "free", "res")]
    out = {k: getattr(lay, k) for k in COL_KEYS}
    out.update(header=lay.header, header_end=lay.header_end, start=items[0] if items else 0, end=last or 0)
    return out


# ===================== listni o'qish =====================
@dataclass
class Grid:
    sheet: str
    layout: Layout
    rows: List[dict] = field(default_factory=list)  # {"row", "kind", "num", "parent", "vals", "fmls"}
    warnings: List[dict] = field(default_factory=list)
    title: str = ""
    signature: str = ""
    manual: Optional[dict] = None  # qo'llangan qo'lda belgilash (foydalanuvchiniki yoki eslab qolingan shablon)


def read(path: Path, sheet: str, progress: Callable[[int, int], None] = None, manual: Optional[dict] = None,
         memo: Callable[[str], Optional[dict]] = None) -> Grid:
    """Listni ikki marta o'qiydi: qiymatlar (Excel saqlagan) va formulalar. Hech narsa o'zgartirilmaydi.
    manual — foydalanuvchi ko'rsatgan ustunlar / jami qator (``MANUAL_KEYS``); berilmasa — ``memo(shablon)`` dan."""
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
    head = (manual or {}).get("header")
    lay = find_layout(values, at=head - 1 if isinstance(head, int) and head > 0 else None)
    if lay is None:
        raise SheetError("Бу листда офёрта жадвали топилмади: «Наименование работ…» сарлавҳали қатор йўқ")
    _apply_manual(lay, manual)  # sarlavha chegarasi foydalanuvchiniki bo'lsa — shablon ham shu qatorlardan
    sig = signature(values, lay)
    if memo and not any(k in (manual or {}) for k in COL_KEYS):  # ustunlar ko'rsatilmagan — shablondan (yig'ilganlar qoladi)
        m = memo(sig)
        if m:
            manual = {**m, **(manual or {})}
    _apply_manual(lay, manual)
    kinds, last_item = _classify(values, lay, formulas)
    _money_cols(values, formulas, kinds, lay)
    _apply_manual(lay, manual)  # foydalanuvchi ko'rsatgani — formulalardan topilgandan ustun
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
    grid = Grid(sheet=sheet, layout=lay, signature=sig, manual=manual or None)
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
    # summa formulalari narx sifatida oladigan ustunlar («=IF(G="",F*H,G*H)» — H) — ularga nusxa formula yozilmaydi
    price_cols = {c for row in grid.rows if row["kind"] in ("work", "free", "res") and lay.amount in row["fmls"]
                  for c, rr in refs(row["fmls"][lay.amount]) if rr == row["row"]} - {lay.qty, lay.total}
    for row in grid.rows:
        if row["kind"] not in ("work", "free", "res"):
            continue
        v, f, r = row["vals"], row["fmls"], row["row"]
        # narx ko'pincha matn bo'lib saqlangan («4500» — .xls dan): Excel uni formulada son deb oladi, biz ham
        get = lambda j: _num(v[j], text=True) if j < len(v) else None  # noqa: E731
        amount = get(lay.amount)
        if amount is not None and lay.amount not in f:
            # resurs: «narx × jami» (F); jami yozilmagan material («ЖЕЛОБА … | М | 48 | — | 5600 | 268800») — «narx ×
            # miqdor» (E): dalolatnomada E o'z ishi hajmiga mutanosib (``Book.norm_money``)
            base = lay.total if row["kind"] == "res" and get(lay.total) is not None else lay.qty
            price, q = get(lay.price), get(base)
            if price is not None and q is not None and abs(price * q - amount) <= max(0.5, abs(amount) * 1e-6):  # faylda yaxlitlangan
                f[lay.amount] = f"={A(lay.price)}{r}*{A(base)}{r}"
        if amount is None and lay.amount not in f:
            continue
        # summaning nusxasi summa ustunidan chapda («Сметная стоимость … общая» = «Стоимость … сумма») — dalolatnomada
        # ham summaga teng bo'lsin (narx ustuni va narxdan ajratib bo'lmaydigan hol — tegilmaydi)
        price = get(lay.price)
        used = {c for c, rr in refs(f[lay.amount]) if rr == r} if lay.amount in f else set()  # summa o'zi oladigan kataklar
        for j in range(max(lay.qty, lay.total) + 1, lay.amount):
            x = get(j)
            if j == lay.price or j in f or j in used or j in price_cols or x is None or amount is None                     or abs(amount) < 0.01 or price is None:
                continue
            if abs(x - amount) <= max(0.01, abs(amount) * 1e-9) and abs(price - amount) > 0.01:
                f[j] = f"={A(lay.amount)}{r}"
        num = lambda j: v[j] if j < len(v) and isinstance(v[j], (int, float)) and not isinstance(v[j], bool) else None  # noqa: E731
        for j in range(lay.amount + 1, lay.block_end + 1):
            x = num(j)
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
    ev = b.evaluate({t: b.contract_qty(t) for t in b.tops}, full=True)
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
    from .formula import col_letter
    lay = grid.layout
    svod_f = sum(len(r["fmls"]) for r in grid.rows if r["kind"] == "svod")
    if not svod_f and any(r["kind"] == "svod" and any(v is not None for v in r["vals"]) for r in grid.rows):
        grid.warnings.append({"row": 0, "code": "svod_nofml", "title": "Сводкада формула йўқ",
                              "text": "Офёрта пастидаги сводка (иш ҳақи, фоизлар, ҚҚС) сон сифатида ёзилган. "
                                      "Далолатномаларда сводка қаторлари ҳисобланмайди — фақат иш қаторлари."})
    manual = [(r["row"], f) for r in grid.rows if r["kind"] == "svod" for f in r["fmls"].values() if _MANUAL.search(f)]
    if manual:
        items = [[f"{col_letter(int(j))}{r['row']}", "=" + f.lstrip("="), "=" + strip_const(f).lstrip("=")]
                 for r in grid.rows if r["kind"] == "svod" for j, f in sorted(r["fmls"].items()) if _MANUAL.search(f)]
        try:  # shu son har bir dalolatnomaga qancha pul qo'shadi (foizlar va QQS bilan): hajmsiz — son bilan − sonsiz
            bb = Book(dict(lay.as_dict(), drop_const=True), grid.rows)
            extra = _money(bb.grand(bb.evaluate({}, full=True)) - bb.grand(bb.evaluate({})))
        except Exception:  # noqa: BLE001 — faqat tushuntirish uchun; hisob buzilmasin
            extra = ""
        if lay.drop_const:
            grid.warnings.append({"row": manual[0][0], "fix": "drop_const_on", "items": items, "count": len(items), "amount": extra,
                                  "title": "Қўлда қўшилган сон далолатномаларда олинмайди (сиз тасдиқлагансиз)",
                                  "text": f"{len(items)} та сводка формуласидаги қўлда қўшилган сон далолатномаларга тушмайди. "
                                          "Офёрта ва «Остатка» файлдагидек (шу сон билан) қолади."})
        else:
            grid.warnings.append({"row": manual[0][0], "fix": "drop_const", "items": items, "count": len(items), "amount": extra,
                                  "title": "Сводка формуласида қўлда қўшилган сон бор",
                                  "text": f"{len(manual)} та формулада ҳисобга қўлда сон қўшилган ёки айрилган (масалан "
                                          f"{manual[0][0]}-қатор: {manual[0][1][:60]}). Тизим уни файлдагидек қолдиради, шунинг учун бу сон "
                                          "ҳар бир далолатномага тушади (ҳажм ёзилмаса ҳам). Смета тузувчи билан аниқлаштиринг "
                                          "ёки пастдаги тугмани босинг."})
    # resurs jami boshqa qatordagi hajmga ko'paytirilgan («=E4880*$E$149» — formula ko'chirilganda $ qotib qolgan):
    # bu pul o'z ishining hajmiga bog'lanmagan, faylning jamisi ham shu qism bo'yicha noto'g'ri bo'lishi mumkin
    foreign: Dict[int, set] = {}
    bad_res = []
    for r in grid.rows:
        f = r["fmls"].get(lay.total) if r["kind"] == "res" else None
        if not f or "!" in f:
            continue
        for c, rr in refs(f):
            if c == lay.qty and rr not in (r["row"], r["parent"]):
                foreign.setdefault(r["parent"], set()).add(rr)
                bad_res.append(r)
    done = list({id(r): r for r in bad_res}.values())
    if foreign and lay.relink:  # foydalanuvchi tasdiqlagan: formula o'z ishi hajmiga ulanadi, qiymatlar qayta hisoblanadi
        old_total = Book(lay.as_dict(), grid.rows).contract_total()
        items = []
        for r in done:
            new = relink_formula(r["fmls"][lay.total], lay.qty, r["row"], r["parent"])
            items.append([f"{col_letter(lay.total)}{r['row']}", "=" + r["fmls"][lay.total].lstrip("="), "=" + new.lstrip("=")])
            r["fmls"][lay.total] = new
        b = Book(lay.as_dict(), grid.rows)
        ev = b.evaluate({t: b.contract_qty(t) for t in b.tops}, full=True)
        for r in grid.rows:  # ofyortada va fayl tekshiruvida yangi formula natijasi
            if r["kind"] == "head":
                continue
            for j, f in r["fmls"].items():
                if "!" in f:
                    continue
                v = ev(j, r["row"])
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    while len(r["vals"]) <= j:
                        r["vals"].append(None)
                    r["vals"][j] = v
        new_total = b.contract_total()
        grid.warnings.append({"row": next(iter(foreign)), "fix": "relink_on", "items": items, "count": len(items),
                              "total_old": old_total, "total_new": new_total,
                              "title": "Ресурслар формуласи тузатилди (сиз тасдиқлагансиз)",
                              "text": f"{len(items)} та ресурс жами ўз иши ҳажмига уланди. Офёрта жами "
                                      f"{_money(old_total)} → {_money(new_total)} сўм. Excel ҳисоботга ҳам тузатилган формула ёзилади."})
    elif foreign and lay.prop_res:  # foydalanuvchi tasdiqlagan: formula olinadi — Book.override F ni hajmga mutanosib qiladi
        ev = None
        for r in done:
            while len(r["vals"]) <= lay.total:
                r["vals"].append(None)
            if not isinstance(r["vals"][lay.total], (int, float)):  # faylda saqlangan natija yo'q — shartnoma hajmida hisoblanadi
                if ev is None:
                    b = Book(lay.as_dict(), grid.rows)
                    ev = b.evaluate({t: b.contract_qty(t) for t in b.tops}, full=True)
                v = ev(lay.total, r["row"])
                r["vals"][lay.total] = v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        for r in done:
            r["fmls"].pop(lay.total, None)
        top = next(iter(foreign))
        grid.warnings.append({"row": top, "fix": "prop_res_on", "title": "Ресурслар ўз иши ҳажмига мутаносиб қилинди (сиз тасдиқлагансиз)",
                              "text": f"{len(done)} та ресурснинг жами бошқа қатордаги ҳажмга кўпайтирилган эди. Офёрта жами "
                                      f"файлдагидек қолди; далолатномада бу ресурслар ўз иши ҳажмига мутаносиб ҳисобланади."})
    elif foreign:
        by_row = {r["row"]: r for r in grid.rows}
        top, src = next(iter(foreign.items()))
        n_res = sum(1 for r in grid.rows if r["kind"] == "res" and r["parent"] in foreign)
        q = lambda row: _text(by_row[row]["vals"][lay.qty]).replace(".", ",") if row in by_row and lay.qty < len(by_row[row]["vals"]) else "?"  # noqa: E731
        E = col_letter(lay.qty)
        grid.warnings.append({"row": top, "title": "Ресурслар бошқа қатордаги ҳажмга кўпайтирилган",
                              "text": f"{by_row[top]['num'] if top in by_row else top}-иш ({top}-қатор, ҳажми {q(top)}) ресурсларининг "
                                      f"жами ўз иши ҳажмига эмас, {', '.join(f'{E}{s} ({q(s)})' for s in sorted(src)[:3])} га "
                                      f"кўпайтирилган — формула кўчирилганда «$» қотиб қолган бўлиши мумкин. Бу пул далолатномада "
                                      f"иш ҳажмига мутаносиб ўзгармайди, файлдаги жами ҳам шу қисм бўйича нотўғри бўлиши мумкин. "
                                      f"Жами {len(foreign)} та ишда, {n_res} та ресурс. Смета тузувчи формулани тузатсин "
                                      f"ёки «Мутаносиб қилиш» тугмасини босинг.", "fix": "prop_res",
                              "example": f"{col_letter(lay.total)}{bad_res[0]['row']}: {bad_res[0]['fmls'][lay.total]}",
                              **_relink_offer(grid, done)})
    other = sorted({r["row"] for r in grid.rows for f in r["fmls"].values() if "!" in f})
    if other:
        grid.warnings.append({"row": other[0], "title": "Бошқа листга ҳавола қилувчи формулалар бор",
                              "text": f"{len(other)} та қаторда формула бошқа листдаги катакни олади "
                                      f"(биринчиси {other[0]}-қатор). Бундай катакларда файлда сақланган қиймат олинади (далолатномада ўзгармайди)."})


def relink_formula(f: str, qty: int, row: int, parent: int) -> str:
    """Hajm ustunidagi begona qatorga havola («$E$149») — o'z ishining hajmiga («E4879»); o'z qatori va ishi qoladi."""
    from .formula import col_letter
    E = col_letter(qty)
    pat = re.compile(rf"(?<![A-Za-z0-9_.$:])\$?{E}\$?(\d+)(?![\d(A-Za-z_:])")
    return pat.sub(lambda m: m.group(0) if int(m.group(1)) in (row, parent) else f"{E}{parent}", f)


def _money(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ")


def _relink_offer(grid: Grid, done: List[dict]) -> dict:
    """«Formulani tuzatish» taklifi: qaysi katakda eski → yangi formula, natijasi va ofyorta jamiga ta'siri
    (foydalanuvchi ko'rib, tasdiqlaydi)."""
    from .formula import col_letter
    lay = grid.layout
    new = {r["row"]: relink_formula(r["fmls"][lay.total], lay.qty, r["row"], r["parent"]) for r in done}
    if not any(new[r["row"]] != r["fmls"][lay.total] for r in done):
        return {}
    b0 = Book(lay.as_dict(), grid.rows)
    b1 = Book(lay.as_dict(), [dict(r, fmls={**r["fmls"], lay.total: new[r["row"]]}) if r["row"] in new else r
                              for r in grid.rows])
    full = lambda b: b.evaluate({t: b.contract_qty(t) for t in b.tops}, full=True)  # noqa: E731
    ev0, ev1 = full(b0), full(b1)
    num = lambda v: _money(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else "—"  # noqa: E731
    F = col_letter(lay.total)
    items = [[f"{F}{r['row']}", "=" + r["fmls"][lay.total].lstrip("="), "=" + new[r["row"]].lstrip("="),
              num(ev0(lay.total, r["row"])), num(ev1(lay.total, r["row"]))] for r in done[:6]]
    t0, t1 = b0.grand(ev0), b1.grand(ev1)
    return {"relink": {"items": items, "count": len(done), "total_old": _money(t0), "total_new": _money(t1),
                       "delta": _money(t1 - t0)}}


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
        # ofyorta va «Остатка» — fayldagi formula; dalolatnoma — qo'lda qo'shilgan sonsiz (foydalanuvchi tasdiqlagan bo'lsa)
        self.full_formulas = self.formulas
        if self.lay.drop_const:
            self.formulas = dict(self.formulas)
            for r in rows:
                if r["kind"] == "svod":
                    for j, f in r["fmls"].items():
                        if "!" not in f and _MANUAL.search(f):
                            self.formulas[(int(j), r["row"])] = compile_formula(strip_const(f))
        self.tops =[r["row"] for r in rows if r["kind"] in ("work", "free")]
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
                if f is None and ch in self.norm_money:
                    continue  # jami yozilmagan — summa E dan, F bo'sh qoladi
                out[(lay.total, ch)] = (f * q / base) if (base and f is not None) else norm * q
        return out

    def evaluate(self, qty: Dict[int, float], full: bool = False) -> Evaluator:
        """full — ofyorta / «Остатка» hisobi (fayldagi formula); aks holda — dalolatnoma (``drop_const`` hisobga olinadi).
        Formulasiz svodka (bo'lim jamlari son bo'lib yozilgan) — o'z ishlari pulига mutanosib (``svod_groups``)."""
        ev = Evaluator(self.values, self.full_formulas if full else self.formulas, self.override(qty))
        groups = self.svod_groups()
        if groups:
            money = self._top_money(ev)
            for g in groups:
                share = sum(money.get(t, 0.0) for t in g["tops"]) / g["base"]
                for key in g["cells"]:
                    ev.override[key] = self._fnum(key) * share
        return ev.run_all()

    def act_values(self, qty: Dict[int, float], full: bool = False) -> Dict[Tuple[int, int], float]:
        """Dalolatnomada fayldagidan farq qiladigan formulasiz kataklar: ``override`` + mutanosib svodka sonlari."""
        if not self.svod_groups():
            return self.override(qty)
        return dict(self.evaluate(qty, full).override)

    def _fnum(self, key) -> float:
        return _num(self.values.get(key), text=True) or 0.0

    def _top_money(self, ev: Evaluator) -> Dict[int, float]:
        """Har bir ish / mustaqil qatorning puli (resurslari bilan; ish qatori ularni takrorlasa — bir marta)."""
        def amt(r):
            v = ev(self.lay.amount, r)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
        out = {}
        for top in self.tops:
            own, kids = amt(top), sum(amt(c) for c in self.children.get(top, ()))
            out[top] = kids + (0.0 if kids and abs(own - kids) <= max(0.5, abs(kids) * 1e-9) else own)
        return out

    def _linked(self, key, memo: dict) -> bool:
        """Svodka katagi formulasi (bevosita yoki boshqa svodka orqali) ish / resurs qatorlariga tayanadimi."""
        if key in memo:
            return memo[key]
        memo[key] = False
        row = self.by_row.get(key[1])
        f = row["fmls"].get(key[0]) if row else None
        out = False
        if f and "!" not in f:
            for d in deps(f, limit=50000):
                k = self.by_row.get(d[1])
                if not k:
                    continue
                if k["kind"] in ("work", "free", "res") or (k["kind"] == "svod" and d in self.full_formulas and self._linked(d, memo)):
                    out = True
                    break
        memo[key] = out
        return out

    def svod_groups(self) -> List[dict]:
        """Formulasiz svodka guruhlari: «ИТОГО ПО РАЗДЕЛУ» ostidagi «ЗАТРАТЫ ТРУДА … / ЕСП 15% / … / ИТОГО С УЧЕТОМ
        КОЭФФ.» — pul kataklari son bo'lib yozilgan (smeta dasturidan), formulalari ham faqat shu sonlarni qo'shadi.
        Fayldagi qiymat dalolatnomaga o'zgarmay tushsa — hajm yozilmasa ham to'liq summa chiqadi. Shuning uchun har bir
        guruhning sonlari o'z ishlari puliga mutanosib: dalolatnoma = son × (dalolatnomadagi pul / shartnomadagi pul).
        Guruhning ishlari — tepasidagi bo'lim; bo'lim jami / svodka foizi boshqa bo'limlarnikiga o'xshamasa —
        yuqoriroqdan boshlab (masalan, «ВСЕГО ПО ОБЪЕКТУ» — hamma ishlar). Topilmasa — guruh fayldagidek qoladi.
        Natija: [{"rows": [dan, gacha], "cells": [(ustun, qator), …], "tops": [ish qatori, …], "base": pul}]."""
        if hasattr(self, "_svod_groups"):
            return self._svod_groups
        self._svod_groups = []
        lay = self.lay
        if not self.svod_rows or not self.tops:
            return self._svod_groups
        item_kinds = ("work", "free", "res")
        cols = {lay.amount} | {j for r in self.rows if r["kind"] in item_kinds for j in r["fmls"] if j > lay.amount}
        groups, cur, last_n = [], None, None
        for r in self.rows:
            k = r["kind"]
            if k in item_kinds:
                cur = None
                continue
            if k != "svod":
                continue
            n = item_num(r["vals"][lay.num] if lay.num < len(r["vals"]) else None)
            n = int(n[0]) if n and n[1] is None else None
            if cur is None or (n is not None and last_n is not None and n <= last_n):  # raqamlash qaytadan — yangi guruh
                cur = []
                groups.append(cur)
                last_n = None
            cur.append(r["row"])
            if n is not None:
                last_n = n
        memo: dict = {}
        found = []
        for g in groups:
            if any(self._linked((j, r), memo) for r in g for j in self.by_row[r]["fmls"] if (j, r) in self.full_formulas):
                continue  # svodka formulasi ishlarni oladi — fayldagidek hisoblanadi
            cells = [(j, r) for r in g for j in sorted(cols) if (j, r) not in self.full_formulas
                     and abs(_num(self.values.get((j, r)), text=True) or 0.0) >= 0.5]
            # summa ustunida soni yo'q guruh («РАЗДЕЛ …» sarlavhasi, yonida tasodifiy yozuv) — svodka emas
            if any(j == lay.amount for j, _r in cells):
                found.append((g, cells))
        if not found:
            return self._svod_groups
        money = self._top_money(Evaluator(self.values, self.full_formulas,
                                          self.override({t: self.contract_qty(t) for t in self.tops})))
        tops = sorted(self.tops)
        # bo'lim chegarasi — puli bor svodka guruhi; ishlar orasidagi «РАЗДЕЛ …» sarlavhasi (jamisiz) chegara emas:
        # uning ishlari keyingi «ИТОГО ПО РАЗДЕЛУ» ga kiradi
        ends = [lay.header_end] + [g[-1] for g in groups if any(
            (lay.amount, r) in self.full_formulas or abs(self._fnum((lay.amount, r))) >= 0.5 for r in g)]

        def span(a, b):  # (a, b) oralig'idagi ishlar va ularning puli
            ts = [t for t in tops if a < t < b]
            return ts, sum(money.get(t, 0.0) for t in ts)

        def big(cells):
            return max(abs(self._fnum(c)) for c in cells)
        near = {}
        for g, cells in found:
            prev = max(e for e in ends if e < g[0])
            ts, s = span(prev, g[0])
            near[g[0]] = (ts, s)
        ratios = sorted(big(cells) / near[g[0]][1] for g, cells in found if near[g[0]][1] > 0.5)
        if not ratios:
            return self._svod_groups
        target = ratios[len(ratios) // 2]
        import math
        fit = lambda s, cells: s > 0.5 and abs(math.log(big(cells) / s / target)) <= math.log(1.3)  # noqa: E731
        done = []  # [(qamrov boshi, guruh boshi, guruh oxiri, jami)] — o'z ishlari topilgan guruhlar

        def inner(e, g0):
            """(e, g0) oralig'idagi topilgan guruhlar jamlari (boshqa yig'indi guruh ichidagilari — bir marta)."""
            hs = [h for h in done if h[1] > e and h[2] < g0]
            return [h for h in hs if not any(o is not h and o[0] < h[1] and o[1] > h[2] and o[0] >= e for o in hs)]
        for g, cells in found:
            ts, s = near[g[0]]
            G = big(cells)
            start = max(e for e in ends if e < g[0])
            if not fit(s, cells):
                # yig'indi guruh («ВСЕГО ПО ОБЪЕКТУ», blok jami): jami — ichidagi bo'lim jamlari yig'indisi (yaxlitlash
                # farqi bilan); qaysi oraliqda shunday bo'lsa — o'sha ishlar. Topilmasa — foiz o'xshashligi bo'yicha
                best = None
                for e in ends:
                    if e >= g[0]:
                        continue
                    hs = inner(e, g[0])
                    if not hs:
                        continue
                    err = abs(sum(h[3] for h in hs) - G)
                    if err <= max(2.0, G * 0.015):  # bo'lim jamlari yaxlitlangan (ba'zan qo'lda tuzatilgan)
                        ts2, s2 = span(e, g[0])
                        if s2 > 0.5 and (best is None or err < best[0] - 1e-6):
                            best = (err, ts2, s2, e)
                if best is None:
                    for e in ends:
                        if e >= g[0]:
                            continue
                        ts2, s2 = span(e, g[0])
                        if fit(s2, cells):
                            d = abs(math.log(G / s2 / target))
                            if best is None or d < best[0]:
                                best = (d, ts2, s2, e)
                if best is None:
                    continue
                ts, s, start = best[1], best[2], best[3]
            done.append((start, g[0], g[-1], G))
            self._svod_groups.append({"rows": [g[0], g[-1]], "cells": cells, "tops": ts, "base": s})
        if self._svod_groups:
            self._copy_cols_group([r for g in groups for r in g], money)
        return self._svod_groups

    def _copy_cols_group(self, svod: List[int], money: Dict[int, float]):
        """Summaning nusxa ustuni summadan chapda («Сметная стоимость … общая» = «Стоимость … сумма», ish qatorlarida
        «=J13»): svodkada u yerda obyekt jamlari son bo'lib turadi («61 414 949» har bo'limda) — dalolatnomada
        hamma ishlar ulushiga mutanosib (aks holda №1 blokida 517 mln ko'rinib turadi)."""
        from .formula import col_letter
        lay = self.lay
        items = [r for r in self.rows if r["kind"] in ("work", "free", "res") and lay.amount in r["fmls"]]
        if not items:
            return
        cols = []
        for c in range(max(lay.qty, lay.total) + 1, lay.amount):
            if c == lay.price:
                continue
            n = sum(1 for r in items if r["fmls"].get(c, "").replace("+", "").upper() == f"={col_letter(lay.amount)}{r['row']}")
            if n * 2 >= len(items):
                cols.append(c)
        cells = [(c, r) for r in svod for c in cols if (c, r) not in self.full_formulas and abs(self._fnum((c, r))) >= 0.5]
        base = sum(money.get(t, 0.0) for t in self.tops)
        if cells and base > 0.5:
            self._svod_groups.append({"rows": [cells[0][1], cells[-1][1]], "cells": cells, "tops": list(self.tops),
                                      "base": base, "copy": True})

    def svod_warnings(self) -> List[dict]:
        """Formulasiz svodka mutanosib hisoblangani haqida — foydalanuvchi bilsin (xato emas)."""
        gs = [g for g in self.svod_groups() if not g.get("copy")]
        if not gs:
            return []
        rows = ", ".join(f"{g['rows'][0]}–{g['rows'][1]}" for g in gs[:4]) + (" …" if len(gs) > 4 else "")
        return [{"row": gs[0]["rows"][0], "code": "svod_prop",
                 "title": f"Сводка сон билан ёзилган — далолатномада ишларга мутаносиб ({len(gs)} та жамлама)",
                 "text": f"Бўлим жамлари («ЗАТРАТЫ ТРУДА…», «ЕСП 15%», «ИТОГО С УЧЕТОМ КОЭФФ.» ва ҳ.к.) файлда формуласиз "
                         f"сон ({rows}-қаторлар). Тизим уларни далолатномага ўзгармай қўшмайди: ҳар бир жамлама ўз бўлими "
                         "ишларининг пулига мутаносиб ҳисобланади (ҳажм ёзилмаса — 0, бўлим тўлиқ бажарилса — файлдагидек). "
                         "Иш ҳақи ва материал улуши бўлимда ҳар хил бўлса, бир неча сўм фарқ бўлиши мумкин."}]

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
        if lay.final and lay.final in self.by_row:
            self._final = (lay.amount, lay.final)
            return self._final
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
            self._contract = self.grand(self.evaluate({r: self.contract_qty(r) for r in self.tops}, full=True))
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


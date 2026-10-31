"""Ф-2 — bajarilgan ishlar dalolatnomasi (oy bo'yicha), texnazor Excel'dagi «Ф-2 за <oy>» varag'i bilan bir xil.

Faqat shu oyda hajmi yozilgan ishlar: ish qatorida E — shu oydagi hajm, resurslarida E — me'yor, F = E × ish hajmi,
I = narx × F; summa J..O (иш ҳақи, машина, материал, жиҳоз пудратчи / буюртмачи, кабель) ustunlariga tushadi.
Pastda «JAMI:» va svodka (foizlar ofyortadan). Hamma joyda Excel formulasi VA uning qiymati yoziladi: telefon,
Telegram ko'rinishi kabi hisoblamaydigan dasturlarda ham summalar ko'rinadi, Excel'da hajm o'zgartirilsa — qayta hisoblanadi.

Ko'rinishi uning faylidagidek: Times New Roman 10, oq katak, ingichka chiziq; чел-ч resurslari binafsha, маш-ч —
to'q yashil, summa (I) — binafsha; blok sarlavhasi qizil, bo'lim sarlavhasi pushti fonda. Bo'sh sarlavhalar yozilmaydi.
"""
import math
from io import BytesIO

import xlsxwriter
from django.db.models import Prefetch

from . import services
from .models import Done, Item, Period, SiteObject

MONTHS_UZ = ["YANVAR", "FEVRAL", "MART", "APREL", "MAY", "IYUN", "IYUL", "AVGUST", "SENTYABR", "OKTYABR", "NOYABR", "DEKABR"]
CAT_COL = {"J": "J", "K": "K", "L": "L", "M": "M", "N": "N", "O": "O"}  # turi -> summa ustuni (ofyortadagidek)
WIDTHS = {"A": 6.6, "B": 11.1, "C": 73, "D": 15.3, "E": 11.9, "F": 12.3, "G": 13.1, "H": 13.1, "I": 15.1,
          "J": 13.3, "K": 12.3, "L": 13.4, "M": 12.3, "N": 13, "O": 10.9}
HIDDEN = ("H", "J", "K", "L", "M", "O")  # uning faylidagidek: yordamchi ustunlar yashirin (bosmaga chiqmaydi)
PURPLE, GREEN_T, RED = "#800080", "#003300", "#FF0000"
PINK, GREEN = "#CCFFFF", "#CCFFCC"
LINE = 12.75  # bitta qator balandligi (Times New Roman 10)


def _pct(v: float) -> str:
    """0.0032 -> «0,32%», 0.17 -> «17%» (svodka qatori nomida)."""
    return f"{v * 100:.4f}".rstrip("0").rstrip(".").replace(".", ",") + "%"


def _num(v: str):
    """«42» -> 42, «1522» -> 1522 (uning faylidagidek son; matn bo'lsa Excel yashil uchburchak ko'rsatadi).
    «42.6», «00001», «Е46-04-012-1» — o'zicha."""
    return int(v) if v and v.isdigit() and (v == "0" or not v.startswith("0")) else v


def _height(*texts) -> float:
    """O'ralgan matn uchun qator balandligi (Excel ochilganda o'zi moslamaydi): C ~62 belgi (qalin), D ~14 belgi."""
    lines = max(math.ceil(len(t or "") / w) if t else 1 for t, w in texts)
    return LINE * max(1, lines)


def file_name(period: Period) -> str:
    return f"Ф-2 {period.title} — {period.obj.name}"[:150] + ".xlsx"


def build(period: Period) -> bytes:
    obj: SiteObject = period.obj
    rates = obj.rates or {}
    done = dict(Done.objects.filter(period=period).values_list("item_id", "qty"))
    items = (obj.items.filter(kind__in=services.ITEM_KINDS, pk__in=done.keys())
             .prefetch_related(Prefetch("resources", queryset=Item.objects.order_by("order"))))
    by_sec = {}
    for it in items:
        if it.kind != Item.Kind.EXTRA:  # qo'shimcha qatorlar — svodkada
            by_sec.setdefault(it.section_id, []).append(it)

    buf = BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet(f"Ф-2 за {Period.MONTHS[period.month - 1]}"[:31])
    cache = {}

    def fmt(bold=False, color=None, bg="#FFFFFF", box=True, align="center", money=False):
        key = (bold, color, bg, box, align, money)
        if key not in cache:
            p = {"font_name": "Times New Roman", "font_size": 10, "bold": bold, "align": align, "valign": "vcenter",
                 "text_wrap": True}
            if color:
                p["font_color"] = color
            if bg:
                p["bg_color"] = bg
            if box:
                p["border"] = 1
            if money:
                p["num_format"] = "#,##0"
            cache[key] = wb.add_format(p)
        return cache[key]

    for col, w in WIDTHS.items():
        ws.set_column(f"{col}:{col}", w, None, {"hidden": col in HIDDEN})

    # ---- sarlavha ----
    top = fmt(bold=True, box=False, align="left")
    ws.merge_range("A1:C1", f"Zakazchik: {obj.customer}", top)
    ws.merge_range("A2:C2", f"Pudratchi:  {obj.contractor}", top)
    for rng, text in (("A4:I4", f"{obj.name} OBYEKTIDA"), ("A6:I6", "BAJARILGAN ISHLAR DALOLATNOMASI"),
                      ("A8:I8", f"{period.year} YIL {MONTHS_UZ[period.month - 1]} OYI")):
        ws.merge_range(rng, text, fmt(bold=True, box=False))
    head = fmt(bold=True, bg=PINK)
    for col, text in (("A", "№ п/п"), ("B", "Шифр номера нормативов и  коды ресурсов"),
                      ("C", "Наименование работ и затрат, характеристика оборудования и его масса, расход ресурсов на единицу измерения"),
                      ("D", "Единица измерения")):
        ws.merge_range(f"{col}10:{col}11", text, head)
    ws.merge_range("E10:I10", "Количество", head)
    for col, text in zip("EFGHI", ("на единицу\nизмерения", "по проектным данным", "Цена подрятчик", "Цена заказчик", "Сумма")):
        ws.write(f"{col}11", text, head)
    for col, text in zip("JKLMNO", ("ЧЕЛ-Ч.", "МАШ-Ч.", "МАТРИАЛ", "ЖИХОЗ ПОДРЯТЧИК", "ЖИХОЗ ЗАКАЗЧИК", "КАБЕЛЬ")):
        ws.write(f"{col}11", text, fmt())
    ws.set_row(10, 38.25)
    for col, n in zip("ABCDEFGHI", (1, 2, 3, 4, 5, 6, None, None, 8)):
        ws.write(f"{col}12", n, head)

    # ---- ishlar (bo'lim sarlavhalari faqat ichida shu oyda ish bo'lsa) ----
    r = 13
    first = r
    sums = dict.fromkeys(CAT_COL, 0.0)  # JAMI qatori qiymatlari

    def heading(title, block):
        nonlocal r
        f = fmt(bold=True, color=RED if block else None, bg=PINK)
        for col in "ABDEFGHI":
            ws.write_blank(f"{col}{r}", None, f)
        ws.write(f"C{r}", title, f)
        r += 1

    def priced(rr, it, qty_ref, qty, color):
        """Narx (buyurtmachi uskunasi — H da), summa formulasi va uning turi ustuni. Narxi 0 — I bo'sh (uning faylidagidek)."""
        pc = "H" if it.category == "N" else "G"
        ws.write(f"{pc}{rr}", it.price, fmt(bold=True, color=color, money=True))
        if not it.price:
            ws.write_blank(f"I{rr}", None, fmt(bold=True, color=PURPLE, money=True))
            amount = 0.0
        else:
            amount = it.price * qty
            ws.write_formula(f"I{rr}", f"=+{pc}{rr}*{qty_ref}{rr}", fmt(bold=True, color=PURPLE, money=True), amount)
        if it.category in CAT_COL:
            ws.write_formula(f"{CAT_COL[it.category]}{rr}", f"=+I{rr}", fmt(color=color, bg=None, box=False, money=True), amount)
            sums[it.category] += amount

    def write_items(rows):
        nonlocal r
        for it in rows:
            q = done[it.pk]
            f, fl = fmt(bold=True), fmt(bold=True, align="left")
            ws.write(f"A{r}", _num(it.num), f)
            ws.write(f"B{r}", _num(it.code), f)
            ws.write(f"C{r}", it.name, fl)
            ws.write(f"D{r}", it.unit, f)
            ws.write(f"E{r}", q, f)
            for col in "FGHI":
                ws.write_blank(f"{col}{r}", None, f)
            h = _height((it.name, 62), (it.unit, 14))
            if h > LINE:
                ws.set_row(r - 1, h)
            if it.kind == Item.Kind.FREE:  # mustaqil resurs: hajm × narx
                priced(r, it, "E", q, None)
                r += 1
                continue
            w = r
            r += 1
            for res in it.resources.all():
                u = (res.unit or "").upper()
                color = PURPLE if "ЧЕЛ" in u else GREEN_T if "МАШ" in u else None
                f, fl = fmt(color=color), fmt(color=color, align="left")
                ws.write(f"A{r}", res.num, f)
                ws.write(f"B{r}", _num(res.code), f)
                ws.write(f"C{r}", res.name, fl)
                ws.write(f"D{r}", res.unit, f)
                ws.write(f"E{r}", res.qty, f)
                ws.write_formula(f"F{r}", f"=E{r}*E{w}", f, res.qty * q)
                ws.write_blank(f"H{r}", None, f)
                priced(r, res, "F", res.qty * q, color)
                h = _height((res.name, 72), (res.unit, 14))
                if h > LINE:
                    ws.set_row(r - 1, h)
                r += 1

    pending = []  # hali yozilmagan sarlavhalar: (nomi, blokmi)
    write_items(by_sec.get(None, []))
    for s in obj.sections.all():
        block = bool(services._BLOCK.search(s.title))
        if block:
            pending = [(s.title, True)]
        elif not s.items_count:
            pending = [p for p in pending if p[1]] + [(s.title, False)]
        elif s.pk in by_sec:
            for t, b in pending + [(s.title, False)]:
                heading(t, b)
            pending = []
            write_items(by_sec[s.pk])
    last = max(first, r - 1)

    # ---- JAMI va svodka (uning faylidagi qatorlar va formulalar) ----
    jami = r
    ws.write(f"C{jami}", "JAMI:", fmt(bold=True, bg=PINK, box=False))
    for cat, col in CAT_COL.items():
        ws.write_formula(f"{col}{jami}", f"=+SUM({col}{first}:{col}{last})", fmt(bold=True, bg=None, box=False, money=True), sums[cat])
    r += 1
    at, val = {}, {}  # qator raqami va qiymati (kalit bo'yicha)
    prev, n = None, 0  # oldingi raqamli qator (bo'sh qatordan keyin ham shundan davom etadi) va uning raqami

    def line(key, label, formula, value, bold=True, bg=GREEN):
        nonlocal r, prev, n
        f = fmt(bold=bold, bg=bg)
        n += 1
        if prev:
            ws.write_formula(f"A{r}", f"=+A{prev}+1", f, n)
        else:
            ws.write(f"A{r}", n, f)
        for col in "BDEFGH":
            ws.write_blank(f"{col}{r}", None, f)
        ws.write(f"C{r}", label, fmt(bold=bold, bg=bg, align="left"))
        if formula:
            ws.write_formula(f"I{r}", formula.format(**at), fmt(bold=bold, bg=bg, money=True), value)
        at[key], val[key], prev = r, value, r
        r += 1

    def rate(key, label, base, k):
        line(key, f"{label} {_pct(rates[k])}", "=+I{%s}*%s" % (base, rates[k]), val[base] * rates[k])

    line("J", "QURUVCHI-ISHCHILAR ISH HAQQI", f"=+J{jami}", sums["J"])
    line("K", "QURILISH MASHINALARI VA MEXANIZMLARI UCHUN JAMI:", f"=+K{jami}", sums["K"])
    line("L", "QURILISH MATERIALLARI UCHUN JAMI: /PUDRATCHI/", f"=+L{jami}", sums["L"])
    rate("trL", "TRANSPORT XARAJATLARI", "L", "tr_material")
    line("O", "KABEL MAHSULOTLARI  /PUDRATCHI/", f"=+O{jami}", sums["O"])
    rate("trO", "TRANSPORT XARAJATLARI SOTUVCHIDAN", "O", "tr_cable")
    line("direct", "JAMI TO'G'RIDAN TO'G'RI XARAJATLAR", "=SUM(I{J}:I{trO})", sum(val[k] for k in ("J", "K", "L", "trL", "O", "trO")))
    rate("over", "PUDRATCHI BOSHQA XARAJATLARI", "direct", "overhead")
    line("do", "JAMI TO'G'RIDAN TO'G'RI VA BOSHQA XARAJATLARI BILAN", "=SUM(I{direct}:I{over})", val["direct"] + val["over"])
    line("N", "USKUNALAR  /BUYURTMACHI/", f"=+N{jami}", sums["N"])
    rate("trN", "TRANSPORT XARAJATI", "N", "tr_eq_customer")
    line("Nf", "JAMI USKUNALAR TRANSPORT XARAJATLARI BILAN", "=SUM(I{N}:I{trN})", val["N"] + val["trN"])
    line("M", "USKUNALAR  /PUDRATCHI/", f"=+M{jami}", sums["M"])
    rate("trM", "TRANSPORT XARAJATLARI", "M", "tr_eq_contractor")
    line("Mf", "JAMI USKUNALAR TRANSPORT XARAJATLARI BILAN", "=SUM(I{M}:I{trM})", val["M"] + val["trM"])
    line("base", "JAMI TO'G'RIDAN TO'G'RI XARAJATLAR USKUNALAR BILAN", "=+I{do}+I{Nf}+I{Mf}", val["do"] + val["Nf"] + val["Mf"])
    rate("ins", "OBYEKTNI SUG'URTALASH", "base", "insurance")
    # svodkadagi qo'shimcha qatorlar (masalan «Художественный элемент») — shu oyda yozilgan hajmi bilan
    xs = 0.0
    for it in obj.items.filter(kind=Item.Kind.EXTRA).order_by("order"):
        q = done.get(it.pk)
        amount = it.price * q if q else 0.0
        line(f"x{it.pk}", it.name, None, amount, bold=False, bg="#FFFFFF")
        xr = r - 1
        ws.write(f"D{xr}", it.unit, fmt())
        ws.write(f"E{xr}", q, fmt()) if q else ws.write_blank(f"E{xr}", None, fmt())
        ws.write(f"G{xr}", it.price, fmt(money=True))
        ws.write_formula(f"I{xr}", f"=+G{xr}*E{xr}", fmt(money=True), amount)
        ws.set_row(xr - 1, _height((it.name, 72)))
        xs += amount
    at["x_last"] = r - 1
    line("novat", "JAMI OB'EKT QIYMATI QQS SIZ", "=SUM(I{base}:I{x_last})", val["base"] + val["ins"] + xs)
    rate("vat", "QQS", "novat", "vat")
    line("total", f"JAMI OB'EKT QIYMATI {_pct(rates['vat'])} QQS BILAN", "=SUM(I{novat}:I{vat})", val["novat"] + val["vat"])
    r += 1
    line("cust", "BUYURTMACHI JIHOZLAR QIYMATI", "=+I{Nf}", val["Nf"])
    rate("cins", "SUG'URTALASH", "cust", "insurance")
    line("cnovat", "JAMI QIYMATI QQS SIZ", "=+I{cust}+I{cins}", val["cust"] + val["cins"])
    line("cvat", f"BUYURTMACHI JIHOZLAR QIYMATI {_pct(rates['vat'])} QQS", "=+I{cnovat}*%s" % rates["vat"], val["cnovat"] * rates["vat"])
    line("ctotal", "BUYURTMACHI JIHOZLAR QIYMATI QQS BILAN", "=+I{cnovat}+I{cvat}", val["cnovat"] + val["cvat"])
    r += 1
    line("pnovat", "PUDRATCHI XARAJATLARI QQS SIZ", "=+I{novat}-I{cnovat}", val["novat"] - val["cnovat"])
    line("pvat", f"PUDRATCHI XARAJATLARI {_pct(rates['vat'])} QQS", "=+I{pnovat}*%s" % rates["vat"], val["pnovat"] * rates["vat"])
    line("ptotal", "PUDRATCHI XARAJATLARI QQS BILAN", "=SUM(I{pnovat}:I{pvat})", val["pnovat"] + val["pvat"])

    ws.repeat_rows(9, 11)
    ws.fit_to_pages(1, 0)
    ws.set_paper(9)  # A4
    wb.close()
    return buf.getvalue()

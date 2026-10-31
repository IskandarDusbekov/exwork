"""Ф-3 — справка-счёт-фактура (oy bo'yicha), texnazor Excel'dagi «Ф-3 №N за <oy>» varag'i bilan bir xil.

Bitta varaq: rekvizitlar, hujjat raqami / sanasi / davri, keyin jadval — shartnoma qiymati (jami va joriy yilga),
boshidan, yil boshidan va shu oyda bajarilgan (pudratchi qismi QQSsiz, QQS, QQS bilan), «Всего к оплате» so'z bilan,
imzolar. Summalar ming so'mda (uning faylidagidek). Formulalar va qiymati birga yoziladi: Excel'da qayta hisoblanadi,
hisoblamaydigan dasturlarda ham ko'rinadi. Uning faylidan farqi: yig'ma summa qo'lda emas — yopilgan oylardan.
"""
from datetime import date
from io import BytesIO

import xlsxwriter

from . import services
from .models import Period

K = 1000.0  # ming so'm
WIDTHS = {"A": 3.6, "B": 62.6, "C": 8.7, "D": 9.3, "E": 9.3, "F": 18.7, "G": 17.9, "H": 7.7, "I": 12.3, "J": 15.7,
          "K": 7.9, "L": 9.1, "M": 15.7, "N": 8.9, "O": 9.0, "P": 15.7}
COLS = "ABCDEFGHIJKLMNOP"


def _th(v: float) -> str:
    """32 235 755 494,95 -> «32 235 755,495» (ming so'm, sarlavha matnida)."""
    return f"{v / K:,.3f}".replace(",", " ").replace(".", ",")


def file_name(period: Period) -> str:
    n = services.f3_meta(period)["num"]
    return f"Ф-3 №{n} {period.title} — {period.obj.name}"[:150] + ".xlsx"


def build(period: Period) -> bytes:
    obj = period.obj
    d = services.f3_data(period)
    ln, meta, vat = d["lines"], d["meta"], d["vat_rate"]
    buf = BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet(f"Ф-3 №{meta['num']} за {Period.MONTHS[period.month - 1]}"[:31])
    cache = {}

    def fmt(bold=True, box=False, align="left", num=None, wrap=True):
        key = (bold, box, align, num, wrap)
        if key not in cache:
            p = {"font_name": "Times New Roman", "font_size": 11, "bold": bold, "align": align, "valign": "vcenter",
                 "text_wrap": wrap}
            if box:
                p["border"] = 1
            if num:
                p["num_format"] = num
            cache[key] = wb.add_format(p)
        return cache[key]

    for c, w in WIDTHS.items():
        ws.set_column(f"{c}:{c}", w)
    ws.set_landscape()
    ws.set_paper(9)
    ws.fit_to_pages(1, 0)
    ws.set_margins(0.4, 0.4, 0.5, 0.5)

    ws.set_row(0, 22.5)
    ws.merge_range("A1:P1", "СПРАВКА СЧЕТ ФАКТУРА О СТОИМОСТИ ВЫПОЛНЕННЫХ РАБОТ (ПОНЕСЕННЫХ ЗАТРАТ)", fmt(align="center"))
    ws.merge_range("A2:P2", f"ЗА {Period.MONTHS[period.month - 1].upper()} МЕСЯЦ {period.year} ГОД", fmt(align="center"))

    # ---- rekvizitlar: chapda buyurtmachi, o'ngda pudratchi ----
    left, right = ln["left"], ln["right"]
    r = 3
    for i in range(max(len(left), len(right))):
        if i < len(left) and left[i].strip():
            ws.merge_range(r + i, 1, r + i, 8, left[i].strip(), fmt(wrap=False))
        if i < len(right) and right[i].strip():
            ws.merge_range(r + i, 10, r + i, 15, right[i].strip(), fmt(wrap=False))
    r += max(len(left), len(right)) + 1

    ws.write(r, 1, "Наименование объекта и его адрес:", fmt())
    ws.merge_range(r, 2, r, 15, obj.name.upper() + (f", {obj.address}" if obj.address else ""), fmt(align="center"))
    r += 1
    ws.merge_range(r, 1, r + 2, 1, ln["contract"], fmt(align="center"))
    ws.merge_range(r, 2, r, 8, f"Общая стоимость в договорных текущих ценах: {_th(d['obj_total'])} тыс.сум", fmt())
    ws.merge_range(r + 1, 2, r + 1, 9, f"Стоимость оборудования, предоставляемого заказчиком:  {_th(d['customer'])} тыс.сум", fmt())
    ws.merge_range(r + 2, 2, r + 2, 7, f"Общая стоимость объекта в текущих ценах   {_th(d['contract']['total'])} тыс.сум", fmt())
    for i in range(3):
        ws.set_row(r + i, 15 if i else 45)
    r += 4

    # ---- hujjat raqami, sanasi, davri ----
    box = fmt(box=True, align="center")
    date_f = fmt(box=True, align="center", num="dd.mm.yyyy")
    ws.merge_range(r, 3, r + 1, 4, "Номер документа", box)
    ws.merge_range(r, 5, r + 1, 6, "Дата составления", box)
    ws.merge_range(r, 9, r, 12, "Отчетный период", box)
    ws.merge_range(r + 1, 9, r + 1, 10, "с", box)
    ws.merge_range(r + 1, 11, r + 1, 12, "по", box)
    ws.merge_range(r + 2, 3, r + 2, 4, meta["num"], box)
    if meta["date"]:
        ws.merge_range(r + 2, 5, r + 2, 6, "", date_f)
        ws.write_datetime(r + 2, 5, meta["date"], date_f)
    else:
        ws.merge_range(r + 2, 5, r + 2, 6, "", date_f)
    if meta["start"]:
        ws.merge_range(r + 2, 9, r + 2, 10, "", date_f)
        ws.write_datetime(r + 2, 9, meta["start"], date_f)
    else:
        ws.merge_range(r + 2, 9, r + 2, 10, "", date_f)
    date_cell = f"F{r + 3}"
    ws.merge_range(r + 2, 11, r + 2, 12, "", date_f)
    if meta["date"]:
        ws.write_formula(r + 2, 11, f"=+{date_cell}", date_f, (meta["date"] - date(1899, 12, 30)).days)
    r += 3

    # ---- jadval sarlavhasi ----
    h = r
    ws.merge_range(h, 0, h + 2, 0, "№", box)
    ws.merge_range(h, 1, h + 2, 1, "Наименование выполненных работ, оборудование и затрат", box)
    ws.merge_range(h, 2, h + 2, 2, "ед.изм.", box)
    ws.merge_range(h, 3, h, 4, "объём работ и затрат в физических показателях", box)
    ws.merge_range(h, 5, h, 6, "стоимость в договорных текущих ценах (тыс.сум)", box)
    ws.merge_range(h, 7, h, 15, "выполненые работы (понесенные затраты)", box)
    ws.merge_range(h + 1, 3, h + 2, 3, "всего", box)
    ws.merge_range(h + 1, 4, h + 2, 4, "в.т.ч.на текущий год", box)
    ws.merge_range(h + 1, 5, h + 2, 5, "всего", box)
    ws.merge_range(h + 1, 6, h + 2, 6, "в.т.ч.на текущий год", box)
    ws.merge_range(h + 1, 7, h + 1, 9, "с начала строительства", box)
    ws.merge_range(h + 1, 10, h + 1, 12, "с начала года по отчетный месяц включительно", box)
    ws.merge_range(h + 1, 13, h + 1, 15, "в том числе за отчетный месяц", box)
    for c in (7, 10, 13):
        ws.write(h + 2, c, "в физических показателях", box)
        ws.write(h + 2, c + 1, "в % к объёму", box)
        ws.write(h + 2, c + 2, "в договорных текущих ценах (тыс.сум)", box)
    for c in range(16):
        ws.write(h + 3, c, c + 1, box)
    for i, ht in enumerate((66.75, 27.75, 77.25, 16.5)):
        ws.set_row(h + i, ht)
    r = h + 4

    # ---- qatorlar ----
    o, e, nv, vt, tt, words = r, r + 2, r + 4, r + 5, r + 6, r + 7  # 0-based qator raqamlari
    R = lambda row: row + 1  # noqa: E731 — Excel raqami
    money = lambda b=False: fmt(bold=b, box=True, align="center", num="#,##0.000")  # noqa: E731
    phys = lambda b=False: fmt(bold=b, box=True, align="center", num="#,##0.00")  # noqa: E731
    pct = lambda b=False: fmt(bold=b, box=True, align="center", num="0%")  # noqa: E731
    plain = lambda b=False, a="center": fmt(bold=b, box=True, align=a)  # noqa: E731
    for row in range(o, tt + 1):
        for c in range(16):
            ws.write_blank(row, c, None, plain())
    c_, y_, cum, ycum, this = d["contract"], d["year"], d["cum"], d["ycum"], d["this"]

    def f(row, col, formula, value, style):
        ws.write_formula(row, col, formula, style, value)

    def ratios(row, b, F_val, G_val, cum_v, ycum_v, this_v):
        """H..P: boshidan (F ga nisbatan), yil boshidan (G ga), shu oy (F ga)."""
        x = R(row)
        q = lambda a, z: a / z if z else 0.0  # noqa: E731
        f(row, 7, f"=+J{x}/F{x}", q(cum_v, F_val), phys(b))
        f(row, 8, f"=+J{x}/F{x}", q(cum_v, F_val), pct(b))
        f(row, 10, f"=+L{x}", q(ycum_v, G_val), phys(b))
        f(row, 11, f"=+M{x}/G{x}", q(ycum_v, G_val), pct(b))
        f(row, 13, f"=+O{x}", q(this_v, F_val), phys(b))
        f(row, 14, f"=+P{x}/F{x}", q(this_v, F_val), pct(b))

    # obyekt
    ws.write(o, 0, 1, plain())
    ws.write(o, 1, obj.name.upper(), plain(a="left"))
    ws.write(o, 2, "Объект", plain())
    ws.write(o, 3, 1, plain())
    ws.write(o, 4, 1, plain())
    f(o, 5, f"=+F{R(e)}+F{R(tt)}", d["obj_total"] / K, money())
    f(o, 6, f"=+G{R(e)}+G{R(tt)}", d["obj_year"] / K, money())
    f(o, 9, f"=+J{R(tt)}", cum["total"] / K, money())
    f(o, 12, f"=+M{R(tt)}", ycum["total"] / K, money())
    f(o, 15, f"=+P{R(tt)}", this["total"] / K, money())
    ratios(o, False, d["obj_total"], d["obj_year"], cum["total"], ycum["total"], this["total"])
    ws.set_row(o, 43.5)
    ws.write(o + 1, 1, "Из них:", plain(a="left"))
    # buyurtmachi uskunasi — pudratchiga to'lanmaydi, faqat shartnoma qiymatida
    ws.write(e, 1, "Стоимость оборудования, предоставляемого заказчиком", plain(True, "left"))
    ws.write_number(e, 5, d["customer"] / K, money())
    ws.write_number(e, 6, d["customer"] / K, money())
    # pudratchi: QQSsiz (qiymat), QQS, QQS bilan
    vp = f"{vat * 100:g}"
    ws.write(nv, 1, "Стоимость подрядных работ без НДС", plain(False, "left"))
    ws.write(vt, 1, f"НДС {vp}%", plain(False, "left"))
    ws.write(tt, 1, "ВСЕГО С НДС", plain(True, "left"))
    for row, b in ((nv, False), (vt, False), (tt, True)):
        ws.write(row, 2, "СУМ", plain(b))
    ws.write_number(nv, 5, c_["novat"] / K, money())
    ws.write_number(nv, 6, y_["novat"] / K, money())
    ws.write_number(nv, 15, this["novat"] / K, money())
    plus = lambda v: f"={v / K!r}+P{R(nv)}" if abs(v) > 1e-9 else f"=+P{R(nv)}"  # noqa: E731 — oldingi oylar (qiymat) + shu oy
    f(nv, 9, plus(d["before"]["novat"]), cum["novat"] / K, money())
    f(nv, 12, plus(d["ybefore"]["novat"]), ycum["novat"] / K, money())
    for col, key in ((5, "contract"), (6, "year"), (9, "cum"), (12, "ycum"), (15, "this")):
        L = COLS[col]
        f(vt, col, f"=+{L}{R(nv)}*{vat!r}", d[key]["vat"] / K, money())
        f(tt, col, f"={L}{R(nv)}+{L}{R(vt)}", d[key]["total"] / K, money(True))
    ratios(nv, False, c_["novat"], y_["novat"], cum["novat"], ycum["novat"], this["novat"])
    ratios(tt, True, c_["total"], y_["total"], cum["total"], ycum["total"], this["total"])

    # «Всего к оплате» — shu oy, QQS bilan, so'z bilan
    ws.write(words, 1, "Всего к оплате", plain(True))
    ws.write_blank(words, 0, None, plain())
    ws.write_blank(words, 2, None, plain())
    ws.write_blank(words, 3, None, plain())
    ws.merge_range(words, 4, words, 15, d["words"], plain(True))
    ws.set_row(words, 30)
    r = words + 2

    # ---- imzolar ----
    lt, rt = ln["sign_left"], ln["sign_right"]
    for i in range(max(len(lt), len(rt))):
        if i < len(lt) and lt[i].strip():
            ws.merge_range(r + i, 1, r + i, 4, lt[i].strip(), fmt(align="center" if i == 0 else "left", wrap=False))
        if i < len(rt) and rt[i].strip():
            ws.merge_range(r + i, 9, r + i, 15, rt[i].strip(), fmt(align="center" if i == 0 else "left", wrap=False))
        ws.set_row(r + i, 25.5)
    r += max(len(lt), len(rt)) + 1
    ws.write(r, 1, "Технический надзор:", fmt())
    ws.merge_range(r, 2, r, 3, "____________________", fmt(align="center"))
    ws.merge_range(r, 4, r, 6, ln["texnazor"], fmt(wrap=False))
    ws.print_area(0, 0, r, 15)

    wb.close()
    return buf.getvalue()

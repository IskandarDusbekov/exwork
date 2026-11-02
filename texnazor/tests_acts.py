"""Texnazor — dalolatnomalar tartibi: list tanlash, ofyorta fayldagidek (bir nechta lokal, «6,1» raqamlar, ruscha
svodka formulalar bilan), №1 / №2 / Остатка, hajm chegarasi, «Хисобот» va «Ф-2 №N» Excel, eski oylardan o'tish."""
import io
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from accounts.models import User

from . import services_acts as sa
from . import tasks
from .models import Act, ActQty, Done, Item, Period, SiteObject
from .sheet import READER

TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_tn_acts_")


def build_sahovat(path):
    """«Оферта саховат» tuzilishi: ikki lokal (har birida sarlavha va «1 2 3» qatori, raqamlar 1 dan), «РАЗДЕЛ» A da,
    resurs «2,1» vergul bilan, turlar I–L (sarlavhasiz), svodka ruscha va formulalar bilan."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Оферта "
    wb.create_sheet("Ф-2 №1")["A1"] = "эски акт"
    ws["B2"] = "Саховат МФИ 36-мактаб спорт майдони"
    ws["B3"] = "(наименование стройки)"
    ws.merge_cells("B2:F2")
    ws.column_dimensions["C"].width = 60
    r = 7

    def header():
        nonlocal r
        for j, v in enumerate(["N п.п.", "Шифр номера нормативов", "Наименование работ и затрат", "Единица измерения",
                               "Количество ", None, "Стоимость"], start=1):
            c = ws.cell(row=r, column=j, value=v)
            c.fill, c.font = PatternFill("solid", fgColor="CCFFFF"), Font(bold=True, color="FF0000")
        for j, v in enumerate(["на. ед. измерения", "по проектным данным", "на единицу измерения", "сумма"], start=5):
            ws.cell(row=r + 1, column=j, value=v)
        for j in range(1, 9):
            ws.cell(row=r + 2, column=j, value=j)
        r += 3

    def put(*vals):
        nonlocal r
        for j, v in enumerate(vals, start=1):
            if v is not None:
                ws.cell(row=r, column=j, value=v)
        r += 1
        return r - 1

    def res(num, name, unit, norm, total, price, cat):
        row = put(num, "1", name, unit, norm, total, price, None)
        ws.cell(row=row, column=8, value=f"=G{row}*F{row}")
        ws.cell(row=row, column=cat, value=f"=H{row}")
        return row

    def free(num, name, unit, qty, price, cat):
        row = put(num, "СЦЕНА", name, unit, qty, None, price)
        ws.cell(row=row, column=8, value=f"=G{row}*E{row}")
        ws.cell(row=row, column=cat, value=f"=H{row}")
        return row

    header()
    first = r
    put("РАЗДЕЛ 1. РЕЗИНОВОЕ ПОКРЫТИЕ")
    put("1", "Е2704-001-04", "УСТРОЙСТВО ОСНОВАНИЙ ИЗ ЩЕБНЯ", "100М3", 2)
    res("1.1", "ЗАТРАТЫ ТРУДА", "ЧЕЛ.-Ч", 20, 40, 1000, 9)
    res("1.2", "АВТОГРЕЙДЕР", "МАШ.-Ч", 1, 2, 0, 10)
    res("1.3", "ЩЕБЕНЬ", "М3", 100, 200, 500, 11)
    free("2", "ПОЛИЭТИЛЕН ПЛЕНКА", "М2", 10, 300, 11)
    put("на ", "КОЗЫРЕК, АС")
    put(None, "(наименование работ и затрат, наименование объекта)")
    put("Основание:")
    header()
    put(None, None, "КОЗЫРЕК")
    put(1, "Е1501-091-01", "ОБЛИЦОВКА ФАСАДА", "100М2", 4)
    res("1,1", "ЗАТРАТЫ ТРУДА", "ЧЕЛ.-Ч", 10, 40, 1000, 9)
    res("1,2", "ВИНТЫ", "ШТ", 5, 20, 200, 11)
    put("РАЗДЕЛ 3. ОБОРУДОВАНИЯ")
    free(2, "ВОЛЕЙБОЛЬНАЯ СТОЙКА", "К-Т", 2, 100000, 12)
    last = r - 1
    t = put()
    for col in "HIJKL":
        ws[f"{col}{t}"] = f"=SUM({col}{first}:{col}{last})"
    s = {}
    for key, label, f, h in (("zp", "ЗАРПЛАТА", None, f"=I{t}"), ("mash", "ЭКСПЛУАТАЦИЯ МАШИН", None, None),
                             ("mat", "СТРОИТЕЛЬНЫЕ МАТЕРИАЛЫ", None, f"=K{t}"),
                             ("tr", "ТРАНСПОРТНЫЕ РАСХОДЫ", 0, "=H{mat}*F{row}"),
                             ("all", "ВСЕГО", None, "=SUM(H{zp}:H{tr})"),
                             ("pr", "ПРОЧИЕ ЗАТРАТЫ ПОДРЯЧИКА 17,27%", 0.1727, "=H{all}*F{row}"),
                             ("eq", "ОБОРУДОВАНИЕ", None, f"=L{t}"),
                             ("itogo", "ИТОГО ", None, "=SUM(H{all}:H{eq})"),
                             ("nds", "НДС 12%", 0.12, "=H{itogo}*F{row}"),
                             ("total", "ВСЕГО", None, "=H{itogo}+H{nds}")):
        row = put(None, None, label, "СУМ")
        s[key] = row
        if f is not None:
            ws[f"F{row}"] = f
        if h:
            ws[f"H{row}"] = h.format(row=row, **s)
    put()
    put(None, None, 'ООО "WORLD STORY HOME" ________________')
    wb.save(path)
    return path


def expected_total(work1=2.0, free2=10.0, work_k=4.0, stand=2.0):
    """Shu ofyortaning svodkasi qo'lda: ish haqi / material / uskuna -> 17,27% -> QQS 12%."""
    zp = 1000 * 20 * work1 + 1000 * 10 * work_k
    mat = 500 * 100 * work1 + 300 * free2 + 200 * 5 * work_k
    eq = 100000 * stand
    allv = zp + mat
    itogo = allv + allv * 0.1727 + eq
    return itogo * 1.12


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class ActsFlowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        Path(TMP_MEDIA).mkdir(parents=True, exist_ok=True)
        self.user = User.objects.create_user("acts@test.uz", "Kuchli-parol-2026", first_name="A", last_name="B")
        self.client.force_login(self.user)
        with open(build_sahovat(f"{TMP_MEDIA}/sahovat.xlsx"), "rb") as f:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("texnazor:create"), {"ofyorta": SimpleUploadedFile("Оферта саховат.xlsx", f.read())})
        self.obj = SiteObject.objects.get()
        tasks.run_now(self.obj.pk)
        self.obj.refresh_from_db()

    def ready(self):
        self.assertEqual(self.obj.status, SiteObject.Status.CHOOSE, self.obj.error)
        r = self.client.post(reverse("texnazor:choose_sheet", args=[self.obj.pk]), {"sheet": "Оферта "})
        self.assertEqual(r.status_code, 302)
        tasks.run_now(self.obj.pk)
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.status, SiteObject.Status.READY, self.obj.error)
        return sa.book(self.obj)

    def test_sheet_choice_is_offered_with_guess(self):
        self.assertEqual(self.obj.status, SiteObject.Status.CHOOSE)
        guess = [s["name"] for s in self.obj.sheets if s.get("guess")]
        self.assertEqual(guess, ["Оферта "])
        r = self.client.get(reverse("texnazor:object", args=[self.obj.pk]))
        self.assertContains(r, "Қайси лист офёрта?")
        self.assertContains(r, "тизим таклифи")
        self.assertContains(r, "Тасдиқлаш — шу листни ўқиш")
        r = self.client.post(reverse("texnazor:choose_sheet", args=[self.obj.pk]), {"sheet": "йўқ"})
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.status, SiteObject.Status.CHOOSE)  # нотўғри лист — ўқилмайди

    def test_read_as_is_rows_and_contract(self):
        b = self.ready()
        kinds = {r["row"]: (r["kind"], r["num"], r["parent"]) for r in b.rows}
        works = [r for r in b.rows if r["kind"] == "work"]
        self.assertEqual([w["num"] for w in works], ["1", "1"])  # иккинчи локал — рақамлар 1 дан
        comma = [r for r in b.rows if r["num"] == "1.1" and r["kind"] == "res"]
        self.assertEqual(len(comma), 2)  # «1,1» ҳам ресурс
        self.assertEqual(kinds[comma[1]["row"]][2], works[1]["row"])
        texts = [r["vals"] for r in b.rows if r["kind"] == "text"]
        self.assertIn(["РАЗДЕЛ 1. РЕЗИНОВОЕ ПОКРЫТИЕ"], [[v for v in t if v] for t in texts])
        self.assertFalse(any(r["kind"] in ("work", "free") and r["vals"][2] == 3 for r in b.rows))  # «1 2 3» — иш эмас
        t = sa.totals(self.obj)
        self.assertAlmostEqual(t["contract"], expected_total(), places=4)
        self.assertEqual(t["done"], 0)

    def test_acts_rest_limit_and_page(self):
        b = self.ready()
        w1, w2 = [r["row"] for r in b.rows if r["kind"] == "work"]
        stand = next(r["row"] for r in b.rows if r["kind"] == "free" and "СТОЙКА" in str(r["vals"][2]))
        self.client.post(reverse("texnazor:act_add", args=[self.obj.pk]), {"date": "2026-10-31"})
        self.client.post(reverse("texnazor:act_add", args=[self.obj.pk]), {})
        a1, a2 = Act.objects.order_by("number")
        self.assertEqual((a1.title, a1.date.isoformat(), a2.title, a2.date), ("№1", "2026-10-31", "№2", None))
        url = reverse("texnazor:qty", args=[self.obj.pk])
        post = lambda d: self.client.post(url, json.dumps(d), content_type="application/json")  # noqa: E731
        r = post({"act": 1, "row": w1, "qty": "1,5"})
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertAlmostEqual(d["sum"], expected_total(1.5, 0, 0, 0), places=4)
        self.assertIn(w1 + 1, [int(k) for k in d["v"]])  # ресурс ҳам қайта ҳисобланди
        self.assertAlmostEqual(post({"act": 2, "row": stand, "qty": "2"}).json()["sum"], expected_total(0, 0, 0, 2), places=4)
        r = post({"act": 2, "row": w1, "qty": "0,6"})  # қолдиқ 0,5
        self.assertEqual(r.status_code, 400)
        self.assertIn("Қолдиқ 0,5", r.json()["error"])
        self.assertEqual(post({"act": 2, "row": w1 + 1, "qty": "1"}).status_code, 400)  # ресурс қаторига ёзилмайди
        t = sa.totals(self.obj)
        self.assertAlmostEqual(t["left"], expected_total() - expected_total(1.5, 0, 0, 0) - expected_total(0, 0, 0, 2), places=3)
        data = self.client.get(reverse("texnazor:data", args=[self.obj.pk])).json()
        self.assertEqual([a["n"] for a in data["acts"]], [1, 2])
        self.assertAlmostEqual(data["rest_sum"], t["left"], places=3)
        self.assertEqual(data["contract"][str(w2)], 4)
        page = self.client.get(reverse("texnazor:object", args=[self.obj.pk]) + "?view=report")
        self.assertContains(page, "Далолатнома №1")
        self.assertContains(page, "Остатка")
        # кирганда — «Далолатнома» кўриниши: охирги очиқ далолатнома ва Остатка (жадвалда фақат шулар)
        page = self.client.get(reverse("texnazor:object", args=[self.obj.pk]))
        self.assertEqual(page.context["view"], "act")
        self.assertEqual(page.context["active"].number, 2)
        self.assertContains(page, 'data-v="act" data-n="2"')
        # ёпилган далолатнома ўзгармайди
        self.client.post(reverse("texnazor:act_edit", args=[self.obj.pk, 1]), {"do": "close"})
        self.assertEqual(post({"act": 1, "row": w1, "qty": "1"}).status_code, 400)

    def test_report_and_f2_excel_keep_file_formulas(self):
        b = self.ready()
        w1 = next(r["row"] for r in b.rows if r["kind"] == "work")
        a = sa.add_act(self.obj)
        sa.set_qty(a, w1, 1.0)
        r = self.client.get(reverse("texnazor:report", args=[self.obj.pk]))
        self.assertEqual(r.status_code, 200)
        wb = load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Офёрта"])  # фақат нархланган офёрта; Ф-2 — алоҳида
        ws = wb["Офёрта"]
        lay = b.lay
        start = lay.block_end + 1  # №1 блоки: охирги устундан кейин дарҳол (бўш устунсиз) E..L нусхаси
        res = w1 + 1
        col = lambda j: ws.cell(row=res, column=start + (j - lay.qty) + 1)  # noqa: E731
        self.assertEqual(ws.cell(row=w1, column=start + 1).value, 1.0)  # иш ҳажми
        self.assertEqual(col(lay.total).value, 20)  # 40 × 1/2
        self.assertTrue(str(col(lay.amount).value).startswith("="))
        svod_total = b.final_cell()[1]
        self.assertIn("+", ws.cell(row=svod_total, column=start + lay.amount - lay.qty + 1).value)
        # turlar ustunlari (summadan keyin) yashirin — ofyortada ham, blokda ham; ustun yozuvlari ustma-ust tushmaydi
        from openpyxl.utils import get_column_letter as L_
        for c in (lay.amount + 1, start + lay.amount + 1 - lay.qty):
            self.assertTrue(ws.column_dimensions[L_(c + 1)].hidden, L_(c + 1))
        self.assertFalse(ws.column_dimensions[L_(lay.amount + 1)].hidden)
        for sheet in wb.worksheets:
            spans = sorted((d.min, d.max) for d in sheet.column_dimensions.values() if d.min)
            self.assertTrue(all(x[1] < y[0] for x, y in zip(spans, spans[1:])), (sheet.title, spans))
        # охирги блокдан ўнгда эски устунлар қолмайди; пастда қўшимча жадвал йўқ (свод — файлдагидек)
        width = lay.block_end - lay.qty + 1
        self.assertLessEqual(ws.max_column, start + 2 * width)
        self.assertNotIn("УМУМИЙ НАТИЖА", [ws.cell(row=i, column=lay.name + 1).value for i in range(1, ws.max_row + 1)])
        # блок устунлари Excel гуруҳида; сайтда йиғилгани Excel'да ҳам йиғилган (сумма устуни кўринади)
        qcol = ws.column_dimensions[L_(start + 1)]
        self.assertEqual((qcol.outline_level, qcol.hidden), (1, False))
        r = self.client.get(reverse("texnazor:report", args=[self.obj.pk]) + "?coll=rest")
        ws2 = load_workbook(io.BytesIO(r.content)).worksheets[0]
        rq = ws2.column_dimensions[L_(start + width + 1)]
        self.assertEqual((rq.outline_level, rq.hidden), (1, True))
        self.assertFalse(ws2.column_dimensions[L_(start + width + lay.amount - lay.qty + 1)].hidden)
        self.assertFalse(ws2.column_dimensions[L_(start + 1)].hidden)
        r = self.client.get(reverse("texnazor:act_f2", args=[self.obj.pk, 1]))
        f2 = load_workbook(io.BytesIO(r.content))["Ф-2 №1"]
        self.assertTrue(f2.column_dimensions[L_(lay.amount + 2)].hidden)
        names = [f2.cell(row=i, column=3).value for i in range(1, f2.max_row + 1)]
        self.assertIn("УСТРОЙСТВО ОСНОВАНИЙ ИЗ ЩЕБНЯ", names)
        self.assertNotIn("ОБЛИЦОВКА ФАСАДА", names)

    def test_sheet_look_like_excel_and_old_objects_reread(self):
        """Saytda list Excel'dagidek: ustun kengligi, fon / shrift rangi, birlashtirilgan kataklar. Eski qoidalar bilan
        o'qilgan obyekt ochilganda o'zi qayta o'qiladi (dalolatnomalar saqlanadi)."""
        b = self.ready()
        look = self.client.get(reverse("texnazor:data", args=[self.obj.pk])).json()["look"]
        self.assertEqual(look["cols"][2], 425)  # C: 60 belgi
        head = b.lay.header
        css = look["styles"][look["pats"][look["rows"][str(head)]][2]][0]
        self.assertIn("background:#ccffff", css)
        self.assertIn("color:#ff0000", css)
        self.assertIn("font-weight:700", css)
        self.assertIn([2, 1, 2, 5], look["merges"])

        w1 = next(r["row"] for r in b.rows if r["kind"] == "work")
        sa.set_qty(sa.add_act(self.obj), w1, 1)
        self.obj.layout = {k: v for k, v in self.obj.layout.items() if k != "reader"}
        self.obj.save(update_fields=["layout"])
        with patch.object(tasks, "start") as start:
            r = self.client.get(reverse("texnazor:object", args=[self.obj.pk]))
        self.assertEqual(r.status_code, 302)
        start.assert_called_once_with(self.obj.pk)
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.status, SiteObject.Status.PARSING)
        tasks.run_now(self.obj.pk)
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.status, SiteObject.Status.READY, self.obj.error)
        self.assertEqual(self.obj.layout["reader"], READER)
        self.assertEqual(sa.qty_of(self.obj.acts.get()), {w1: 1.0})

    def test_percent_entry_and_mark_selected_done(self):
        """Katakka «50%» (ofyortadagi hajmdan); belgilangan ishlar «жами N%» — yetmagani tanlangan dalolatnomaga."""
        b = self.ready()
        w1, w2 = [r["row"] for r in b.rows if r["kind"] == "work"]
        base1, base2 = b.contract_qty(w1), b.contract_qty(w2)
        a1 = sa.add_act(self.obj)
        post = lambda url, d: self.client.post(reverse(url, args=[self.obj.pk]), json.dumps(d), content_type="application/json")  # noqa: E731
        r = post("texnazor:qty", {"act": 1, "row": w1, "qty": "50%"}).json()
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["qty"], base1 / 2)
        self.assertIn(str(w1), r["v"])
        r = post("texnazor:qty", {"act": 1, "row": w1, "qty": ""}).json()  # o'chirilsa (Ctrl+Z) — hisoblangan pul ham qolmaydi
        kids = sa.book(self.obj).children.get(w1, [])
        self.assertTrue(all(str(x) not in r["v"] for x in [w1] + kids), r["v"])
        post("texnazor:qty", {"act": 1, "row": w1, "qty": "50%"})
        a2 = sa.add_act(self.obj)
        r = post("texnazor:act_fill", {"act": 2, "rows": [w1, w2], "pct": 100}).json()
        self.assertTrue(r["ok"], r)
        self.assertEqual(sa.qty_of(a1), {w1: base1 / 2})  # oldingisi o'zgarmaydi
        q2 = sa.qty_of(a2)
        self.assertAlmostEqual(q2[w1], base1 / 2)
        self.assertAlmostEqual(q2[w2], base2)
        rest = sa.rest_qty(sa.book(self.obj), sa.all_qty(self.obj))
        self.assertAlmostEqual(rest.get(w1, 0), 0)
        self.assertAlmostEqual(rest.get(w2, 0), 0)
        d = self.client.get(reverse("texnazor:data", args=[self.obj.pk])).json()  # 100% — «Остатка»да ҳеч нарса қолмайди
        kids = [c for c in sa.book(self.obj).children.get(w1, [])]
        for row in [w1, w2] + kids:
            self.assertNotIn(str(row), d["rest"])
        r = post("texnazor:act_fill", {"act": 2, "rows": [w1], "pct": 30}).json()  # 30% < №1 dagi 50% — №2 ga 0
        self.assertNotIn(w1, sa.qty_of(a2))
        self.assertEqual(post("texnazor:act_fill", {"act": 2, "rows": [w1], "pct": 120}).status_code, 400)

    def test_rename_and_delete_any_open_act(self):
        """Nom beriladi; o'rtadagi ochiq dalolatnoma o'chsa — keyingilari bittaga suriladi, hajmlari o'zi bilan."""
        b = self.ready()
        w1 = next(r["row"] for r in b.rows if r["kind"] == "work")
        a1, a2, a3 = (sa.add_act(self.obj) for _ in range(3))
        sa.set_qty(a3, w1, 0.5)
        edit = lambda n, **d: self.client.post(reverse("texnazor:act_edit", args=[self.obj.pk, n]), d)  # noqa: E731
        edit(2, do="name", name="  Пойдевор   ишлари ")
        a2.refresh_from_db()
        self.assertEqual((a2.name, a2.label), ("Пойдевор ишлари", "№2 · Пойдевор ишлари"))
        self.assertContains(self.client.get(reverse("texnazor:object", args=[self.obj.pk])), "Пойдевор ишлари")
        edit(2, do="delete", confirm="№2")
        self.assertEqual([(a.number, a.pk) for a in self.obj.acts.order_by("number")], [(1, a1.pk), (2, a3.pk)])
        self.assertEqual(sa.qty_of(Act.objects.get(pk=a3.pk)), {w1: 0.5})
        a3.refresh_from_db()
        a3.closed = True
        a3.save()
        edit(1, do="delete", confirm="№1")  # keyingisi yopilgan — o'chmaydi
        self.assertEqual(self.obj.acts.count(), 2)

    def test_old_months_become_numbered_acts(self):
        b = self.ready()
        w1 = next(r["row"] for r in b.rows if r["kind"] == "work")
        o = self.obj
        o.mode = SiteObject.Mode.MONTHS
        o.save(update_fields=["mode"])
        o.acts.all().delete()
        it = Item.objects.create(obj=o, order=1, excel_row=w1, kind="work", name="x", qty=2)
        for y, m, q in ((2026, 9, 0.5), (2026, 7, 1.0)):
            Done.objects.create(period=Period.objects.create(obj=o, year=y, month=m, closed=m == 7), item=it, qty=q)
        sa.import_raw(o, sa._xlsx(o))
        acts = list(o.acts.order_by("number"))
        self.assertEqual([(a.number, a.date.month, a.closed) for a in acts], [(1, 7, True), (2, 9, False)])
        self.assertEqual([sa.qty_of(a) for a in acts], [{w1: 1.0}, {w1: 0.5}])
        self.assertEqual(SiteObject.objects.get(pk=o.pk).mode, SiteObject.Mode.ACTS)
        self.assertFalse(ActQty.objects.filter(qty=0).exists())

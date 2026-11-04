"""Texnazor — formulasiz bo'lim svodkalari («ФИЗ ОБЪЁМ.xls», «ОФЁРТА кесим» kabi): smeta dasturidan chiqqan fayl,
har bo'lim ostida «ИТОГО ПО РАЗДЕЛУ» — «ЗАТРАТЫ ТРУДА … / ЕСП 15% / ИТОГО ПРЯМЫЕ / ИТОГО С УЧЕТОМ КОЭФФ.» son bo'lib
yozilgan, oxirida «ВСЕГО ПО ОБЪЕКТУ»; narxlar matn («4500»). Bu sonlar dalolatnomaga o'zgarmay tushmasligi, hajmsiz
dalolatnoma 0, yarim hajm 50% bo'lishi; belgilash sahifasi tez ochilishi va «Шундай давом этиш» eslab qolinishi."""
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from accounts.models import User

from . import services_acts as sa
from . import tasks
from .models import SiteObject
from .sheet import Book, read

TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_tn_svod_")
SHEET = "ОФЁРТА кесим"

# bo'limlar: 1 — 107 800 so'm ishlar (ish haqi 90 000), 2 — 22 500 (hammasi ish haqi)
S1_LABOR, S1_MAT, S2_LABOR = 90000.0, 17800.0, 22500.0


def _svod_block(put, labor, mat, title="ИТОГО ПО РАЗДЕЛУ"):
    """Smeta dasturining bo'lim jami: sonlar (formulasiz), faqat «ИТОГО» qatorlari formula — xuddi fayldagidek.
    Birligi «СУМ» E ustunida (D bo'sh)."""
    put(None, "РАЗДЕЛ", title)
    a = put(1, None, "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ БЕЗ ОТЧИСЛЕНИЯ ЕСП 15%", None, "СУМ", None, None, labor)
    b = put(2, None, "ОТЧИСЛЕНИЯ ЕСП 15%", None, "СУМ", None, 0.15, round(labor * 0.15))
    c = put(3, None, "ИТОГО ЗАТРАТЫ ТРУДА", None, "СУМ", None, None, f"=H{b}+H{a}")
    d = put(4, None, "СТРОИТЕЛЬНЫЕ МАТЕРИАЛЫ", None, "СУМ", None, None, mat)
    e = put(5, None, "ИТОГО ПРЯМЫЕ ЗАТРАТЫ", None, "СУМ", None, None, f"=H{d}+H{c}")
    total = (labor + round(labor * 0.15) + mat) * 1.175
    put(6, None, "ИТОГО С УЧЕТОМ КОЭФФ. 17,5 %", None, "СУМ", None, 1.175, total)
    return e, total


def build_fiz(path, heading=False):
    wb = Workbook()
    ws = wb.active
    ws.title = "ФИЗ.ОБЁМ"
    ws = wb.create_sheet(SHEET)
    wb.create_sheet("ФОРМА №2")["A1"] = "акт"
    r = 1

    def put(*vals):
        nonlocal r
        for j, v in enumerate(vals, start=1):
            if v is not None:
                ws.cell(row=r, column=j, value=v)
        r += 1
        return r - 1

    put("НАИМЕНОВАНИЕ СТРОЙКИ: Кап.ремонт здания центра культуры")
    put("ЛОКАЛЬНАЯ РЕСУРСНАЯ ВЕДОМОСТЬ")
    put("№", "Шифр", "Наименование", "Единица измерения", "Количество", None, "Стоимость")
    put(None, None, None, None, "На ед. измерения", "По проектным данным", "цена", "сумма")
    put(1, 2, 3, 4, 5, 6, 7, 8)
    put(None, "РАЗДЕЛ", "КРОВЛЯ")
    put(1, "E58-17-4", "РАЗБОРКА ПОКРЫТИЙ КРОВЕЛЬ", "100М2", 2)
    put("1 .1", 1, "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ-Ч", 10, 20, "4500", 90000)  # narx matn, summa — son
    put("1 .2", 31053, "КРАСКА", "Т", 1, 2, "5000", 10000)
    put("1 .3", 6237, "ПРОЧИЕ МАТЕРИАЛЫ", "СУМ", 0, 0, 0, 0)  # resurs pul birligida — svodka emas
    put("1 .4", "С113-1009", "ЖЕЛОБА КРАШЕННЫЕ СТАЛЬНЫЕ", "М", 48, None, "100", 4800)  # jami yozilmagan: narx × E
    if heading:  # bo'lim ichidagi sarlavha (jamisiz) — uning ishlari keyingi «ИТОГО ПО РАЗДЕЛУ» ga kiradi
        put(None, "РАЗДЕЛ", "ПОДПОЛНЫЕ КАНАЛЫ")
    put(2, "С1534-15", "КОЛЕНО", "ШТ", 3, None, "1000", 3000)
    _svod_block(put, S1_LABOR, S1_MAT)
    put(None, "РАЗДЕЛ", "ПРОЧИЕ РАБОТЫ")
    put(3, "E69-1-3", "ПРОБИВКА ОТВЕРСТИЙ", "100 ОТВЕРСТИЙ", 1)
    put("3 .1", 1, "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ-Ч", 5, 5, "4500", 22500)
    _svod_block(put, S2_LABOR, 0)
    _e, grand = _svod_block(put, S1_LABOR + S2_LABOR, S1_MAT, "ВСЕГО ПО ОБЪЕКТУ")
    wb.save(path)
    return Path(path), grand, r - 1


class SvodNoFormulaReadTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dir = Path(tempfile.mkdtemp(prefix="narxlash_tn_fiz_"))
        cls.path, cls.grand, cls.last = build_fiz(cls.dir / "fiz.xlsx")
        cls.grid = read(cls.path, SHEET)
        cls.b = Book(cls.grid.layout.as_dict(), cls.grid.rows)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)
        super().tearDownClass()

    def kind(self, row):
        return self.b.by_row[row]["kind"]

    def test_svod_rows_are_not_works_and_money_unit_resource_is_resource(self):
        kinds = {r["row"]: r["kind"] for r in self.grid.rows}
        self.assertEqual([kinds[i] for i in (7, 8, 9, 10, 11, 12)], ["work", "res", "res", "res", "res", "free"])
        self.assertTrue(all(kinds[i] == "svod" for i in range(13, 20)), kinds)  # «ИТОГО ПО РАЗДЕЛУ» bloki
        self.assertEqual(sorted(self.b.tops), [7, 12, 21])

    def test_text_prices_get_amount_formula(self):
        self.assertEqual(self.b.by_row[8]["fmls"].get(7), "=G8*F8")
        self.assertEqual(self.b.by_row[11]["fmls"].get(7), "=G11*E11")  # jami yo'q — narx × E (ishga mutanosib)
        self.assertEqual(self.b.fixed_money(), [])

    def test_self_check_passes(self):
        c = self.b.self_check()
        self.assertTrue(c["ok"], c["fails"])
        self.assertAlmostEqual(c["full"], self.grand, places=2)
        self.assertAlmostEqual(c["empty"], 0.0, places=6)
        self.assertAlmostEqual(c["half"] / c["full"], 0.5, places=9)

    def test_groups_map_to_own_section_and_object_total_to_all(self):
        gs = {g["rows"][0]: g for g in self.b.svod_groups()}
        self.assertEqual(len(gs), 3, gs)
        first, second, total = (gs[k] for k in sorted(gs))
        self.assertEqual(first["tops"], [7, 12])
        self.assertEqual(second["tops"], [21])
        self.assertEqual(total["tops"], [7, 12, 21])  # «ВСЕГО ПО ОБЪЕКТУ» — hamma ishlar
        self.assertAlmostEqual(first["base"], S1_LABOR + S1_MAT, places=4)

    def test_act_of_first_section_only(self):
        H = 7
        ev = self.b.evaluate({7: 2.0, 12: 3.0})  # 1-bo'lim to'liq, 2-bo'lim — yo'q
        self.assertAlmostEqual(ev(H, 14), S1_LABOR, places=4)  # 1-bo'lim jami fayldagidek
        self.assertAlmostEqual(ev(H, 16), S1_LABOR * 1.15, places=4)  # formula ham
        self.assertAlmostEqual(ev(H, 24), 0.0, places=6)  # 2-bo'lim ish haqi — 0
        share = (S1_LABOR + S1_MAT) / (S1_LABOR + S1_MAT + S2_LABOR)  # «ВСЕГО ПО ОБЪЕКТУ» — hamma ishlar pulidan ulushi
        self.assertAlmostEqual(ev(H, 31), (S1_LABOR + S2_LABOR) * share, places=4)
        self.assertAlmostEqual(ev(H, 11), 4800.0, places=4)
        self.assertAlmostEqual(self.b.evaluate({7: 1.0, 12: 1.5})(H, 11), 2400.0, places=4)  # E ham mutanosib
        self.assertIsNone(self.b.evaluate({7: 1.0})(5, 11))  # jami (F) bo'shligicha qoladi
        self.assertEqual(self.b.svod_warnings()[0]["code"], "svod_prop")

    def test_act_values_carry_scaled_svod_for_excel(self):
        over = self.b.act_values({7: 1.0, 12: 1.5})
        self.assertAlmostEqual(over[(7, 14)], S1_LABOR / 2, places=4)


class SvodHeadingInsideSectionTests(SimpleTestCase):
    """«РАЗДЕЛ …» sarlavhasi (jamisiz) bo'lim ichida: bo'lim chegarasi emas; «ВСЕГО ПО ОБЪЕКТУ» — hamma ishlar
    (avval sarlavha chegara deb olinib, umumiy jami birinchi ishlarni tashlab yuborardi — №1 jami 0 chiqardi)."""

    def test_heading_does_not_split_section_and_total_covers_all(self):
        d = Path(tempfile.mkdtemp(prefix="narxlash_tn_fiz_"))
        try:
            path, grand, _last = build_fiz(d / "fiz.xlsx", heading=True)
            g = read(path, SHEET)
            b = Book(g.layout.as_dict(), g.rows)
            self.assertTrue(b.self_check()["ok"], b.self_check()["fails"])
            gs = [x for x in b.svod_groups() if not x.get("copy")]
            self.assertEqual(len(gs), 3)
            self.assertEqual(gs[0]["tops"], [7, 13])
            self.assertEqual(gs[-1]["tops"], sorted(b.tops))
            ev = b.evaluate({7: 2.0})  # faqat birinchi ish — №1 jami 0 bo'lmasin
            share = (S1_LABOR + 10000 + 4800) / (S1_LABOR + S1_MAT + S2_LABOR)
            self.assertAlmostEqual(b.grand(ev), grand * share, places=2)
        finally:
            shutil.rmtree(d, ignore_errors=True)


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class SvodNoFormulaFlowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        Path(TMP_MEDIA).mkdir(parents=True, exist_ok=True)
        self.user = User.objects.create_user("svod@test.uz", "Kuchli-parol-2026", first_name="A", last_name="B")
        self.client.force_login(self.user)
        path, self.grand, _last = build_fiz(f"{TMP_MEDIA}/fiz.xlsx")
        with open(path, "rb") as f, patch("texnazor.tasks.start", lambda pk: None):
            self.client.post(reverse("texnazor:create"), {"ofyorta": SimpleUploadedFile("ФИЗ ОБЪЁМ.xlsx", f.read())})
        self.obj = SiteObject.objects.get()
        tasks.run_now(self.obj.pk)
        self.obj.refresh_from_db()
        with patch("texnazor.tasks.start", lambda pk: None):
            self.client.post(reverse("texnazor:choose_sheet", args=[self.obj.pk]), {"sheet": SHEET})
        tasks.run_now(self.obj.pk)
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.status, SiteObject.Status.READY, self.obj.error)

    def test_read_without_errors_and_mark_page_goes_on(self):
        self.assertTrue(self.obj.layout["check"]["ok"], self.obj.layout["check"])
        self.assertFalse([w for w in self.obj.warnings if w.get("level") == "error"])
        self.assertIn("svod_prop", [w.get("code") for w in self.obj.warnings])
        self.assertNotIn("svod_nofml", [w.get("code") for w in self.obj.warnings])
        self.assertAlmostEqual(sa.totals(self.obj)["contract"], self.grand, places=2)
        r = self.client.get(reverse("texnazor:mark", args=[self.obj.pk]) + "?auto=1")
        self.assertContains(r, "текширувдан ўтди")

    def test_empty_act_has_no_money(self):
        act = sa.add_act(self.obj)
        b = sa.book(self.obj)
        self.assertAlmostEqual(b.grand(b.evaluate(sa.qty_of(act))), 0.0, places=6)
        sa.set_qty(act, 21, 1.0)  # 2-bo'lim to'liq
        b = sa.book(self.obj)
        share = S2_LABOR / (S1_LABOR + S1_MAT + S2_LABOR)
        self.assertAlmostEqual(b.grand(b.evaluate(sa.qty_of(act))), self.grand * share, places=2)

    def test_report_block_has_act_money(self):
        import io

        from openpyxl import load_workbook

        from . import hisobot
        act = sa.add_act(self.obj)
        sa.set_qty(act, 21, 0.5)  # 2-bo'lim yarmi
        share = S2_LABOR / 2 / (S1_LABOR + S1_MAT + S2_LABOR)
        ws = load_workbook(io.BytesIO(hisobot.build_report(self.obj)), data_only=True).worksheets[0]
        last = [c.value for c in ws[ws.max_row] if isinstance(c.value, (int, float))]
        self.assertTrue(any(abs(v - self.grand * share) < 0.01 for v in last), last)  # №1 jami
        self.assertTrue(any(abs(v - self.grand * (1 - share)) < 0.01 for v in last), last)  # Остатка
        res = [c.value for c in ws[22] if isinstance(c.value, (int, float))]
        self.assertIn(S2_LABOR / 2, res)  # resurs summasi — dalolatnoma hajmi bo'yicha (shartnomaniki emas)

    def test_continue_anyway_is_remembered_for_same_fails(self):
        fails = [{"code": "empty", "row": 30, "title": "Ҳажм ёзилмаса ҳам 1 сўм чиқяпти", "text": "…"}]
        self.obj.layout = dict(self.obj.layout, check={"ok": False, "fails": fails})
        self.obj.save(update_fields=["layout"])
        url = reverse("texnazor:mark", args=[self.obj.pk]) + "?auto=1"
        self.assertContains(self.client.get(url), "Шундай давом этиш")
        r = self.client.post(reverse("texnazor:mark_accept", args=[self.obj.pk]))
        self.assertRedirects(r, reverse("texnazor:object", args=[self.obj.pk]), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(url), reverse("texnazor:object", args=[self.obj.pk]), fetch_redirect_response=False)
        # qayta o'qilganda ham eslab qolinadi (xatolar o'sha bo'lsa)
        self.obj.refresh_from_db()
        with patch.object(Book, "self_check", lambda b: {"ok": False, "full": 1, "empty": 1, "half": 1, "file": 1, "fails": fails}):
            sa.import_raw(self.obj, sa._xlsx(self.obj))
        self.obj.refresh_from_db()
        self.assertIn("accepted", self.obj.layout)
        self.assertRedirects(self.client.get(url), reverse("texnazor:object", args=[self.obj.pk]), fetch_redirect_response=False)
        # boshqa xato — yana to'xtaydi
        self.obj.layout = dict(self.obj.layout, check={"ok": False, "fails": [dict(fails[0], code="half", row=0)]})
        self.obj.save(update_fields=["layout"])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_xls_is_converted_once(self):
        """.xls: belgilash sahifasi / hisobot har so'rovda LibreOffice'ni qayta ishga tushirmaydi."""
        src = self.obj.abs("src/ofyorta.xls")
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"xls")
        conv = self.obj.abs("conv/ofyorta.xlsx")
        conv.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.obj.abs(self.obj.source_path), conv)
        self.obj.source_path = "src/ofyorta.xls"
        with patch("texnazor.services_acts.convert.ensure_xlsx", side_effect=AssertionError("qayta o'girildi")):
            self.assertEqual(sa._xlsx(self.obj), conv)
        import os
        os.utime(src, (conv.stat().st_mtime + 10,) * 2)  # manba yangilangan — qayta o'giriladi
        with patch("texnazor.services_acts.convert.ensure_xlsx", return_value=conv) as m:
            sa._xlsx(self.obj)
        m.assert_called_once()

"""Texnazor: ofyortani o'qish, svodka zanjiri, oyma-oy hajmlar, qoldiq, ruxsatlar."""
import json
from pathlib import Path
import shutil
import tempfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from accounts.models import User

from . import services, tasks
from .calc import chain
from .models import Correction, Done, Item, Period, SiteObject
from .ofyorta import parse

TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_tn_")


def build_ofyorta(path, broken_zero_work=False):
    """Haqiqiy ofyorta tuzilishi: sarlavha, bo'limlar, ish + resurslar, mustaqil resurs, buyurtmachi uskunasi, svodka."""
    wb = Workbook()
    ws = wb.active
    ws.title = "ОФЁРТА"
    ws["C2"], ws["C3"] = "CHILONZOR TUMAN 5-MAKTAB TA'MIRI", "obyekt nomi"
    ws["C4"], ws["C5"] = "MUKAMMAL TA'MIRLASH", "obyekt turi"
    head = ["№ п/п", "Шифр номера нормативов", "Наименование работ и затрат", "Единица измерения", "Количество"]
    for j, v in enumerate(head, start=1):
        ws.cell(row=8, column=j, value=v)
    for j, v in enumerate(["на единицу измерения", "по проектным данным", "Цена подрятчик", "Цена заказчик", "Сумма",
                           "ЧЕЛ-Ч.", "МАШ-Ч.", "МАТРИАЛ", "ЖИХОЗ ПОДРЯТЧИК", "ЖИХОЗ ЗАКАЗЧИК", "КАБЕЛЬ"], start=5):
        ws.cell(row=9, column=j, value=v)
    for j in range(1, 16):
        ws.cell(row=10, column=j, value=j)
    r = 11

    def put(*vals):
        nonlocal r
        for j, v in enumerate(vals, start=1):
            if v is not None:
                ws.cell(row=r, column=j, value=v)
        r += 1
        return r - 1

    put(None, None, "на АС Блок-1")
    put(None, None, "ФУНДАМЕНТ")
    w = put(1, "Е06-01-001-1", "БЕТОН ФУНДАМЕНТОВ", "1 М3", 10)
    # resurs: me'yor (E), F = E × ish hajmi, I = G × F, summa — J/K/L ustunlardan biriga
    for num, code, name, unit, norm, price, col in (("1.1", "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ", "ЧЕЛ-Ч.", 5, 1000, 10),
                                                   ("1.2", "715", "КРАН", "МАШ-Ч.", 0.5, 20000, 11),
                                                   ("1.3", "22006", "БЕТОН", "М3", 1.02, 500000, 12)):
        row = put(num, code, name, unit, norm, norm * 10, price, None, price * norm * 10)
        ws.cell(row=row, column=col, value=price * norm * 10)
    put(2, "30032", "ШВЕЛЛЕР №16", "ТН", 2, None, 9600000, None, 19200000, None, None, 19200000)
    put(None, None, "УСКУНАЛАР")
    put(3, None, "ТЕПЛОВОЙ НАСОС", "КОМПЛЕКТ", 4, None, None, 1000000, 4000000, None, None, None, None, 4000000)
    if broken_zero_work:
        z = put(4, "Е10-01-082-2", "УКЛАДКА ПРОГОНОВ", "1 М3", 0)
        put("4.1", "1", "ЗАТРАТЫ ТРУДА", "ЧЕЛ-Ч.", 15, 0, 1000, None, 15000, 15000)  # I = G × E (xato)
    # svodka (A da ham raqam bor)
    rows = [(None, "JAMI:"), (1, "QURUVCHI-ISHCHILAR ISH HAQQI"), (2, "QURILISH MASHINALARI VA MEXANIZMLARI"),
            (3, "QURILISH MATERIALLARI UCHUN JAMI: /PUDRATCHI/"), (4, "TRANSPORT XARAJATLARI 3%"),
            (5, "KABEL MAHSULOTLARI  /PUDRATCHI/"), (6, "TRANSPORT XARAJATLARI SOTUVCHIDAN 1,5%"),
            (7, "JAMI TO'G'RIDAN TO'G'RI XARAJATLAR"), (8, "PUDRATCHI BOSHQA XARAJATLARI 17%"),
            (10, "USKUNALAR  /BUYURTMACHI/"), (11, "TRANSPORT XARAJATI 2%"), (13, "USKUNALAR  /PUDRATCHI/"),
            (14, "TRANSPORT XARAJATLARI 2%"), (17, "OBYEKTNI SUG'URTALASH 0,32%")]
    for a, label in rows:
        put(a, None, label)
    row = put(18, None, "ХУДОЖЕСТВЕННЫЙ ЭЛЕМЕНТ", "М2", 10)
    ws.cell(row=row, column=9, value=500000)
    put(19, None, "JAMI OB'EKT QIYMATI QQS SIZ")
    put(20, None, "QQS 12%")
    wb.save(path)
    return path


# ======== sof hisob (Django'siz) ========
def test_parse_structure_and_unit_costs(tmp_path):
    of = parse(build_ofyorta(tmp_path / "o.xlsx"))
    kinds = [r.kind for r in of.rows]
    assert kinds.count("section") == 3 and kinds.count("work") == 1 and kinds.count("res") == 3
    assert kinds.count("free") == 2 and kinds.count("extra") == 1
    work = next(r for r in of.rows if r.kind == "work")
    # 1 m3 betonning narxi: mehnat 5×1000, kran 0,5×20000, beton 1,02×500000
    assert work.unit_cost == pytest.approx({"J": 5000, "K": 10000, "L": 510000})
    pump = next(r for r in of.rows if r.name == "ТЕПЛОВОЙ НАСОС")
    assert (pump.category, pump.price, pump.amount) == ("N", 1000000, 4000000)  # narx H da
    assert of.title == "CHILONZOR TUMAN 5-MAKTAB TA'MIRI"
    assert of.rates["tr_material"] == 0.03 and of.rates["overhead"] == 0.17 and of.rates["vat"] == 0.12
    extra = next(r for r in of.rows if r.kind == "extra")
    assert (extra.qty, extra.unit, extra.amount) == (10, "М2", 500000)
    assert not of.warnings


def test_zero_volume_work_with_wrong_formula_is_reported(tmp_path):
    of = parse(build_ofyorta(tmp_path / "o.xlsx", broken_zero_work=True))
    w = of.warnings[0]
    assert len(of.warnings) == 1 and "ҳажми 0" in w["text"] and "15 000" in w["title"] and w["row"]


def test_chain_matches_svodka_formula():
    rates = {"tr_material": 0.03, "tr_cable": 0.015, "overhead": 0.17, "tr_eq_customer": 0.02,
             "tr_eq_contractor": 0.02, "insurance": 0.0032, "vat": 0.12}
    c = chain({"J": 100, "K": 50, "L": 1000, "O": 200, "N": 400, "M": 300, "X": 10}, rates)
    direct = 100 + 50 + 1000 * 1.03 + 200 * 1.015
    base = direct * 1.17 + 400 * 1.02 + 300 * 1.02
    total = (base * 1.0032 + 10) * 1.12
    assert c["direct"] == pytest.approx(direct) and c["total"] == pytest.approx(total)
    assert c["customer"] == pytest.approx(400 * 1.02 * 1.0032 * 1.12)
    assert c["contractor"] == pytest.approx(total - c["customer"])


# ======== bazaga yozish, oylar, sahifalar ========
@override_settings(MEDIA_ROOT=TMP_MEDIA)
class TexnazorFlowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user("tn@test.uz", "Kuchli-parol-2026", first_name="T", last_name="N")
        self.client.force_login(self.user)
        path = build_ofyorta(f"{TMP_MEDIA}/src.xlsx")
        with open(path, "rb") as f:
            data = f.read()
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(reverse("texnazor:create"), {"ofyorta": SimpleUploadedFile("5-maktab.xlsx", data),
                                                              "address": "Чилонзор"})
        self.obj = SiteObject.objects.get()
        self.assertRedirects(r, reverse("texnazor:object", args=[self.obj.pk]), fetch_redirect_response=False)
        tasks.run_now(self.obj.pk)  # testda — darhol (thread o'rniga)
        self.obj.refresh_from_db()
        self.work = Item.objects.get(kind="work")
        self.free = Item.objects.get(name="ШВЕЛЛЕР №16")

    def _post(self, name, body, **kw):
        return self.client.post(reverse(f"texnazor:{name}", kwargs={"obj_id": self.obj.pk, **kw}), json.dumps(body),
                                content_type="application/json")

    def test_import(self):
        self.assertEqual(self.obj.status, "ready")
        self.assertEqual(self.obj.name, "CHILONZOR TUMAN 5-MAKTAB TA'MIRI")  # nomi berilmadi — ofyortadan
        self.assertEqual(self.work.resources.count(), 3)
        self.assertAlmostEqual(self.work.cost_l, 510000)
        self.assertGreater(self.obj.contract_totals["contractor"], 0)
        page = self.client.get(reverse("texnazor:object", args=[self.obj.pk]))
        self.assertContains(page, "Биринчи ҳисобот ойини қўшинг")

    def test_months_carry_over_and_remaining(self):
        self.client.post(reverse("texnazor:period_add", args=[self.obj.pk]), {"year": 2026, "month": 7})
        july = Period.objects.get()
        self.assertTrue(self._post("done", {"period": july.pk, "item": self.work.pk, "value": "4"}).json()["ok"])
        self.assertTrue(self._post("done", {"period": july.pk, "item": self.free.pk, "value": "50%"}).json()["ok"])
        self.assertEqual(Done.objects.get(item=self.free).qty, 1.0)  # 2 ТН × 50%
        self.client.post(reverse("texnazor:period_add", args=[self.obj.pk]), {"year": 2026, "month": 9})
        sept = Period.objects.get(month=9)
        # sentyabrda iyuldagi 4 m3 «avval» bo'lib turadi; qoldiq 6 — 7 yozib bo'lmaydi
        r = self._post("done", {"period": sept.pk, "item": self.work.pk, "value": "7"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Қолдиқ 6 ", r.json()["error"])
        d = self._post("done", {"period": sept.pk, "item": self.work.pk, "value": "6"}).json()
        self.assertTrue(d["ok"])
        t = services.period_totals(sept)
        per_m3 = 5000 + 10000 + 510000
        self.assertAlmostEqual(t["this"]["direct"], 6 * (5000 + 10000 + 510000 * 1.03))
        self.assertAlmostEqual(t["before"]["L"], 4 * 510000 + 1 * 9600000)
        self.assertAlmostEqual(t["cum"]["J"], 10 * 5000)
        self.assertAlmostEqual(t["left"]["J"], 0)
        self.assertGreater(per_m3, 0)
        self.assertAlmostEqual(d["summary"]["cum"] + d["summary"]["left"], d["summary"]["contract"], places=2)
        rows = services.item_rows(sept, [self.work])
        self.assertEqual((rows[0]["before"], rows[0]["this"], rows[0]["left"]), (4.0, 6.0, 0.0))
        # resurslar ishga ergashadi (me'yor × hajm), bo'lim qatori ham yangilanadi
        for x in rows[0]["res"]:
            self.assertAlmostEqual(x["this"], x["r"].qty * 6)
        self.assertAlmostEqual(sum(x["this_amount"] for x in rows[0]["res"]), rows[0]["this_amount"])
        self.assertAlmostEqual(d["section"]["this"], 6 * per_m3)

    def test_closed_month_and_section_fill(self):
        self.client.post(reverse("texnazor:period_add", args=[self.obj.pk]), {"year": 2026, "month": 7})
        july = Period.objects.get()
        sec = self.work.section
        self.assertTrue(self._post("fill", {"period": july.pk, "pct": 100}, section_id=sec.pk).json()["ok"])
        self.assertEqual(Done.objects.get(item=self.work).qty, 10)
        self.client.post(reverse("texnazor:period_close", args=[self.obj.pk, july.pk]), {"closed": "1"})
        r = self._post("done", {"period": july.pk, "item": self.work.pk, "value": "1"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("ёпилган", r.json()["error"])
        # o'tmishdagi oy qo'shib bo'lmaydi
        self.client.post(reverse("texnazor:period_add", args=[self.obj.pk]), {"year": 2026, "month": 5})
        self.assertEqual(Period.objects.count(), 1)

    def test_pages_and_partials(self):
        self.client.post(reverse("texnazor:period_add", args=[self.obj.pk]), {"year": 2026, "month": 7})
        july = Period.objects.get()
        self.assertContains(self.client.get(reverse("texnazor:index")), "CHILONZOR")
        page = self.client.get(reverse("texnazor:object", args=[self.obj.pk]))
        self.assertContains(page, "Фундамент")  # mundarija; nomlar oddiy yozuvda (readable)
        self.assertContains(page, "Бетон фундаментов")  # birinchi bo'lim o'ng tomonda darhol ochiq
        self.assertContains(page, "Июль 2026")
        part = self.client.get(reverse("texnazor:section", args=[self.obj.pk, self.work.section_id]), {"p": july.pk})
        self.assertContains(part, "Бетон фундаментов")
        self.assertContains(part, "Кран")  # ishning resurslari shu jadvalda (yopiq), alohida so'rovsiz
        self.assertContains(self.client.get(reverse("texnazor:search", args=[self.obj.pk]), {"p": july.pk, "q": "швеллер"}), "Швеллер")
        res_row = self.work.resources.first().excel_row  # Excel qatori bo'yicha: resurs qatori -> uning ishi
        self.assertContains(self.client.get(reverse("texnazor:search", args=[self.obj.pk]), {"p": july.pk, "q": str(res_row)}),
                            "Бетон фундаментов")

    def test_correction_changes_contract_and_keeps_history(self):
        self.client.post(reverse("texnazor:period_add", args=[self.obj.pk]), {"year": 2026, "month": 7})
        july = Period.objects.get()
        self._post("done", {"period": july.pk, "item": self.work.pk, "value": "4"})
        before = SiteObject.objects.get().contract_totals["direct"]
        url = lambda it: reverse("texnazor:correct", args=[self.obj.pk, it.pk])  # noqa: E731
        post = lambda it, body: self.client.post(url(it), json.dumps(body), content_type="application/json")  # noqa: E731
        r = post(self.work, {"period": july.pk, "value": "12", "basis": ""})
        self.assertIn("Асос", r.json()["error"])  # asos hujjatisiz bo'lmaydi
        r = post(self.work, {"period": july.pk, "value": "3", "basis": "ҚК №2"})
        self.assertIn("Бажарилган 4", r.json()["error"])  # bajarilganidan kam bo'lmaydi
        self.assertTrue(post(self.work, {"period": july.pk, "value": "12", "basis": "ҚК №2, 12.11.2026"}).json()["ok"])
        self.work.refresh_from_db()
        self.assertEqual(self.work.qty, 12)
        self.assertEqual(Done.objects.get(item=self.work).qty, 4)  # bajarilgan o'zgarmaydi
        per_m3 = 5000 + 10000 + 510000 * 1.03
        self.assertAlmostEqual(SiteObject.objects.get().contract_totals["direct"] - before, 2 * per_m3, places=2)
        c = Correction.objects.get()
        self.assertEqual((c.old_qty, c.new_qty, c.basis, c.author), (10, 12, "ҚК №2, 12.11.2026", self.user))
        page = self.client.get(reverse("texnazor:object", args=[self.obj.pk]))
        self.assertContains(page, "Офёрта тузатишлари")
        self.assertContains(page, "ҚК №2, 12.11.2026")
        # yopilgan oydan kuchga kirmaydi
        self.client.post(reverse("texnazor:period_close", args=[self.obj.pk, july.pk]), {"closed": "1"})
        self.assertIn("ёпилган", post(self.work, {"period": july.pk, "value": "13", "basis": "ҚК №3"}).json()["error"])

    def test_paid_difference(self):
        self.client.post(reverse("texnazor:period_add", args=[self.obj.pk]), {"year": 2026, "month": 7})
        july = Period.objects.get()
        self._post("done", {"period": july.pk, "item": self.work.pk, "value": "4"})
        calc_ = services.period_totals(july)["this"]["contractor"]
        self.client.post(reverse("texnazor:paid", args=[self.obj.pk, july.pk]), {"paid": f"{calc_ + 1000.5:,.2f}".replace(",", " ").replace(".", ",")})
        july.refresh_from_db()
        self.assertAlmostEqual(july.paid, calc_ + 1000.5, places=2)
        self.assertAlmostEqual(services.payments(self.obj)["over"], 1000.5, places=2)
        self.assertContains(self.client.get(reverse("texnazor:object", args=[self.obj.pk])), "ортиқча тўланган")

    def test_delete_month_needs_typed_title_and_open_month(self):
        self.client.post(reverse("texnazor:period_add", args=[self.obj.pk]), {"year": 2026, "month": 7})
        july = Period.objects.get()
        self._post("done", {"period": july.pk, "item": self.work.pk, "value": "4"})
        url = reverse("texnazor:period_delete", args=[self.obj.pk, july.pk])
        self.client.post(url, {"confirm": "ҳа"})
        self.assertTrue(Period.objects.exists())  # 2-qadam: oy nomi yozilmasa o'chmaydi
        self.client.post(reverse("texnazor:period_close", args=[self.obj.pk, july.pk]), {"closed": "1"})
        self.client.post(url, {"confirm": "Июль 2026"})
        self.assertTrue(Period.objects.exists())  # yopilgan oy o'chmaydi
        self.client.post(reverse("texnazor:period_close", args=[self.obj.pk, july.pk]), {"closed": "0"})
        self.client.post(url, {"confirm": "июль 2026"})
        self.assertFalse(Period.objects.exists())
        self.assertFalse(Done.objects.exists())

    def test_revert_correction_last_first(self):
        self.client.post(reverse("texnazor:period_add", args=[self.obj.pk]), {"year": 2026, "month": 7})
        july = Period.objects.get()
        c1 = services.correct_item(self.user, self.work, july, "12", "ҚК №1")
        c2 = services.correct_item(self.user, Item.objects.get(pk=self.work.pk), july, "15", "ҚК №2")
        rv = lambda c, w: self.client.post(reverse("texnazor:revert", args=[self.obj.pk, c.pk]), json.dumps({"confirm": w}),  # noqa: E731
                                           content_type="application/json").json()
        self.assertIn("БЕКОР", rv(c2, "ҳа")["error"])
        self.assertIn("кейинги", rv(c1, "бекор")["error"])  # oxirgisi birinchi
        self.assertTrue(rv(c2, "бекор")["ok"])
        self.assertEqual(Item.objects.get(pk=self.work.pk).qty, 12)
        self.assertTrue(rv(c1, "БЕКОР")["ok"])
        self.assertEqual(Item.objects.get(pk=self.work.pk).qty, 10)
        self.assertEqual(Correction.objects.filter(reverted__isnull=False).count(), 2)  # tarixda qoladi
        self.assertAlmostEqual(SiteObject.objects.get().contract_totals["direct"], self.obj.contract_totals["direct"], places=2)

    def test_delete_object_needs_word(self):
        self.client.post(reverse("texnazor:delete", args=[self.obj.pk]), {"confirm": ""})
        self.assertTrue(SiteObject.objects.exists())
        self.client.post(reverse("texnazor:delete", args=[self.obj.pk]), {"confirm": "ўчириш"})
        self.assertFalse(SiteObject.objects.exists())

    def test_other_user_cannot_see_object(self):
        other = User.objects.create_user("b@test.uz", "Kuchli-parol-2026", first_name="B", last_name="B")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("texnazor:object", args=[self.obj.pk])).status_code, 404)
        self.assertEqual(self._post("done", {"item": self.work.pk, "value": "1"}).status_code, 404)
        self.assertNotContains(self.client.get(reverse("texnazor:index")), "CHILONZOR")


def test_money_is_shown_to_tiyin_without_rounding_to_som():
    from texnazor.templatetags.texnazor_tags import pul
    assert pul(2862982.6934) == "2 862 982,69"
    assert pul(1000) == "1 000,00"
    assert pul(-0.001) == "0,00"


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class ZeroVolumeCorrectionTests(TestCase):
    """Ofyortada hajmi 0 bo'lgan ish (resurslarida formula xato): tuzatilsa narxi me'yorlardan tiklanadi."""

    def test_zero_volume_work_gets_unit_cost_from_norms(self):
        user = User.objects.create_user("z@test.uz", "Kuchli-parol-2026", first_name="Z", last_name="Z")
        self.client.force_login(user)
        Path(TMP_MEDIA).mkdir(parents=True, exist_ok=True)
        with open(build_ofyorta(f"{TMP_MEDIA}/z.xlsx", broken_zero_work=True), "rb") as f:
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("texnazor:create"), {"ofyorta": SimpleUploadedFile("z.xlsx", f.read())})
        obj = SiteObject.objects.get()
        tasks.run_now(obj.pk)
        zero = Item.objects.get(name="УКЛАДКА ПРОГОНОВ")
        self.assertEqual((zero.qty, zero.unit_cost), (0, 0))
        self.client.post(reverse("texnazor:period_add", args=[obj.pk]), {"year": 2026, "month": 7})
        page = self.client.get(reverse("texnazor:object", args=[obj.pk]))
        self.assertContains(page, "Жадвалда кўрсатиш")
        norms = sum(r.price * r.qty for r in zero.resources.all())
        before = SiteObject.objects.get().contract_totals["direct"]
        r = self.client.post(reverse("texnazor:correct", args=[obj.pk, zero.pk]),
                             json.dumps({"value": "2", "basis": "Смета тузатмаси №1"}), content_type="application/json")
        self.assertTrue(r.json()["ok"])
        zero.refresh_from_db()
        self.assertAlmostEqual(zero.unit_cost, norms)
        self.assertGreater(SiteObject.objects.get().contract_totals["direct"], before)
        self.assertTrue(all(abs(x.total_qty - x.qty * 2) < 1e-9 for x in zero.resources.all()))
        self.assertContains(self.client.get(reverse("texnazor:object", args=[obj.pk])), "Тузатилди: 0 → 2")


def _mini_ofyorta(path, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "ОФЁРТА"
    ws.append(["№ п/п", "Шифр", "Наименование работ", "Единица", "Количество"])
    ws.append([None, None, None, None, "на единицу", "по проектным", "Цена подрятчик", "Цена заказчик", "Сумма", "ЧЕЛ-Ч.", "МАШ-Ч.", "МАТРИАЛ"])
    ws.append(list(range(1, 13)))
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


_WORK = [[1, "Е06-01-001-1", "БЕТОН", "1 М3", 10], ["1.1", "1", "ЗАТРАТЫ ТРУДА", "ЧЕЛ-Ч.", 5, 50, 1000, None, 50000, 50000]]
_FREE = [[2, "30032", "ШВЕЛЛЕР", "ТН", 2, None, 9600000, None, 19200000, None, None, 19200000]]


def test_items_without_any_header_are_not_lost(tmp_path):
    of = parse(_mini_ofyorta(tmp_path / "a.xlsx", _WORK + _FREE))
    assert of.sections == ["Бўлим номи кўрсатилмаган"]
    assert all(r.section == 0 for r in of.rows)
    assert any("сарлавҳаси йўқ" in w["title"] for w in of.warnings)


def test_numbered_header_without_unit_or_price_is_a_section(tmp_path):
    rows = [[1, None, "ДЕМОНТАЖ"], [2, "Е06-01-001-1", "БЕТОН", "1 М3", 10],
            ["2.1", "1", "ЗАТРАТЫ", "ЧЕЛ-Ч.", 5, 50, 1000, None, 50000, 50000]]
    of = parse(_mini_ofyorta(tmp_path / "b.xlsx", rows))
    assert of.sections == ["ДЕМОНТАЖ"]
    assert [r.kind for r in of.rows] == ["section", "work", "res"]


def test_block_titles_with_or_without_na():
    from .services import _BLOCK, block_label
    for t in ("на АС Блок-1", "НА ЭО", "АС БЛОК-2", "БЛОК №3"):
        assert _BLOCK.search(t), t
    for t in ("ДВЕРНЫЕ БЛОКИ (АС ЛИСТ-9)", "ДЕМОНТАЖ 1-ЭТАЖ", "НАРУЖНОЙ ОТДЕЛКИ"):
        assert not _BLOCK.search(t), t
    assert block_label("НА АС БЛОК-1") == "АС БЛОК-1"

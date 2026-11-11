"""Объект таҳлили: смета нархларини ўқиш, ҳисоб (фойда, хулоса, омиллар) ва саҳифалар / панел."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from accounts.models import User
from core.columns import detect_local_columns
from core.tender import tender_prices

from . import tender
from .models import Job, TenderFactor, TenderSettings


def _row(r, name, unit, qty, t_price, g, cat="K", status="EXACT"):
    return {"row": r, "name": name, "unit": unit, "qty": qty, "code": None, "qty_unit": None, "qty_total": qty,
            "g": g, "h": g * qty if g is not None else None, "category": cat, "status": status,
            "t_price": t_price, "t_amount": t_price * qty if t_price is not None else None}


ZERO_PCT = {"transport_mat": 0, "transport_kabel": 0, "transport_obor": 0, "prochie": 0, "strahovanie": 0, "s_nds": 0.12}


class TenderPricesTests(TestCase):
    def test_reads_price_and_amount_columns_by_header(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["№", "Шифр", "Наименование", "Ед. изм.", "Количество на ед.", "Количество всего", "Цена, сум", "Сумма, сум"])
        ws.append([1, 2, 3, 4, 5, 6, 7, 8])
        ws.append([1, "258", "ЦЕМЕНТ М400", "т", 1, 2, 1000, 2000])
        ws.append([2, "300", "ПЕСОК", "м3", 1, 4, 50, 200])
        ws.append([3, "301", "ЩЕБЕНЬ", "м3", 1, 3, None, None])
        cols = detect_local_columns(ws)
        self.assertIsNotNone(cols)
        prices = tender_prices(ws, cols)
        self.assertEqual(prices[3], (1000.0, 2000.0))
        self.assertEqual(prices[4], (50.0, 200.0))
        self.assertNotIn(5, prices)  # нархсиз қатор олинмайди


class AnalyseTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("q@test.uz", "Kuchli-parol-2026")
        rows = [
            _row(10, "ИШ ҲАҚИ", "чел.-ч", 100, 12000, 10000, cat="I"),   # тендерда 20% қиммат — фойда
            _row(11, "АРМАТУРА", "т", 10, 6000000, 8000000),             # 25% арзон — зарар
            _row(12, "ЦЕМЕНТ", "т", 20, 600000, 600000),
            _row(13, "ГВОЗДИ", "кг", 5, 10000, None, status="NOT_FOUND"),  # бизда нархсиз
            _row(14, "ПАРОВОЗ", "шт", 1, None, 50000),                    # тендерда нархсиз
        ]
        self.job = Job.objects.create(owner=self.user, status=Job.Status.DONE, local_name="t.xlsx", res_name="r.xlsx",
                                      local_path="", res_path="r.xlsx",
                                      result={"rows": rows, "percents": ZERO_PCT})

    def test_numbers_and_lists(self):
        r = tender.analyse([self.job])
        self.assertEqual(r["status"], "ok")
        tender_sum = 1200000 + 60000000 + 12000000 + 50000
        self.assertAlmostEqual(r["tender_bez"], tender_sum)
        cost = 1000000 + 80000000 + 12000000 + 50000 + 50000  # нархсиз — тендердагидек, тендерда йўғи — бозорда
        self.assertAlmostEqual(r["cost"], cost)
        self.assertEqual(r["verdict"]["key"], "no")
        self.assertEqual([g["name"] for g in r["loss_rows"]], ["АРМАТУРА"])
        self.assertEqual([g["name"] for g in r["gain_rows"]], ["ИШ ҲАҚИ"])
        self.assertEqual(r["missing"]["n"], 1)
        self.assertEqual(r["unpriced"]["n"], 1)
        self.assertTrue(any("паст ёзилган" in b["title"] for b in r["bad"]))
        self.assertLess(r["reserve"], 0)  # зарар — захира йўқ

    def test_coefficients_and_hidden_factor_change_profit(self):
        TenderSettings.objects.create(pk=1, enabled=True, params={"cost_K": 70})
        TenderFactor.objects.create(title="махфий", scope="revenue", effect=5, visible=False)
        tender.forget_config()
        r = tender.analyse([self.job])
        cost = 1000000 + (80000000 + 12000000 + 50000 + 50000) * 0.7
        self.assertAlmostEqual(r["cost"], cost)
        self.assertAlmostEqual(r["revenue"], (1200000 + 60000000 + 12000000 + 50000) * 1.05)
        self.assertEqual(r["verdict"]["key"], "go")
        self.assertTrue(r["factors_hidden"])
        self.assertEqual(r["factors_visible"], [])

    def test_cost_rise_lowers_margin(self):
        a = tender.analyse([self.job])
        b = tender.analyse([self.job], rise=10)
        self.assertLess(b["margin"], a["margin"])
        self.assertAlmostEqual(b["cost"], a["cost"] * 1.1)

    def test_suspicious_prices_are_left_out(self):
        rows = self.job.result["rows"]
        rows.append(_row(15, "ТРУБА", "м", 10, 1, 90000))            # тендерда 1 сўм
        rows.append(_row(16, "ПРОВОЛОКА", "т", 2, 9000, 9000000))    # кг/т хатоси — 1000 баравар
        self.job.save()
        r = tender.analyse([self.job])
        self.assertEqual(r["n_susp"], 2)
        self.assertEqual({g["name"] for g in r["suspicious"]}, {"ТРУБА", "ПРОВОЛОКА"})
        self.assertNotIn("ТРУБА", [g["name"] for g in r["loss_rows"]])
        self.assertTrue(any("шубҳали" in x["title"] for x in r["risks"]))

    def test_reserve(self):
        TenderSettings.objects.create(pk=1, params={"cost_K": 70})
        tender.forget_config()
        r = tender.analyse([self.job])
        self.assertAlmostEqual(r["reserve"], (r["revenue"] / r["cost"] - 1) * 100)
        self.assertLess(r["reserve_min"], r["reserve"])
        self.assertLess(tender.analyse([self.job], rise=r["reserve"] + 0.1)["profit"], 0)

    def test_no_tender_prices(self):
        for r in self.job.result["rows"]:
            r["t_price"] = r["t_amount"] = None
        self.job.res_path = ""
        self.job.save()
        self.assertEqual(tender.analyse([self.job])["status"], "no_prices")


class TenderPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("u@test.uz", "Kuchli-parol-2026")
        self.job = Job.objects.create(owner=self.user, status=Job.Status.DONE, local_name="t.xlsx", res_name="r.xlsx",
                                      local_path="", res_path="r.xlsx",
                                      result={"rows": [_row(10, "АРМАТУРА", "т", 10, 6000000, 8000000)], "percents": ZERO_PCT})
        self.url = reverse("job_tender", args=[self.job.id])
        self.client.force_login(self.user)

    def test_hidden_when_disabled(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_visible_when_enabled(self):
        TenderSettings.objects.create(pk=1, enabled=True)
        tender.forget_config()
        page = self.client.get(self.url)
        self.assertContains(page, "Тавсия этилмайди")
        self.assertContains(page, "АРМАТУРА")
        self.assertContains(page, "Қурилиш давомида нархлар ошса")
        self.assertNotContains(page, "таклиф қиласиз")

    def test_other_users_job_is_hidden(self):
        TenderSettings.objects.create(pk=1, enabled=True)
        tender.forget_config()
        other = User.objects.create_user("x@test.uz", "Kuchli-parol-2026")
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)


class PanelTenderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user("a@test.uz", "Kuchli-parol-2026", is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)
        s = self.client.session
        s["_2fa_ok"] = self.admin.pk
        s.save()

    def test_save_settings_and_factor(self):
        self.assertEqual(self.client.get(reverse("panel:tender")).status_code, 200)
        data = {k: d for k, _l, _h, _u, d, *_ in tender.PARAMS}
        data.update(enabled="1", cost_K="92,5", norm_in_cost="")
        self.client.post(reverse("panel:tender_save"), data)
        s = TenderSettings.objects.get(pk=1)
        self.assertTrue(s.enabled)
        self.assertEqual(s.params["cost_K"], 92.5)
        self.assertTrue(tender.enabled())
        self.client.post(reverse("panel:tender_factor_add"), {"title": "Улгуржи чегирма", "scope": "K", "effect": "-8",
                                                              "active": "1", "note": "сир"})
        f = TenderFactor.objects.get()
        self.assertEqual((f.scope, f.effect, f.visible), ("K", -8.0, False))
        self.assertEqual(tender.config()["factors"][0]["effect"], -8.0)

    def test_bad_value_rejected(self):
        data = {k: d for k, _l, _h, _u, d, *_ in tender.PARAMS}
        data.update(min_margin="20", good_margin="10")
        self.client.post(reverse("panel:tender_save"), data)
        self.assertFalse(TenderSettings.objects.exists())

    def test_staff_only(self):
        user = User.objects.create_user("s@test.uz", "Kuchli-parol-2026")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("panel:tender")).status_code, 404)

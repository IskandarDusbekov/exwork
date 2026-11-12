"""Янги нарх базаси: паст / ўрта / юқори, тасдиқлаб йиғиш, қўлда киритиш, паспорт ва панел саҳифалари."""
import json
from datetime import date
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from jobs.models import Job

from . import services
from .models import Batch, Item, Price, Stat

PASSWORD = "Kuchli-parol-2026"


def row(n, name, unit, price, status="EXACT", cat="K", **kw):
    return {"row": n, "code": "", "name": name, "unit": unit, "g": price, "status": status, "category": cat, **kw}


def make_job(owner, rows, name="lokal.xlsx"):
    return Job.objects.create(owner=owner, local_name=name, res_name="res.xlsx", local_path="x", res_path="y",
                              status=Job.Status.DONE, result={"rows": rows})


class ThreeTests(TestCase):
    def test_same_price_is_one(self):
        self.assertEqual(services.three([D(5)] * 5)[:3], (D(5), D(5), D(5)))

    def test_small_sets_min_median_max(self):
        self.assertEqual(services.three([D(120), D(100)])[:3], (D(100), D(100), D(120)))  # тенг — арзони
        self.assertEqual(services.three([D(120), D(100)], [5, 2])[:3], (D(100), D(120), D(120)))  # кўп учрагани
        self.assertEqual(services.three([D(130), D(100), D(110)])[:3], (D(100), D(110), D(130)))

    def test_quartiles_and_outlier(self):
        low, mid, high, out = services.three([D(100), D(110), D(120), D(130), D(1000)])
        self.assertEqual(out, {D(1000)})
        self.assertEqual((low, mid, high), (D(100), D(110), D(120)))

    def test_always_real_prices_by_rank(self):
        ps = [D(100 + i) for i in range(25)]
        self.assertEqual(services.three(ps)[:3], (D(106), D(112), D(118)))  # 7-, 13-, 19-нарх

    def test_new_low_price_shifts_down(self):
        a = services.three([D(100), D(110), D(120), D(130)])
        b = services.three([D(90), D(95), D(100), D(110), D(120), D(130)])
        self.assertLess(b[1], a[1])
        self.assertLess(b[0], a[0])


class CollectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ali@test.uz", PASSWORD, first_name="A", last_name="V")

    def test_rows_grouped_and_sure(self):
        job = make_job(self.user, [
            row(10, "РАЗРАБОТКА ГРУНТА", "100М3", 0, status="WORK_HEADER"),
            row(11, "ЗАТРАТЫ ТРУДА", "ЧЕЛ.-Ч", 53339, cat="I", qty_total=2),
            row(12, "затраты  труда", "чел-час", 53339, cat="I", qty_total=3),
            row(13, "ВОДА", "М3", 760, status="FUZZY"),
            row(14, "МАШИНИСТ", "ЧЕЛ.-Ч", 0, cat="I"),
            row(15, "НОУТБУК", "шт", 10_000_000, cat="M", manual=True, status="FUZZY"),
        ])
        rows = services.rows_from_jobs([job])
        self.assertEqual([r["name"] for r in rows], ["ЗАТРАТЫ ТРУДА", "ВОДА", "НОУТБУК"])
        self.assertEqual(rows[0]["qty"], D(5))
        self.assertEqual(rows[0]["n"], 2)
        self.assertEqual([r["sure"] for r in rows], [True, False, True])

    def test_add_batch_creates_passport_and_stats(self):
        job = make_job(self.user, [row(11, "ВОДА", "М3", 760), row(12, "ЦЕМЕНТ", "Т", 1_200_000)], name="f1.xlsx")
        b = services.add_batch("smeta", job, services.rows_from_jobs([job]), region="buxoro", day=date(2026, 5, 3),
                               user=self.user)
        self.assertEqual((b.prices.count(), b.stats["new"]), (2, 2))
        p = Price.objects.get(item__name="ВОДА")
        self.assertEqual((p.year, p.places[0]["file"], p.places[0]["row"]), (2026, "f1.xlsx", 11))
        st = Stat.objects.get(item=p.item, region="buxoro", year=2026)
        self.assertTrue(st.single)
        job2 = make_job(self.user, [row(5, "вода", "м3", 900)])  # бошқа смета, бошқа нарх
        b2 = services.add_batch("smeta", job2, services.rows_from_jobs([job2]), region="buxoro", day=date(2026, 6, 1))
        st.refresh_from_db()
        self.assertEqual((st.low, st.mid, st.high, st.n), (D(760), D(760), D(900), 2))
        job3 = make_job(self.user, [row(5, "ВОДА", "М3", 900), row(9, "ВОДА", "М3", 900)])  # 900 — 2 марта
        services.add_batch("smeta", job3, services.rows_from_jobs([job3]), region="buxoro", day=date(2026, 7, 1))
        st.refresh_from_db()
        self.assertEqual((st.low, st.mid, st.high), (D(760), D(900), D(900)))
        self.assertEqual((Item.objects.count(), b2.stats["new"]), (2, 0))

    def test_manual_exclude_delete(self):
        p = services.add_manual(name="Ноутбук", unit="шт", category="M", price=D(10_000_000), qty=D(2),
                                region="toshkent_sh", day=date(2026, 11, 1), user=self.user)
        self.assertEqual(p.batch.kind, Batch.Kind.MANUAL)
        p2 = services.add_manual(name="НОУТБУК", unit="ШТ", category="M", price=D(12_000_000), region="toshkent_sh",
                                 day=date(2026, 11, 2))
        self.assertEqual(p.item_id, p2.item_id)
        services.set_excluded(p2, True)
        st = Stat.objects.get(item=p.item)
        self.assertEqual((st.mid, st.n), (D(10_000_000), 1))
        services.delete_batch(p.batch)
        self.assertFalse(Stat.objects.filter(item=p.item).exists())  # қолгани истисно — ҳисобга кирадиган нарх йўқ


class PanelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.smetachi = User.objects.create_user("s@test.uz", PASSWORD, first_name="S", last_name="V")
        self.job = make_job(self.smetachi, [row(11, "ВОДА", "М3", 760), row(12, "ГРАВИЙ", "М3", 35000, status="FUZZY")])
        self.url = reverse("panel:narx_review", args=["smeta", self.job.pk])
        self.client.force_login(self.admin)

    def test_pages_and_confirm_flow(self):
        for name in ("panel:narx", "panel:narx_collect", "panel:narx_batches", "panel:narx_how"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        r = self.client.get(self.url)
        self.assertContains(r, "ГРАВИЙ")
        r = self.client.post(self.url, {"sig": r.context["sig"], "region": "buxoro", "date": "2026-05-03",
                                        "pick": json.dumps([0, 1])})
        b = Batch.objects.get()
        self.assertRedirects(r, reverse("panel:narx_batch", args=[b.pk]))
        self.assertEqual(b.prices.count(), 2)
        it = Item.objects.get(name="ВОДА")
        r = self.client.get(reverse("panel:narx_item", args=[it.pk]))
        self.assertContains(r, "lokal.xlsx")
        self.assertContains(r, self.smetachi.get_full_name())
        self.assertEqual(self.client.get(reverse("panel:narx_batch", args=[b.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("panel:narx")), "ВОДА")
        self.assertNotContains(self.client.get(reverse("panel:narx_collect")), self.url)

    def test_no_js_takes_only_sure_rows(self):
        sig = self.client.get(self.url).context["sig"]
        self.client.post(self.url, {"sig": sig, "region": "buxoro", "date": "2026-05-03"})
        self.assertEqual(list(Price.objects.values_list("name", flat=True)), ["ВОДА"])

    def test_future_date_and_stale_sig_rejected(self):
        sig = self.client.get(self.url).context["sig"]
        self.client.post(self.url, {"sig": sig, "region": "buxoro", "date": "2999-01-01"})
        self.client.post(self.url, {"sig": "eski", "region": "buxoro", "date": "2026-05-03"})
        self.assertFalse(Price.objects.exists())

    def test_manual_add_view(self):
        r = self.client.post(reverse("panel:narx_add"), {"name": "Ноутбук", "unit": "шт", "category": "M",
                                                          "price": "10 000 000", "region": "buxoro", "date": "2026-11-01"})
        it = Item.objects.get()
        self.assertRedirects(r, reverse("panel:narx_item", args=[it.pk]))
        self.assertEqual(it.prices.get().price, D(10_000_000))
        self.assertContains(self.client.get(reverse("panel:narx_item", args=[it.pk])), "қўлда киритилган")

    def test_staff_cannot_confirm(self):
        staff = User.objects.create_user("st@test.uz", PASSWORD, first_name="S", last_name="T", is_staff=True)
        self.client.force_login(staff)
        sig = self.client.get(self.url).context["sig"]
        self.assertEqual(self.client.post(self.url, {"sig": sig, "region": "buxoro", "date": "2026-05-03"}).status_code, 404)


class UploadAndListTests(TestCase):
    def setUp(self):
        import tempfile
        self.media = tempfile.mkdtemp()
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.media, ignore_errors=True)

    def _xlsx(self):
        import io
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["Тижорий таклиф"])
        ws.append(["№", "Наименование", "Ед. изм.", "Кол-во", "Цена", "Тоифа"])
        ws.append([1, "Ноутбук HP", "шт", 2, 10_000_000, "ускуна"])
        ws.append([2, "Кабель ВВГ 3х2.5", "м", 100, 12_000, "кабель"])
        ws.append([3, "Итого", None, None, None, None])
        buf = io.BytesIO()
        wb.save(buf)
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile("taklif.xlsx", buf.getvalue())

    def test_excel_upload_preview_and_confirm(self):
        from django.test import override_settings
        with override_settings(MEDIA_ROOT=self.media):
            r = self.client.post(reverse("panel:narx_upload"), {"file": self._xlsx(), "kind": "excel", "category": "K"})
            self.assertEqual(r.status_code, 302, r)
            page = self.client.get(r["Location"])
            self.assertContains(page, "Ноутбук HP")
            self.assertEqual([x["category"] for x in page.context["rows"]], ["M", "L"])
            r = self.client.post(r["Location"], {"region": "buxoro", "date": "2026-11-01", "pick": "[0, 1]"})
            b = Batch.objects.get()
            self.assertEqual((b.kind, b.title, b.prices.count()), ("file", "taklif.xlsx", 2))
            self.assertEqual(Price.objects.get(item__name="Ноутбук HP").qty, D(2))
            self.assertEqual(self.client.get(reverse("panel:narx_batch_file", args=[b.pk])).status_code, 200)
            it = Item.objects.get(name="Ноутбук HP")
            self.assertContains(self.client.get(reverse("panel:narx_item", args=[it.pk])), "асл файл")

    def test_upload_cancel(self):
        from django.test import override_settings
        with override_settings(MEDIA_ROOT=self.media):
            r = self.client.post(reverse("panel:narx_upload"), {"file": self._xlsx(), "kind": "excel"})
            self.client.post(r["Location"], {"action": "cancel"})
            self.assertFalse(Batch.objects.exists())
            self.assertEqual(self.client.get(r["Location"]).status_code, 404)

    def test_list_filters_sorts_and_coverage(self):
        services.add_manual(name="Цемент", unit="т", category="K", price=D(1_200_000), region="buxoro", day=date(2026, 3, 1))
        services.add_manual(name="Цемент", unit="т", category="K", price=D(1_400_000), region="buxoro", day=date(2026, 4, 1))
        services.add_manual(name="Ноутбук", unit="шт", category="M", price=D(9_000_000), region="andijon", day=date(2025, 4, 1))
        u = reverse("panel:narx")
        for q in ("", "?sort=cheap", "?sort=dear", "?sort=most", "?sort=new", "?flag=multi", "?flag=single",
                  "?flag=manual", "?flag=outlier", "?region=buxoro&year=2026", "?cat=M", "?q=цем", "?per=25&page=2"):
            self.assertEqual(self.client.get(u + q).status_code, 200, q)
        r = self.client.get(u + "?flag=multi")
        self.assertEqual([i.name for i in r.context["rows"]], ["Цемент"])
        self.assertContains(r, "юқори")
        r = self.client.get(u + "?sort=dear")
        self.assertEqual([i.name for i in r.context["rows"]], ["Ноутбук", "Цемент"])
        make_job(self.admin, [row(1, "ЦЕМЕНТ", "Т", 5), row(2, "ГВОЗДИ", "КГ", 7)])
        cov = services.coverage()
        self.assertEqual((cov["rows"], cov["covered"], cov["pct"]), (2, 1, 50))


class DeleteItemTests(TestCase):
    def test_delete_item_view(self):
        admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(admin)
        p = services.add_manual(name="Ноутбук", unit="шт", category="M", price=D(10), region="buxoro", day=date(2026, 1, 1))
        r = self.client.post(reverse("panel:narx_item_delete", args=[p.item_id]))
        self.assertRedirects(r, reverse("panel:narx"))
        self.assertFalse(Item.objects.exists() or Price.objects.exists() or Stat.objects.exists() or Batch.objects.exists())


import shutil as _shutil
import tempfile as _tempfile
from pathlib import Path as _Path

from django.test import override_settings as _override

_MEDIA = _Path(_tempfile.mkdtemp(prefix="narxlash_narxbaza_"))


@_override(MEDIA_ROOT=_MEDIA)
class PricingTests(TestCase):
    """Нархлашга улаш: RES'сиз смета номи + бирлиги бўйича, смета­чи танлаган паст / ўрта / юқори нарх."""

    @classmethod
    def tearDownClass(cls):
        _shutil.rmtree(_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        from .models import Config
        self.user = User.objects.create_user("ali@test.uz", PASSWORD, first_name="A", last_name="V")
        for price, day in ((200000, 1), (250000, 2), (300000, 3)):  # кран: паст 200 000, ўрта 250 000, юқори 300 000
            services.add_manual(name="Краны на автомобильном ходу", unit="маш-час", category="J", price=D(price),
                                region="samarqand", day=date(2026, 1, day))
        Config.objects.update_or_create(pk=1, defaults={"enabled": True, "level": "mid"})
        self.client.force_login(self.user)

    def test_price_map_levels(self):
        from resbase.services import period_label, price_map
        key = services.item_key("КРАНЫ НА АВТОМОБИЛЬНОМ ХОДУ", "МАШ.-Ч")
        self.assertEqual(price_map({"region": "samarqand", "year": 2026})[key]["price"], 250000.0)
        self.assertEqual(price_map({"region": "samarqand", "year": 2026, "level": "low"})[key]["price"], 200000.0)
        self.assertEqual(price_map({"region": "samarqand", "year": 2026, "level": "high"})[key]["price"], 300000.0)
        self.assertEqual(price_map({"region": "samarqand", "year": 2025}), {})  # бошқа йил — йўқ
        self.assertIn("юқори", period_label({"region": "samarqand", "year": 2026, "level": "high"}))

    def test_level_saved_from_form(self):
        from jobs.services import base_period_of
        self.client.post(reverse("base_period"), {"region": "samarqand", "year": "2026", "level": "high"})
        self.assertEqual(base_period_of(self.user)["level"], "high")

    def test_local_without_res_priced_by_name(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from core.tests.conftest import build_local
        from jobs.models import Batch as JB
        self.client.post(reverse("base_period"), {"region": "samarqand", "year": "2026", "level": "low"})
        self.client.post(reverse("price_source"), {"source": "base"})
        local = build_local(_MEDIA / "Локал.xlsx")
        self.client.post(reverse("index"), {"files": [SimpleUploadedFile("Локал.xlsx", local.read_bytes())]})
        batch = JB.objects.get()
        self.client.post(reverse("batch_confirm", args=[batch.id]), {"include_0": "1", "res_0": "__base__"})
        job = Job.objects.get(batch=batch)
        rows = {r["name"]: r for r in job.result["rows"] if r.get("name")}
        crane = rows["КРАНЫ НА АВТОМОБИЛЬНОМ ХОДУ"]
        self.assertEqual((crane["status"], crane["g"]), ("BASE", 200000))
        self.assertEqual(rows["ВОДА"]["status"], "NOT_FOUND")
        # смета саҳифасида юқори нарх танланди — қайта нархланади
        self.client.post(reverse("job_base", args=[job.id]), {"region": "samarqand", "year": "2026", "level": "high"})
        job.refresh_from_db()
        crane = {r["name"]: r for r in job.result["rows"] if r.get("name")}["КРАНЫ НА АВТОМОБИЛЬНОМ ХОДУ"]
        self.assertEqual(crane["g"], 300000)

    def test_panel_defaults(self):
        from resbase.models import BaseConfig
        from .models import Config
        admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(admin)
        self.client.post(reverse("panel:narx_config"), {"region": "samarqand", "year": "2026", "level": "low"})
        self.assertEqual(Config.get().level, "low")
        self.assertEqual((BaseConfig.get().region, BaseConfig.get().year), ("samarqand", 2026))
        page = self.client.get(reverse("panel:narx"))
        self.assertContains(page, "нархлашда ишлаяпти")
        self.client.post(reverse("panel:narx_config"), {"enabled": "0"})
        self.assertFalse(Config.get().enabled)
        from resbase.services import price_map
        self.assertEqual(price_map({"region": "samarqand", "year": 2026}), {})
        self.assertContains(self.client.get(reverse("panel:narx")), "нархлаш ўчирилган")
        self.client.post(reverse("panel:narx_config"), {"enabled": "1"})
        self.assertTrue(Config.get().enabled)

    def test_old_toggle_does_nothing(self):
        from resbase.models import BaseConfig
        admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(admin)
        self.client.post(reverse("panel:base_toggle"), {"enabled": "1"})
        self.assertFalse(BaseConfig.get().enabled)

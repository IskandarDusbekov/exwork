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
        self.assertEqual((low, mid, high), (D(100), D(110), D(130)))  # юқори — энг қиммат ҳақиқий нарх

    def test_always_real_prices_by_rank(self):
        ps = [D(100 + i) for i in range(25)]
        self.assertEqual(services.three(ps)[:3], (D(100), D(112), D(124)))  # энг арзони, 13-нарх, энг қиммати

    def test_high_is_real_price_from_many_files(self):
        # 1-ресурс ҳолати: 36 030 атрофида 6 та (яхлитлаш фарқи), 53 339 — 7 файлда, 54 606,75 — 20 файлда
        ps = [D("36030"), D("36030"), D("36030.1"), D("36030.1"), D("36030.1"), D("36030.12"), D("53339"), D("54606.75")]
        seen = [13, 13, 20, 1, 6, 10, 7, 20]
        low, mid, high, out = services.three(ps, seen)
        self.assertEqual((low, mid, high, out), (D("36030.1"), D("36030.1"), D("54606.75"), set()))

    def test_weight_by_files_moves_mid(self):
        # қиммат нарх кўп файлда — ўрта шунга сурилади
        self.assertEqual(services.three([D(100), D(150)], [2, 30])[1], D(150))
        self.assertEqual(services.three([D(100), D(120), D(150)], [1, 1, 30])[1], D(150))

    def test_same_file_counted_once(self):
        f = frozenset({"job1", "job2"})
        # 100 — икки тўпламда, лекин ўша 2 та файл; 120 — 3 та бошқа файлда
        self.assertEqual(services.three([D(100), D(100), D(120)], [f, f, frozenset({"a", "b", "c"})])[1], D(120))

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
        services.trash_batch(p.batch)
        self.assertFalse(Stat.objects.filter(item=p.item).exists())  # қолгани истисно — ҳисобга кирадиган нарх йўқ


class UploadAndListTests(TestCase):
    """Эски тўғридан-тўғри тасдиқлаш ва файл юклаш синовлари — ``tests_intake.py`` да (текширув навбати)."""

    def setUp(self):
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)

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
        self.assertFalse(Item.objects.exists() or Price.objects.exists() or Stat.objects.exists())  # саватчада
        self.assertContains(self.client.get(reverse("panel:narx_trash")), "Ноутбук")
        self.client.post(reverse("panel:narx_trash_purge", args=["item", p.item_id]))
        self.assertFalse(Item.all_objects.exists() or Price.all_objects.exists() or Batch.all_objects.exists())


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


# ===================== тузатиш, бирлаштириш, саватча =====================
from .models import Alias, Log  # noqa: E402


class EditMergeTrashTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)

    def manual(self, name, price, unit="т", region="buxoro", day=date(2026, 3, 1)):
        return services.add_manual(name=name, unit=unit, category="K", price=D(price), region=region, day=day,
                                   user=self.admin)

    def stat(self, item, region="buxoro", year=2026):
        return Stat.objects.get(item=item, region=region, year=year)

    def test_edit_price_logs_and_recalcs(self):
        p = self.manual("Цемент М400", 12_000_000)  # битта нол ортиқча
        self.manual("Цемент М400", 1_100_000)
        url = reverse("panel:narx_price_edit", args=[p.pk])
        self.client.post(url, {"price": "1 200 000", "region": "buxoro", "date": "2026-03-01"})  # сабабсиз — йўқ
        p.refresh_from_db()
        self.assertEqual(p.price, D(12_000_000))
        self.client.post(url, {"price": "1 200 000", "region": "buxoro", "date": "2026-03-01", "reason": "нол ортиқча"})
        p.refresh_from_db()
        self.assertEqual((p.price, p.edited), (D(1_200_000), True))
        self.assertEqual(self.stat(p.item).high, D(1_200_000))
        g = Log.objects.get(action=Log.Action.PRICE_EDIT)
        self.assertEqual((g.data["old"]["price"], g.data["reason"], g.user), ("12000000.0000", "нол ортиқча", self.admin))
        r = self.client.get(reverse("panel:narx_item", args=[p.item_id]))
        self.assertContains(r, "нол ортиқча")
        self.assertContains(r, "12 000 000 → 1 200 000")

    def test_edit_price_moves_region_and_year(self):
        p = self.manual("Қум", 100_000)
        services.edit_price(p, price=D(100_000), region="xorazm", day=date(2025, 6, 1), reason="вилоят хато")
        self.assertFalse(Stat.objects.filter(item=p.item, region="buxoro").exists())
        self.assertEqual(self.stat(p.item, "xorazm", 2025).mid, D(100_000))
        self.assertFalse(services.edit_price(p, price=D(100_000), region="xorazm", day=date(2025, 6, 1), reason="x"))

    def test_rename_keeps_old_name_as_alias(self):
        p = self.manual("ЦЕМЕНТ М-400", 1_000_000)
        it = p.item
        services.edit_item(it, name="Цемент М400", category="K", user=self.admin)
        self.assertEqual(Alias.objects.get().item, it)
        p2 = self.manual("цемент  м-400", 1_100_000)  # эски ном билан келди — ўша ресурсга
        self.assertEqual(p2.item_id, it.pk)
        other = self.manual("Цемент М500", 1_300_000).item
        with self.assertRaises(services.Conflict):
            services.edit_item(other, name="Цемент М-400", category="K")  # бошқа ресурснинг эски номи

    def test_merge_items(self):
        a = self.manual("Цемент М400", 1_000_000).item
        b_price = self.manual("Цемент марки 400", 1_200_000)
        b = b_price.item
        self.manual("Цемент марки 400", 1_300_000)
        self.assertIn(b, services.similar_items(a, "марки"))
        r = self.client.post(reverse("panel:narx_item_merge", args=[a.pk]), {"source": b.pk})
        self.assertRedirects(r, reverse("panel:narx_item", args=[a.pk]))
        self.assertFalse(Item.all_objects.filter(pk=b.pk).exists())
        self.assertEqual(a.prices.count(), 3)
        st = self.stat(a)
        self.assertEqual((st.low, st.mid, st.high, st.n), (D(1_000_000), D(1_200_000), D(1_300_000), 3))
        self.assertEqual(self.manual("ЦЕМЕНТ МАРКИ 400", 1_250_000).item_id, a.pk)  # эски номи — шу ерга
        m = services.price_map({"region": "buxoro", "year": 2026, "level": "mid"})
        self.assertEqual(m[services.item_key("Цемент марки 400", "т")]["price"], m[a.key]["price"])
        kg = self.manual("Цемент М400", 1000, unit="кг").item
        with self.assertRaises(ValueError):
            services.merge_items(a, kg)  # бирлиги бошқа

    def test_trash_and_restore_item(self):
        p = self.manual("Гипс", 50_000)
        it = p.item
        services.trash_item(it, self.admin)
        self.assertFalse(Item.objects.filter(pk=it.pk).exists())
        self.assertFalse(Stat.objects.filter(item=it).exists())
        self.assertNotIn(services.item_key("Гипс", "т"), services.price_map({"region": "buxoro", "year": 2026}))
        p2 = self.manual("ГИПС", 60_000)  # шу орада янги ресурс очилди
        self.assertNotEqual(p2.item_id, it.pk)
        r = self.client.post(reverse("panel:narx_trash_restore", args=["item", it.pk]))
        self.assertRedirects(r, reverse("panel:narx_item", args=[p2.item_id]))  # янгисига қўшилди
        self.assertEqual(self.stat(p2.item).n, 2)
        q = self.manual("Оҳак", 70_000)
        services.trash_item(q.item)
        back = services.restore_item(Item.all_objects.get(pk=q.item_id))
        self.assertEqual((back.pk, back.key), (q.item_id, services.item_key("Оҳак", "т")))
        self.assertEqual(self.stat(back).mid, D(70_000))

    def test_trash_restore_purge_batch(self):
        job = make_job(self.admin, [row(11, "ВОДА", "М3", 760)])
        b = services.add_batch("smeta", job, services.rows_from_jobs([job]), region="buxoro", day=date(2026, 5, 3))
        self.client.post(reverse("panel:narx_batch_delete", args=[b.pk]))
        self.assertFalse(Stat.objects.exists())
        self.assertIn(("smeta", job), services.pending_works())  # яна йиғишда
        self.assertEqual(self.client.get(reverse("panel:narx_trash")).status_code, 200)
        self.client.post(reverse("panel:narx_trash_restore", args=["batch", b.pk]))
        self.assertTrue(Stat.objects.exists())
        services.trash_batch(Batch.objects.get(pk=b.pk))
        b2 = services.add_batch("smeta", job, services.rows_from_jobs([job]), region="buxoro", day=date(2026, 5, 4))
        with self.assertRaises(ValueError):  # шу иш қайта киритилган
            services.restore_batch(Batch.all_objects.get(pk=b.pk))
        self.client.post(reverse("panel:narx_trash_purge", args=["batch", b.pk]))
        self.assertFalse(Batch.all_objects.filter(pk=b.pk).exists())
        self.assertEqual(Price.objects.get().batch, b2)
        self.assertTrue(Log.objects.filter(action=Log.Action.PURGE).exists())

    def test_edit_batch_region(self):
        job = make_job(self.admin, [row(11, "ВОДА", "М3", 760), row(12, "ЩЕБЕНЬ", "М3", 90_000)])
        b = services.add_batch("smeta", job, services.rows_from_jobs([job]), region="buxoro", day=date(2026, 5, 3))
        alone = Price.objects.get(name="ЩЕБЕНЬ")
        services.edit_price(alone, price=D(95_000), region="navoiy", day=date(2026, 5, 3), reason="бошқа вилоят")
        r = self.client.post(reverse("panel:narx_batch_edit", args=[b.pk]), {"region": "xorazm", "date": "2025-12-01"})
        self.assertRedirects(r, reverse("panel:narx_batch", args=[b.pk]))
        water = Price.objects.get(name="ВОДА")
        self.assertEqual((water.region, water.year), ("xorazm", 2025))
        self.assertEqual(Price.objects.get(name="ЩЕБЕНЬ").region, "navoiy")  # алоҳида тузатилгани ўзгармади
        self.assertEqual(set(Stat.objects.values_list("region", "year")), {("xorazm", 2025), ("navoiy", 2026)})

    def test_pages_render(self):
        p = self.manual("Цемент М400", 1_000_000)
        self.manual("Цемент марки М400", 1_200_000)
        r = self.client.get(reverse("panel:narx_item", args=[p.item_id]))
        self.assertContains(r, "Шу ресурсга қўшиш")  # ўхшаш ном таклиф қилинди
        self.assertContains(r, "Нархни тузатиш")
        self.assertEqual(self.client.get(reverse("panel:narx_batch", args=[p.batch_id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("panel:narx_trash")).status_code, 200)
        staff = User.objects.create_user("st@test.uz", PASSWORD, first_name="S", last_name="T", is_staff=True)
        self.client.force_login(staff)
        self.assertNotContains(self.client.get(reverse("panel:narx_item", args=[p.item_id])), "Шу ресурсга қўшиш")
        self.client.post(reverse("panel:narx_item_delete", args=[p.item_id]))
        self.assertTrue(Item.objects.filter(pk=p.item_id).exists())  # кўрувчи ўчира олмайди


# ===================== динамика, текшириш навбати, бирданига =====================
class Stage2Tests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)

    def manual(self, name, price, unit="т", region="buxoro", day=date(2026, 3, 1), cat="K"):
        return services.add_manual(name=name, unit=unit, category=cat, price=D(price), region=region, day=day,
                                   user=self.admin)

    def test_dynamics(self):
        p = self.manual("Цемент М400", 1_000_000, day=date(2024, 3, 1))
        self.manual("Цемент М400", 1_100_000, day=date(2025, 3, 1))
        self.manual("Цемент М400", 1_210_000, day=date(2026, 3, 1))
        self.manual("Цемент М400", 1_300_000, region="xorazm", day=date(2025, 3, 1))
        d = services.dynamics(list(p.item.stats.all()))
        self.assertEqual(d["years"], [2024, 2025, 2026])
        self.assertEqual([(s.region, s.year) for s in d["latest"]], [("buxoro", 2026), ("xorazm", 2025)])
        bux = d["rows"][0]
        self.assertEqual([c["change"] for c in bux["cells"]], [None, 10.0, 10.0])
        self.assertEqual(len(bux["spark"].split()), 3)
        self.assertEqual(d["rows"][1]["spark"], "")  # битта йил — график йўқ
        r = self.client.get(reverse("panel:narx_item", args=[p.item_id]))
        self.assertContains(r, "Йиллар бўйича ўзгариши")
        self.assertContains(r, "+10,0% олдинги йилга")

    def test_duplicates_and_ignore(self):
        a = self.manual("Агрегаты окрасочные высокого давления", 100).item
        b = self.manual("Агрегаты окрасочные высокогодавления", 110).item
        self.manual("Лист толщ 5мм", 100)
        self.manual("Лист толщ 6мм", 100)  # рақами бошқа — икки нусха эмас
        self.manual("Агрегаты окрасочные высокого давления", 100, unit="шт")  # бирлиги бошқа
        self.assertEqual([(x, y) for _, x, y in services.duplicate_pairs()], [(a.pk, b.pk)])
        r = self.client.get(reverse("panel:narx_check"))
        self.assertContains(r, "Шу номга бирлаштириш")
        self.client.post(reverse("panel:narx_check_ignore"), {"a": b.pk, "b": a.pk})
        self.assertEqual(services.duplicate_pairs(), [])
        self.assertEqual(services.review_counts()["dup"], 0)

    def test_merge_from_queue_returns_back(self):
        a = self.manual("Агрегаты окрасочные высокого давления", 100).item
        b = self.manual("Агрегаты окрасочные высокогодавления", 110).item
        back = reverse("panel:narx_check") + "?s=dup"
        r = self.client.post(reverse("panel:narx_item_merge", args=[a.pk]), {"source": b.pk, "next": back})
        self.assertRedirects(r, back)
        self.assertEqual(a.prices.count(), 2)

    def test_other_sections(self):
        self.manual("Нимадир", 100, cat="O")
        self.manual("Эски қум", 100, day=date(2020, 1, 1))
        for price in (100, 105, 110, 1000):
            self.manual("Шағал", price)
        c = services.review_counts()
        self.assertEqual((c["cat"], c["outlier"], c["stale"]), (1, 1, 1))
        for sec, text in (("cat", "Нимадир"), ("stale", "Эски қум"), ("outlier", "Шағал")):
            self.assertContains(self.client.get(reverse("panel:narx_check") + f"?s={sec}"), text)

    def test_bulk(self):
        ids = [self.manual(n, 100, cat="O").item_id for n in ("Бир", "Икки", "Уч")]
        url = reverse("panel:narx_bulk")
        self.client.post(url, {"ids": ids[:2], "action": "category", "category": "M"})
        self.assertEqual(sorted(Item.objects.values_list("category", flat=True)), ["M", "M", "O"])
        self.assertEqual(Log.objects.filter(action=Log.Action.CATEGORY).count(), 2)
        kg = self.manual("Тўрт", 100, unit="кг").item_id
        r = self.client.post(url, {"ids": ids + [kg], "action": "merge", "target": ids[0]})
        self.assertRedirects(r, reverse("panel:narx_item", args=[ids[0]]), fetch_redirect_response=False)
        self.assertEqual(Item.objects.get(pk=ids[0]).prices.count(), 3)
        self.assertTrue(Item.objects.filter(pk=kg).exists())  # бирлиги бошқа — қўшилмади
        self.client.post(url, {"ids": [ids[0], kg], "action": "trash"})
        self.assertFalse(Item.objects.exists())
        self.assertEqual(services.trash_counts()["items"], 2)

    def test_list_has_bulk_bar_only_for_admin(self):
        self.manual("Бир", 100)
        self.assertContains(self.client.get(reverse("panel:narx")), 'id="nx-bulk"')
        staff = User.objects.create_user("st@test.uz", PASSWORD, first_name="S", last_name="T", is_staff=True)
        self.client.force_login(staff)
        self.assertNotContains(self.client.get(reverse("panel:narx")), 'id="nx-bulk"')
        self.assertEqual(self.client.get(reverse("panel:narx_check")).status_code, 200)
        self.assertEqual(self.client.post(reverse("panel:narx_bulk"), {"ids": [1], "action": "trash"}).status_code, 403)


# ===================== статистика =====================
class StatsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)

    def manual(self, name, price, region, year=2026, unit="т"):
        return services.add_manual(name=name, unit=unit, category="K", price=D(price), region=region,
                                   day=date(year, 3, 1))

    def test_price_index_and_overview(self):
        from . import stats as S
        for region, k in (("buxoro", 1.0), ("xorazm", 1.2), ("samarqand", 0.9)):
            self.manual("Цемент", 1000 * k, region)
            self.manual("Қум", 100 * k, region)
        self.manual("Гипс", 50, "buxoro")  # битта вилоятда — солиштирилмайди
        self.manual("Цемент", 900, "buxoro", year=2025)
        idx, _ = S.price_index(2026)
        self.assertEqual(idx["buxoro"], {"pct": 0.0, "n": 2})
        self.assertEqual(idx["xorazm"]["pct"], 20.0)
        self.assertEqual(idx["samarqand"]["pct"], -10.0)
        ov = S.overview(2026, "index", "xorazm")
        rank = [r["code"] for r in ov["rank"]]
        self.assertEqual(rank, ["xorazm", "buxoro", "samarqand"])
        reg = {r["code"]: r for r in ov["regions"]}
        self.assertEqual(len(reg), 14)
        self.assertEqual((reg["buxoro"]["items"], reg["xorazm"]["color"], reg["samarqand"]["color"]),
                         (3, S.DIV_HI[3], S.DIV_LO[1]))
        self.assertEqual(reg["navoiy"]["color"], S.EMPTY)
        self.assertEqual(S.overview(None, "items")["covered"], 3)
        mx = S.matrix()
        self.assertEqual(mx["years"], [2025, 2026])
        bux = next(r for r in mx["rows"] if r["code"] == "buxoro")
        self.assertEqual([c["n"] for c in bux["cells"]], [1, 3])
        d = S.region_detail("xorazm", 2026, ov["ratios"])
        self.assertEqual([x["pct"] for x in d["dear"]], [20, 20])
        self.assertEqual(d["cheap"], [])

    def test_page(self):
        self.manual("Цемент", 1000, "buxoro")
        self.manual("Цемент", 1200, "xorazm")
        url = reverse("panel:narx_stats")
        for q in ("", "?y=2026", "?m=prices", "?m=index&r=xorazm", "?y=1999&m=bad&r=bad"):
            r = self.client.get(url + q)
            self.assertEqual(r.status_code, 200, q)
        r = self.client.get(url + "?m=index&r=xorazm")
        self.assertContains(r, "Мамлакатдан энг")
        self.assertContains(r, "+9,1%")  # 1200 / медиана 1100
        self.assertContains(r, "OpenStreetMap")

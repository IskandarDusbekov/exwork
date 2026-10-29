"""Ресурс базаси: код ва бирликни бир хиллаштириш, сметадан йиғиш, медиана, панел саҳифалари."""
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from jobs.models import Batch, Job, Project
from panel.models import Event

from . import services
from .models import BaseConfig, Observation, Resource, Source
from .regions import guess_region

PASSWORD = "Kuchli-parol-2026"


def row(n, code, name, unit, price, status="EXACT", **kw):
    return {"row": n, "code": code, "name": name, "unit": unit, "g": price, "status": status, "category": kw.pop("cat", "K"),
            "default_category": kw.pop("cat_default", None), "manual": kw.pop("manual", False), **kw}


def make_job(owner, rows, **kw):
    return Job.objects.create(owner=owner, local_name=kw.pop("name", "lokal.xlsx"), res_name="res.xlsx", local_path="x",
                              res_path="y", status=Job.Status.DONE, result={"rows": rows}, **kw)


class NormalizeTests(TestCase):
    def test_codes(self):
        self.assertEqual(services.norm_code("000258"), "258")
        self.assertEqual(services.norm_code(258.0), "258")
        self.assertEqual(services.norm_code("00001"), "1")
        for bad in ("С1", "Е0101-195-26", "15-104045-20", "", None, "1234567"):
            self.assertEqual(services.norm_code(bad), "", bad)

    def test_units(self):
        self.assertEqual(services.unit_key("ЧЕЛ.-Ч"), services.unit_key("ЧЕЛ-ЧАС"))
        self.assertEqual(services.unit_key("МАШ.-Ч"), services.unit_key("маш-час"))
        self.assertNotEqual(services.unit_key("100ШТ"), services.unit_key("ШТ"))

    def test_region_guess(self):
        self.assertEqual(guess_region(['IL "URGUT TUMANIDAGI YANGI-DARGOM KANALINING']), ("samarqand", "urgut"))
        self.assertEqual(guess_region(["Тошкент вилояти Кибрай туманида жойлашган"])[0], "toshkent_v")
        self.assertEqual(guess_region(["Парк Чирчик Амир Темур"])[0], "toshkent_v")
        self.assertEqual(guess_region(["НАИМЕНОВАНИЕ СТРОЙКИ: 7-БОЛНИЦА"]), ("", ""))


class HarvestTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ali@test.uz", PASSWORD, first_name="A", last_name="V")

    def test_only_reliable_rows_once_per_smeta(self):
        job = make_job(self.user, [
            row(10, "Е0101-001", "РАЗРАБОТКА ГРУНТА", "1000М3", 0, status="WORK_HEADER"),
            row(11, "000001", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 36030.1, cat="I"),
            row(12, "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 36030.1, cat="I"),  # такрор
            row(13, "258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", 275240, status="FUZZY"),       # тахминий — йўқ
            row(14, "43113", "ЩЕБЕНЬ", "М3", 0),                                        # нархсиз — йўқ
            row(15, "С1", "АРМАТУРА А-III", "Т", 9000000),                              # кодсиз — йўқ
            row(16, "9219", "ВОДА", "М3", 5000, status="MANUAL", manual=True),          # қўлда — белги билан
        ])
        taken, skipped, total = services.rows_from_job(job)
        self.assertEqual([t["code"] for t in taken], ["1", "9219"])
        self.assertEqual(dict(skipped), {"duplicate": 1, "not_exact": 1, "zero_price": 1, "no_code": 1})
        self.assertEqual(total, 6)
        self.assertTrue(taken[1]["manual"])

    def test_harvest_median_and_period(self):
        jobs = [make_job(self.user, [row(11, "258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", p, cat="J")], name=f"s{i}.xlsx")
                for i, p in enumerate([100, 200, 250])]
        sources = [services.add_source("smeta", j.id, region="samarqand", year=2025, quarter=2, user=self.user) for j in jobs]
        res = Resource.objects.get(code="258")
        self.assertEqual((res.unit_key, res.category), ("машч", "J"))
        self.assertEqual(services.price_table("samarqand", 2025, 2)["258"], (Decimal("200.00"), 3))
        self.assertEqual(services.price_table("samarqand", 2025, 3), {})  # бошқа давр — олинмайди
        self.assertEqual(services.price_table("", None, None), {})
        # истисно — медианага кирмайди; кеш янгиланади
        o = Observation.objects.get(source=sources[2])
        o.excluded = True
        o.save()
        services.bump()
        self.assertEqual(services.price_table("samarqand", 2025, 2)["258"], (Decimal("150.00"), 2))
        # манба даври ўзгарса — нарх ўша даврга ўтади
        sources[0].quarter = 3
        sources[0].save()
        services.bump()
        self.assertEqual(services.price_table("samarqand", 2025, 3)["258"], (Decimal("100.00"), 1))

    def test_unknown_period_not_used_and_delete_cleans(self):
        job = make_job(self.user, [row(11, "45002", "КРУГ ОТРЕЗНОЙ", "ШТ", 12000)])
        s = services.add_source("smeta", job.id, user=self.user)  # давр номаълум
        self.assertFalse(s.period_known)
        self.assertEqual(Resource.objects.count(), 1)
        self.assertEqual(services.price_table("toshkent_sh", 2025, 1), {})
        services.delete_source(s)
        self.assertFalse(Resource.objects.exists())  # ҳеч қаерда учрамайдиган код қолмайди

    def test_folder_reharvest_replaces(self):
        project = Project.objects.create(owner=self.user, name="Папка", status=Project.Status.DONE)
        part = Batch.objects.create(owner=self.user, project=project, folder="A", name="A")
        job = make_job(self.user, [row(11, "6321", "БЕТОН В15", "М3", 900000)], batch=part)
        s = services.add_source("folder", project.id, region="toshkent_v", year=2025, quarter=1)
        self.assertEqual(s.stats["taken"], 1)
        job.result["rows"].append(row(12, "9219", "ВОДА", "М3", 4000))
        job.save()
        services.harvest(s)
        self.assertEqual(s.observations.count(), 2)
        self.assertEqual(Source.objects.count(), 1)


class ReviewTests(TestCase):
    """4-bosqich: shubhali nom, birlik va narx — admin qaror qilguncha medianaga kirmaydi."""

    def setUp(self):
        self.user = User.objects.create_user("ali@test.uz", PASSWORD, first_name="A", last_name="V")

    def add(self, price, name="БУЛЬДОЗЕРЫ 79 КВТ", unit="МАШ.-Ч", code="258", quarter=2):
        job = make_job(self.user, [row(11, code, name, unit, price, cat="J")])
        return services.add_source("smeta", job.id, region="samarqand", year=2025, quarter=quarter)

    def flags(self):
        return {o.price: (o.flags, o.review) for o in Observation.objects.all()}

    def test_name_similarity(self):
        self.assertGreaterEqual(services.name_similarity(
            "БУЛЬДОЗЕРЫ 79 КВТ", "БУЛЬДОЗЕРЫ ПРИ РАБОТЕ НА ДРУГИХ ВИДАХ СТРОИТЕЛЬСТВА 79 (108) КВТ (Л.С.)"), 0.5)
        self.assertLess(services.name_similarity("ВОДА", "ГВОЗДИ СТРОИТЕЛЬНЫЕ"), 0.5)
        self.assertEqual(services.name_similarity("", "ВОДА"), 0)

    def test_price_outlier_pending_until_decided(self):
        for p in (100, 110, 120):
            self.add(p)
        self.add(1000)
        self.assertEqual(self.flags()[Decimal("1000.0000")], ("price", "pending"))
        self.assertEqual(services.price_table("samarqand", 2025, 2)["258"], (Decimal("110.00"), 3))
        o = Observation.objects.get(price=1000)
        services.decide([o.pk], accept=True)
        self.assertEqual(services.price_table("samarqand", 2025, 2)["258"], (Decimal("115.00"), 4))
        services.decide([o.pk], accept=False)
        o.refresh_from_db()
        self.assertTrue(o.excluded)
        self.assertEqual(services.price_table("samarqand", 2025, 2)["258"], (Decimal("110.00"), 3))
        # 2 та нархдан қайсиси хато — билиб бўлмайди: белгиланмайди
        self.add(5, code="9999", name="ВОДА", unit="М3", quarter=3)
        self.add(500, code="9999", name="ВОДА", unit="М3", quarter=3)
        self.assertFalse(Observation.objects.filter(resource__code="9999").exclude(review="").exists())

    def test_name_and_unit_flags_and_accepted_variant_remembered(self):
        self.add(100)
        self.add(105, name="ГВОЗДИ СТРОИТЕЛЬНЫЕ")
        self.add(110, unit="Т")
        f = self.flags()
        self.assertEqual((f[Decimal("100.0000")], f[Decimal("105.0000")], f[Decimal("110.0000")]),
                         (("", ""), ("name", "pending"), ("unit", "pending")))
        self.assertEqual(services.price_table("samarqand", 2025, 2)["258"], (Decimal("100.00"), 1))
        services.decide([Observation.objects.get(price=105).pk], accept=True)
        self.add(120, name="ГВОЗДИ СТРОИТЕЛЬНЫЕ")  # қабул қилинган ном — энди шубҳали эмас
        self.assertEqual(self.flags()[Decimal("120.0000")], ("", ""))

    def test_panel_queue(self):
        admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.client.force_login(admin)
        for p in (100, 110, 120, 1000):
            self.add(p)
        self.add(105, name="ГВОЗДИ СТРОИТЕЛЬНЫЕ")
        url = reverse("panel:base_review")
        page = self.client.get(url)
        self.assertContains(page, "ГВОЗДИ СТРОИТЕЛЬНЫЕ")
        self.assertContains(page, "нарх кескин фарқ")
        self.assertNotContains(self.client.get(url, {"f": "price"}), "ГВОЗДИ СТРОИТЕЛЬНЫЕ")
        ids = list(Observation.objects.filter(review="pending").values_list("pk", flat=True))
        self.client.post(reverse("panel:base_review_decide"), {"ids": ids[:1], "action": "accept"})
        self.client.post(reverse("panel:base_review_decide"), {"ids": ids[1:], "action": "reject"})
        self.assertFalse(Observation.objects.filter(review="pending").exists())
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_BASE, detail__contains="текширув").exists())
        self.assertEqual(self.client.post(reverse("panel:base_recheck")).status_code, 302)


class CoverageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ali@test.uz", PASSWORD, first_name="A", last_name="V")
        for i, (p, region, quarter) in enumerate([(100, "samarqand", 2), (300, "samarqand", 2), (500, "buxoro", 2)]):
            job = make_job(self.user, [row(11, "258", "БУЛЬДОЗЕРЫ", "МАШ.-Ч", p, cat="J"),
                                       row(12, "1", "ЗАТРАТЫ ТРУДА", "ЧЕЛ.-Ч", 30000, cat="I")], name=f"s{i}.xlsx")
            services.add_source("smeta", job.id, region=region, year=2025, quarter=quarter)
        services.add_source("smeta", make_job(self.user, [row(11, "1", "ЗАТРАТЫ ТРУДА", "ЧЕЛ.-Ч", 1)]).id,
                            region="", year=None, quarter=None)  # фақат номаълум даврда
        self.target = make_job(self.user, [
            row(10, "Е0101-001", "РАЗРАБОТКА", "1000М3", 0, status="WORK_HEADER"),
            row(11, "000258", "БУЛЬДОЗЕРЫ", "МАШ.-Ч", 0, status="NOT_FOUND"),
            row(12, "258", "БУЛЬДОЗЕРЫ", "МАШ.-Ч", 0, status="NOT_FOUND"),
            row(13, "43113", "ЩЕБЕНЬ", "М3", 0, status="NOT_FOUND"),
            row(14, "С1", "АРМАТУРА", "Т", 0, status="NOT_FOUND"),
            row(15, "1", "ЗАТРАТЫ ТРУДА", "ЧЕЛ.-Ч", 0, status="NOT_FOUND"),
        ])

    def test_counts_and_lists(self):
        rep = services.coverage(self.target, "samarqand", 2025, 2)
        self.assertEqual(rep["total"], 5)
        self.assertEqual({k: n for k, n, _ in rep["counts"]}, {"exact": 3, "other": 0, "none": 1, "no_code": 1})
        self.assertEqual(rep["items"][0]["median"], Decimal("200.00"))
        self.assertEqual([m["code"] for m in rep["missing"]], ["43113"])
        rep = services.coverage(self.target, "xorazm", 2025, 2)  # бу вилоятда йўқ — бошқа жойда бор
        self.assertEqual({k: n for k, n, _ in rep["counts"]}["other"], 3)
        self.assertEqual(rep["other"][0]["code"], "258")
        self.assertEqual(rep["other"][0]["rows"], 2)

    def test_own_prices_ignored_by_default(self):
        own = Job.objects.get(local_name="s2.xlsx")  # бухоро — ягона манба
        rep = services.coverage(own, "buxoro", 2025, 2)
        self.assertEqual({k: n for k, n, _ in rep["counts"]}["exact"], 0)
        self.assertEqual({k: n for k, n, _ in services.coverage(own, "buxoro", 2025, 2, own=True)["counts"]}["exact"], 2)

    def test_page(self):
        admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.client.force_login(admin)
        url = reverse("panel:base_coverage")
        page = self.client.get(url, {"job": self.target.id, "region": "samarqand", "year": "2025", "quarter": "2"})
        self.assertContains(page, "43113")
        self.assertContains(page, "(60%)")
        src_job = Job.objects.get(local_name="s0.xlsx")
        page = self.client.get(url, {"job": src_job.id})  # давр — манбадан
        self.assertEqual((page.context["r"], page.context["y"], page.context["q"]), ("samarqand", 2025, 2))
        self.assertEqual(self.client.get(url, {"job": "nope"}).status_code, 404)


class PanelBaseTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.staff = User.objects.create_user("staff@test.uz", PASSWORD, first_name="S", last_name="T", is_staff=True)
        self.user = User.objects.create_user("user@test.uz", PASSWORD, first_name="U", last_name="V")
        self.job = make_job(self.user, [row(11, "000258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", 275240, cat="J")], name="Урgut.xlsx")

    def test_access(self):
        for url in (reverse("panel:base"), reverse("panel:base_search"), reverse("panel:base_sources"),
                    reverse("panel:base_coverage"), reverse("panel:base_review")):
            self.client.force_login(self.user)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.client.force_login(self.staff)
            self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse("panel:base_toggle"), {"enabled": "1"}).status_code, 403)
        self.assertFalse(BaseConfig.get().enabled)

    def test_add_edit_search_and_toggle(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("panel:base_sources"))
        self.assertContains(page, "Урgut.xlsx")  # ҳали қўшилмаган
        self.client.post(reverse("panel:base_add", args=["smeta", self.job.id]),
                         {"region": "samarqand", "year": "2025", "quarter": "2"})
        s = Source.objects.get()
        self.assertEqual((s.region, s.year, s.quarter, s.stats["taken"]), ("samarqand", 2025, 2, 1))
        self.assertContains(self.client.get(reverse("panel:base_search"), {"q": "000258"}), "БУЛЬДОЗЕРЫ 79 КВТ")
        self.assertContains(self.client.get(reverse("panel:base_search"), {"q": "бульдоз"}), reverse("panel:base_resource", args=[258]))
        detail = self.client.get(reverse("panel:base_resource", args=["258"]))
        self.assertContains(detail, "275")
        self.client.post(reverse("panel:base_resource_edit", args=["258"]), {"name": "БУЛЬДОЗЕР 79 кВт", "unit": "маш.-ч", "category": "J"})
        res = Resource.objects.get(code="258")
        self.assertTrue(res.edited)
        self.client.post(reverse("panel:base_source", args=[s.pk, "reharvest"]))
        self.assertEqual(Resource.objects.get(code="258").name, "БУЛЬДОЗЕР 79 кВт")  # админ таҳрири сақланади
        self.client.post(reverse("panel:base_source", args=[s.pk, "edit"]), {"region": "samarqand", "year": "", "quarter": ""})
        self.assertFalse(Source.objects.get().period_known)
        overview = self.client.get(reverse("panel:base"))
        self.assertContains(overview, "давр номаълум")
        self.client.post(reverse("panel:base_toggle"), {"enabled": "1"})
        self.assertTrue(BaseConfig.get().enabled)
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_BASE).exists())

    def test_work_page_card_and_wipe(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("panel:work", args=["smeta", self.job.id]))
        self.assertContains(page, "Базага қўшиш")
        self.client.post(reverse("panel:base_add_all"))
        self.assertEqual(Source.objects.count(), 1)
        self.assertContains(self.client.get(reverse("panel:work", args=["smeta", self.job.id])), "Базада:")
        self.client.post(reverse("panel:base_wipe"))
        self.assertFalse(Resource.objects.exists() or Source.objects.exists())


TMP_MEDIA = Path(tempfile.mkdtemp(prefix="narxlash_resbase_"))


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class PricingFromBaseTests(TestCase):
    """3-bosqich: RES topa olmagan (yoki RES'siz) qatorlar ресурс базасидан."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user("ali@test.uz", PASSWORD, first_name="A", last_name="V")
        src = make_job(self.user, [row(11, "762", "КРАНЫ НА АВТОМОБИЛЬНОМ ХОДУ", "МАШ.-Ч", 250000, cat="J"),
                                   row(12, "6322", "БЕТОН ТЯЖЕЛЫЙ", "М3", 500000)])
        services.add_source("smeta", src.id, region="samarqand", year=2026, quarter=4)
        self.client.force_login(self.user)

    def _enable(self, **period):
        cfg = BaseConfig.get()
        cfg.enabled = True
        cfg.region, cfg.year, cfg.quarter = period.get("region", "samarqand"), period.get("year", 2026), period.get("quarter", 4)
        cfg.save()

    def test_period_choice_and_price_map(self):
        from jobs.services import base_period_of
        self.assertEqual(base_period_of(self.user), {})  # база ўчиқ
        self.assertEqual(services.price_map({"region": "samarqand", "year": 2026, "quarter": 4}), {})
        self._enable()
        self.assertEqual(base_period_of(self.user), {"region": "samarqand", "year": 2026, "quarter": 4})  # админ сукути
        self.assertContains(self.client.get(reverse("index")), "Нарх манбаи")
        r = self.client.post(reverse("base_period"), {"region": "buxoro", "year": "2025", "quarter": "9"})
        self.assertEqual(r.json()["ok"], True)
        self.assertEqual(base_period_of(self.user), {"region": "buxoro", "year": 2025, "quarter": None})
        m = services.price_map({"region": "samarqand", "year": 2026, "quarter": 4})
        self.assertEqual((m["762"]["price"], m["762"]["n"], m["762"]["unit_key"], m["762"]["category"]), (250000.0, 1, "машч", "J"))

    def test_local_without_res_priced_from_base(self):
        from core.tests.conftest import build_local
        self._enable()
        local = build_local(TMP_MEDIA / "Локал.xlsx")
        self.client.post(reverse("index"), {"files": [SimpleUploadedFile("Локал.xlsx", local.read_bytes())]})
        batch = Batch.objects.get()
        self.assertEqual((batch.pairs[0]["res"], batch.pairs[0]["base"], batch.pairs[0]["include"]), (None, True, True))
        self.assertContains(self.client.get(reverse("batch", args=[batch.id])), "Ресурс базаси (RES'сиз)")
        self.client.post(reverse("batch_confirm", args=[batch.id]), {"include_0": "1", "res_0": "__base__"})
        job = Job.objects.get(batch=batch)
        self.assertEqual((job.status, job.res_path, job.res_name), (Job.Status.DONE, "", "ресурс базаси"))
        rows = {r["code"]: r for r in job.result["rows"] if r.get("code")}
        self.assertEqual((rows["762"]["status"], rows["762"]["g"]), ("BASE", 250000))
        self.assertEqual(rows["23469"]["status"], "NOT_FOUND")  # базада йўқ
        page = self.client.get(reverse("job", args=[job.id]))
        self.assertContains(page, "База: Самарқанд")
        self.assertContains(page, "БАЗА")
        self.assertEqual(self.client.get(reverse("job_sheet", args=[job.id]), {"f": "res"}).status_code, 404)
        # базадан олинган нарх қайта йиғилмайди (фақат EXACT/қўлда)
        self.assertEqual(services.rows_from_job(job)[0], [])
        # давр ўзгарди — бу даврда нарх йўқ, қатор қайта ТОПИЛМАДИ бўлади
        self.client.post(reverse("job_base", args=[job.id]), {"region": "samarqand", "year": "2025", "quarter": "1"})
        job.refresh_from_db()
        self.assertEqual({r["code"]: r for r in job.result["rows"] if r.get("code")}["762"]["status"], "NOT_FOUND")
        # база ўчирилди — RES'сиз жуфтлик нархланмайди
        BaseConfig.objects.filter(pk=1).update(enabled=False)
        self.client.post(reverse("batch_confirm", args=[batch.id]), {"include_0": "1", "res_0": "__base__"})
        self.assertFalse(Job.objects.filter(batch=batch).exists())

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

    def test_one_code_for_different_resources_in_smeta(self):
        job = make_job(self.user, [
            row(11, "1360", "ЗАГЛУШКА СТ. Д=100ММ", "ШТ", 179400),
            row(12, "1360", "ОТВОД 90 ГР. Д=75ММ", "ШТ", 25704),
            row(13, "1", "ЗАТРАТЫ ТРУДА РАБОЧИХ-СТРОИТЕЛЕЙ", "ЧЕЛ.-Ч", 36030.1, cat="I"),
        ])
        taken, skipped, _ = services.rows_from_job(job)
        self.assertEqual([t["code"] for t in taken], ["1"])
        self.assertEqual(dict(skipped), {"code_conflict": 2})

    def test_harvest_median_and_period(self):
        jobs = [make_job(self.user, [row(11, "258", "БУЛЬДОЗЕРЫ 79 КВТ", "МАШ.-Ч", p, cat="J")], name=f"s{i}.xlsx")
                for i, p in enumerate([100, 200, 250])]
        sources = [services.add_source("smeta", j.id, region="samarqand", year=2025, quarter=2, user=self.user) for j in jobs]
        res = Resource.objects.get(code="258")
        self.assertEqual((res.unit_key, res.category), ("машч", "J"))
        self.assertEqual(services.price_table("samarqand", 2025, 2)["258"], (Decimal("200.00"), 3))
        self.assertEqual(services.price_table("samarqand", 2025, 3)["258"], (Decimal("200.00"), 3))  # чорак ишлатилмайди
        self.assertEqual(services.price_table("samarqand", 2024), {})  # бошқа йил — олинмайди
        self.assertEqual(services.price_table("toshkent_sh", 2025), {})  # бошқа вилоят — олинмайди
        self.assertEqual(services.price_table("", None, None), {})
        # истисно — медианага кирмайди; кеш янгиланади
        o = Observation.objects.get(source=sources[2])
        o.excluded = True
        o.save()
        services.bump()
        self.assertEqual(services.price_table("samarqand", 2025, 2)["258"], (Decimal("150.00"), 2))
        # манба йили ўзгарса — нарх ўша йилга ўтади
        sources[0].year = 2026
        sources[0].save()
        services.bump()
        self.assertEqual(services.price_table("samarqand", 2026)["258"], (Decimal("100.00"), 1))

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

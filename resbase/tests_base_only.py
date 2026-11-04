"""«Нарх манбаи»: локал + RES (олдинги усул) ёки фақат ресурс базаси (RES юкланса ҳам ишлатилмайди)."""
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from jobs.models import Batch, Job

from . import services
from .models import BaseConfig
from .tests import PASSWORD, make_job, row

TMP_MEDIA = Path(tempfile.mkdtemp(prefix="narxlash_baseonly_"))


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class BaseOnlyTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        from core.tests.conftest import build_local, build_res
        self.user = User.objects.create_user("ali@test.uz", PASSWORD, first_name="A", last_name="V")
        src = make_job(self.user, [row(11, "762", "КРАНЫ НА АВТОМОБИЛЬНОМ ХОДУ", "МАШ.-Ч", 250000, cat="J")])
        services.add_source("smeta", src.id, region="samarqand", year=2026, quarter=4)
        cfg = BaseConfig.get()
        cfg.enabled, cfg.region, cfg.year, cfg.quarter = True, "samarqand", 2026, 4
        cfg.save()
        self.client.force_login(self.user)
        self.local = build_local(TMP_MEDIA / "Локал.xlsx").read_bytes()
        self.res = build_res(TMP_MEDIA / "Ресурс.xlsx").read_bytes()

    def _upload(self):
        from core.tests.test_batch import OBJ_LOCAL, OBJ_RES
        files = [SimpleUploadedFile(Path(p).name, Path(p).read_bytes()) for p in (OBJ_LOCAL, OBJ_RES)]
        self.client.post(reverse("index"), {"files": files})
        return Batch.objects.latest("created")

    def test_choice_shown_and_saved(self):
        page = self.client.get(reverse("index"))
        self.assertContains(page, 'id="price-source"')
        self.assertContains(page, "Ресурс базасидан")
        r = self.client.post(reverse("price_source"), {"source": "base"})
        self.assertEqual(r.json(), {"ok": True, "source": "base"})
        self.assertContains(self.client.get(reverse("index")), 'value="base" checked')
        self.assertEqual(self.client.post(reverse("price_source"), {"source": "x"}).json()["source"], "res")

    def test_local_and_res_keeps_old_flow(self):
        from jobs.services import base_options
        batch = self._upload()
        self.assertTrue(any(p["res"] for p in batch.pairs))  # RES жуфтланди
        self.assertFalse(any(p["base"] for p in batch.pairs))  # база аралашмайди
        # RES топилмаган локал базага ўтмайди — нархланмайди
        self.assertTrue(all(not p["include"] for p in batch.pairs if not p["res"]))
        # «Ресурс базаси» вариантини қўлда юборса ҳам — бу режимда қабул қилинмайди
        n = next((i for i, p in enumerate(batch.pairs) if not p["res"]), None)
        if n is not None:
            self.client.post(reverse("batch_confirm", args=[batch.id]), {f"include_{n}": "1", f"res_{n}": "__base__"})
            self.assertFalse(Job.objects.filter(batch=batch, res_path="").exists())
        self.assertNotContains(self.client.get(reverse("batch", args=[batch.id])), "Ресурс базаси (RES'сиз)")
        self.client.post(reverse("index"), {"local": SimpleUploadedFile("Локал.xlsx", self.local),
                                            "res": SimpleUploadedFile("Ресурс.xlsx", self.res)})
        job = Job.objects.filter(batch=None).latest("created")
        self.assertNotEqual(job.res_path, "")
        self.assertEqual(base_options(job), {"base_prices": None, "base_label": ""})  # давр бор, лекин RES'лида база йўқ
        self.assertNotContains(self.client.get(reverse("job", args=[job.id])), "База:")

    def test_local_alone_not_from_base_unless_chosen(self):
        self.client.post(reverse("index"), {"files": [SimpleUploadedFile("Локал.xlsx", self.local)]})
        batch = Batch.objects.latest("created")
        self.assertEqual((batch.pairs[0]["res"], batch.pairs[0]["base"], batch.pairs[0]["include"]), (None, False, False))

    def test_base_only_ignores_res(self):
        self.client.post(reverse("price_source"), {"source": "base"})
        batch = self._upload()
        self.assertTrue(batch.pairs)
        self.assertTrue(all((p["res"], p["base"], p["include"]) == (None, True, True) for p in batch.pairs))
        data = {}
        for n, p in enumerate(batch.pairs):
            data.update({f"include_{n}": "1", f"res_{n}": "__base__"})
        self.client.post(reverse("batch_confirm", args=[batch.id]), data)
        jobs = list(Job.objects.filter(batch=batch))
        self.assertEqual(len(jobs), len(batch.pairs))
        self.assertTrue(all((j.res_path, j.res_name) == ("", "ресурс базаси") for j in jobs))
        statuses = {r["status"] for j in jobs for r in j.result["rows"]}
        self.assertFalse(statuses & {"EXACT", "FUZZY"}, statuses)  # RES'дан нарх олинмади

    def test_base_only_without_period_falls_back(self):
        self.client.post(reverse("price_source"), {"source": "base"})
        self.client.post(reverse("base_period"), {"region": "samarqand", "year": "2026", "quarter": ""})
        batch = self._upload()
        self.assertTrue(any(p["res"] for p in batch.pairs))  # давр тўлиқ эмас — олдинги усул

    def test_legacy_two_fields_base_only(self):
        self.client.post(reverse("price_source"), {"source": "base"})
        self.client.post(reverse("index"), {"local": SimpleUploadedFile("Локал.xlsx", self.local),
                                            "res": SimpleUploadedFile("Ресурс.xlsx", self.res)})
        job = Job.objects.latest("created")
        self.assertEqual((job.res_path, job.res_sheet, job.res_name), ("", "", "ресурс базаси"))

    def test_folder_base_only_ignores_single_res(self):
        """Папка: «ресурс базасидан» танланган бўлса, папкадаги ягона RES ҳам локалга уланмайди."""
        from unittest import mock

        from jobs import folders
        from jobs.models import Project
        self.client.post(reverse("price_source"), {"source": "base"})
        files = {"Объект/1. Блок/Локал.xlsx": self.local, "Объект/1. Блок/Ресурс.xlsx": self.res}
        data = {"files": [SimpleUploadedFile(p.split("/")[-1], b) for p, b in files.items()], "paths": list(files)}
        with mock.patch.object(folders, "start", lambda project: folders._run(project.id)):
            self.client.post(reverse("project_create"), data)
            project = Project.objects.latest("created")
            part = project.parts.get()
            self.assertTrue(part.pairs)
            self.assertTrue(all((p["res"], p["base"]) == (None, True) for p in part.pairs), part.pairs)
            self.client.post(reverse("project_price", args=[project.id]))
        jobs = list(Job.objects.filter(batch__project=project))
        self.assertTrue(jobs)
        self.assertTrue(all((j.res_path, j.res_name) == ("", "ресурс базаси") for j in jobs))

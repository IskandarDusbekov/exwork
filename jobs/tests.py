import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from core.tests.conftest import build_local, build_res

from .models import Job

TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_media_")


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class FlowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = Path(tempfile.mkdtemp(prefix="narxlash_src_"))
        cls.local = build_local(cls.src / "Локал 3.xlsx")
        cls.res = build_res(cls.src / "Ресурс 3.xlsx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src, ignore_errors=True)
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def _upload(self, **files):
        data = {k: SimpleUploadedFile(p.name, p.read_bytes()) for k, p in files.items()}
        return self.client.post(reverse("index"), data)

    def test_index_page(self):
        r = self.client.get(reverse("index"))
        self.assertContains(r, "Локал файл")

    def test_full_flow(self):
        r = self._upload(local=self.local, res=self.res)
        job = Job.objects.get()
        self.assertRedirects(r, reverse("job", args=[job.id]))
        self.assertEqual((job.local_sheet, job.res_sheet), ("202611450_БВ", "202611450_БР"))

        page = self.client.get(reverse("job", args=[job.id]))
        self.assertContains(page, "Нархлаш")

        r = self.client.post(reverse("job_run", args=[job.id]), {
            "local_sheet": job.local_sheet, "res_sheet": job.res_sheet, "fuzzy": "95"})
        self.assertRedirects(r, reverse("job", args=[job.id]))
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.DONE, job.error)
        self.assertEqual(job.result["stats"]["FUZZY"], 1)

        page = self.client.get(reverse("job", args=[job.id]))
        self.assertContains(page, "#ЗНАЧ!")           # «--» туфайли Excel'дагидек
        self.assertContains(page, "29 818")       # ишчилар нархи
        self.assertContains(page, "ТОПИЛМАДИ")

        dl = self.client.get(reverse("job_download", args=[job.id]))
        self.assertEqual(dl.status_code, 200)
        self.assertIn("narxlandi.xlsx", dl["Content-Disposition"])
        out = self.src / "dl.xlsx"
        out.write_bytes(b"".join(dl.streaming_content))
        self.assertEqual(load_workbook(out).sheetnames, ["LOG", "202611450_БВ"])

    def test_single_file_for_both(self):
        self._upload(local=self.res)
        job = Job.objects.get()
        self.assertEqual(job.local_path, job.res_path)

    def test_bad_extension(self):
        r = self.client.post(reverse("index"), {"local": SimpleUploadedFile("a.csv", b"x")})
        self.assertContains(r, "фақат .xls")
        self.assertFalse(Job.objects.exists())

    def test_wrong_sheet_shows_error(self):
        self._upload(local=self.local, res=self.res)
        job = Job.objects.get()
        self.client.post(reverse("job_run", args=[job.id]), {"local_sheet": "йўқ", "res_sheet": "", "fuzzy": "95"})
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertContains(self.client.get(reverse("job", args=[job.id])), "Нархлашда хато")

    def test_download_before_run_404(self):
        self._upload(local=self.local, res=self.res)
        job = Job.objects.get()
        self.assertEqual(self.client.get(reverse("job_download", args=[job.id])).status_code, 404)

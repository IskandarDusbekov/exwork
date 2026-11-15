"""Объект саҳифаси: битта файлни (локал ёки RES) юклаб олиш ва тузатилганини шу ўрнига юклаш."""
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from .models import Batch
from .tests import LoggedInTestCase

TMP_MEDIA = Path(tempfile.mkdtemp(prefix="narxlash_replace_"))


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class ReplaceFileTests(LoggedInTestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        from core.tests.test_batch import OBJ_LOCAL, OBJ_RES
        self.local, self.res = Path(OBJ_LOCAL), Path(OBJ_RES)
        files = [SimpleUploadedFile(p.name, p.read_bytes()) for p in (self.local, self.res)]
        self.client.post(reverse("index"), {"files": files})
        self.batch = Batch.objects.latest("created")

    def test_page_offers_download_and_replace(self):
        page = self.client.get(reverse("batch", args=[self.batch.id]))
        self.assertContains(page, reverse("batch_file", args=[self.batch.id, 1]))
        self.assertContains(page, 'data-replace="1"')
        self.assertContains(page, 'id="file-replace"')

    def test_download_returns_original(self):
        r = self.client.get(reverse("batch_file", args=[self.batch.id, 1]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), self.res.read_bytes())
        self.assertEqual(self.client.get(reverse("batch_file", args=[self.batch.id, 9])).status_code, 404)

    def test_replace_only_that_file(self):
        before = self.batch.files[0]
        new = SimpleUploadedFile("res_tuzatilgan" + self.res.suffix, self.res.read_bytes())
        r = self.client.post(reverse("batch_file_replace", args=[self.batch.id, 1]), {"file": new})
        self.assertRedirects(r, reverse("batch", args=[self.batch.id]) + "#structure", fetch_redirect_response=False)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.files[0], before)  # локал тегилмади
        self.assertEqual(self.batch.files[1]["name"], new.name)
        self.assertTrue(any(p.get("res", "") and p["res"].startswith("1:") for p in self.batch.pairs))
        # энди юклаб олинса — янги нусха
        r = self.client.get(reverse("batch_file", args=[self.batch.id, 1]))
        self.assertIn("res_tuzatilgan", r["Content-Disposition"])
        page = self.client.get(reverse("batch", args=[self.batch.id]))
        self.assertContains(page, "юкланди ва листлари қайта ўқилди")

    def test_bad_file_keeps_old(self):
        r = self.client.post(reverse("batch_file_replace", args=[self.batch.id, 1]),
                             {"file": SimpleUploadedFile("x.txt", b"hello")}, follow=True)
        self.assertContains(r, "фақат .xls, .xlsx, .xlsm")
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.files[1]["name"], self.res.name)

    def _replace(self, i, name, **extra):
        new = SimpleUploadedFile(name, (self.res if i == 1 else self.local).read_bytes())
        return self.client.post(reverse("batch_file_replace", args=[self.batch.id, i]), {"file": new, **extra})

    def test_undo_restores_previous_version(self):
        self.assertContains(self.client.get(reverse("batch", args=[self.batch.id])), "var UNDO = [0, 0];")
        self._replace(1, "v2" + self.res.suffix)
        self._replace(1, "v3" + self.res.suffix)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.files[1]["name"], "v3" + self.res.suffix)
        self.assertEqual(len(self.batch.files[1]["history"]), 2)
        self.assertContains(self.client.get(reverse("batch", args=[self.batch.id])), "var UNDO = [0, 1];")
        r = self.client.post(reverse("batch_file_undo", args=[self.batch.id, 1]))
        self.assertEqual(r.status_code, 302)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.files[1]["name"], "v2" + self.res.suffix)
        self.client.post(reverse("batch_file_undo", args=[self.batch.id, 1]))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.files[1]["name"], self.res.name)  # асл нусха
        self.assertFalse(self.batch.files[1].get("history"))
        r = self.client.post(reverse("batch_file_undo", args=[self.batch.id, 1]), follow=True)
        self.assertContains(r, "қайтариладиган олдинги нусха йўқ")
        self.assertTrue(any(p.get("res", "") and p["res"].startswith("1:") for p in self.batch.pairs))

    def test_next_redirect_only_local(self):
        r = self._replace(1, "n" + self.res.suffix, next="/folder/abc/")
        self.assertEqual(r["Location"], "/folder/abc/")
        r = self._replace(1, "n2" + self.res.suffix, next="https://evil.example/")
        self.assertTrue(r["Location"].startswith(reverse("batch", args=[self.batch.id])))

    def test_replace_after_pricing_recalcs_only_that_files_jobs(self):
        from unittest import mock
        from .services import confirm_batch, file_jobs
        jobs = confirm_batch(self.batch, {})
        self.assertTrue(jobs)
        self.batch.refresh_from_db()
        with mock.patch("jobs.recalc.start") as start:
            self._replace(1, "res_v2" + self.res.suffix)
        affected = file_jobs(self.batch, 1)
        self.assertEqual({c.args[0] for c in start.call_args_list}, {j.pk for j in affected})
        for j in affected:
            j.refresh_from_db()
            self.assertIn("res_v2", j.res_name)
            self.assertGreater(j.recalc_seq, j.recalc_done)

    def test_undo_other_user_404(self):
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create_user("other2@test.localhost", password="x")
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("batch_file_undo", args=[self.batch.id, 1])).status_code, 404)

    def test_other_user_cannot_replace(self):
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create_user("other@test.localhost", password="x")
        self.client.force_login(other)
        r = self.client.post(reverse("batch_file_replace", args=[self.batch.id, 1]),
                             {"file": SimpleUploadedFile("a.xlsx", self.res.read_bytes())})
        self.assertEqual(r.status_code, 404)

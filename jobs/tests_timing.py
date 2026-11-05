"""Юклаш вақти: сервердаги иш ва браузер ўлчаган юбориш вақти — панелдаги «Тезлик»да."""
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from .models import UploadTiming
from .tests import LoggedInTestCase

TMP_MEDIA = Path(tempfile.mkdtemp(prefix="narxlash_timing_"))


@override_settings(MEDIA_ROOT=TMP_MEDIA, NARXLASH_STAFF_2FA=False)
class UploadTimingTests(LoggedInTestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def _upload(self):
        from core.tests.test_batch import OBJ_LOCAL, OBJ_RES
        files = [SimpleUploadedFile(Path(p).name, Path(p).read_bytes()) for p in (OBJ_LOCAL, OBJ_RES)]
        return self.client.post(reverse("index"), {"files": files}, headers={"X-Requested-With": "XMLHttpRequest"})

    def test_upload_recorded_and_browser_time_added(self):
        r = self._upload()
        t = UploadTiming.objects.get(pk=r.json()["timing"])
        self.assertEqual((t.owner, t.kind, t.files, t.error), (self.user, "object", 2, ""))
        self.assertGreater(t.size, 0)
        self.assertIsNotNone(t.server_s)
        self.assertTrue(t.work_id)
        url = reverse("upload_timing")
        self.assertEqual(self.client.post(url, {"id": t.pk, "upload_s": "12.5"}).json(), {"ok": True})
        self.assertEqual(self.client.post(url, {"id": t.pk, "upload_s": "99"}).json(), {"ok": False})  # бир марта
        t.refresh_from_db()
        self.assertEqual(t.upload_s, 12.5)
        self.assertEqual(self.client.post(url, {"id": t.pk, "upload_s": "x"}).status_code, 400)

    def test_failed_upload_recorded(self):
        self.client.post(reverse("index"), {"files": [SimpleUploadedFile("a.txt", b"hi")]})
        t = UploadTiming.objects.get()
        self.assertIn("фақат .xls", t.error)

    def test_other_user_cannot_set_time(self):
        t = UploadTiming.objects.create(owner=None, kind="object", size=10)
        self.assertEqual(self.client.post(reverse("upload_timing"), {"id": t.pk, "upload_s": "1"}).json(), {"ok": False})

    def test_speed_page_shows_uploads(self):
        r = self._upload()
        self.client.post(reverse("upload_timing"), {"id": r.json()["timing"], "upload_s": "3"})
        self.user.is_staff = True
        self.user.save()
        page = self.client.get(reverse("panel:speed"))
        self.assertContains(page, "Энг узоқ юкланган 10 та")
        self.assertContains(page, "Интернет орқали юбориш")

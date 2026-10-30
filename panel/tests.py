import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core.tests.conftest import build_local, build_res
from jobs.models import Job
from jobs.services import run_job

from .models import Event

PASSWORD = "Kuchli-parol-2026"
TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_panel_media_")


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class PanelTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = Path(tempfile.mkdtemp(prefix="narxlash_panel_src_"))
        cls.local = build_local(cls.src / "Локал.xlsx")
        cls.res = build_res(cls.src / "Ресурс.xlsx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src, ignore_errors=True)
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.admin = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="Bosh", last_name="Admin")
        self.viewer = User.objects.create_user("viewer@test.uz", PASSWORD, first_name="Kuz", last_name="Atuvchi",
                                               is_staff=True)
        self.ali = User.objects.create_user("ali@test.uz", PASSWORD, first_name="Ali", last_name="Valiyev")
        self.client.force_login(self.ali)
        self.client.post(reverse("index"), {"local": SimpleUploadedFile(self.local.name, self.local.read_bytes()),
                                            "res": SimpleUploadedFile(self.res.name, self.res.read_bytes())})
        self.job = Job.objects.get()

    def _pages(self):
        return [reverse("panel:overview"), reverse("panel:users"), reverse("panel:user", args=[self.ali.pk]),
                reverse("panel:work", args=["smeta", self.job.id]), reverse("panel:processes"),
                reverse("panel:files"), reverse("panel:log")]

    def test_regular_user_gets_404_everywhere(self):
        for url in self._pages():
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post(reverse("panel:user_toggle", args=[self.admin.pk, "is_active"])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("panel:overview")).status_code, 302)  # аввал кириш

    def test_admin_sees_all_pages(self):
        self.client.force_login(self.admin)
        for url in self._pages():
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        r = self.client.get(reverse("panel:user", args=[self.ali.pk]))
        self.assertContains(r, "ali@test.uz")
        self.assertContains(r, self.local.name)  # юклаган файли
        self.assertContains(self.client.get(reverse("panel:users")), "Ali Valiyev")
        self.assertContains(self.client.get(reverse("panel:log")), "Юклади")

    def test_upload_and_login_are_logged(self):
        self.assertTrue(Event.objects.filter(kind=Event.Kind.UPLOAD, actor=self.ali, work_id=self.job.id).exists())
        self.client.logout()
        self.client.post(reverse("login"), {"email": "ali@test.uz", "password": "xato"})
        self.client.post(reverse("login"), {"email": "ali@test.uz", "password": PASSWORD})
        kinds = list(Event.objects.values_list("kind", flat=True))
        self.assertIn(Event.Kind.LOGIN_FAILED, kinds)
        self.assertIn(Event.Kind.LOGIN, kinds)

    def test_staff_viewer_cannot_act_or_download(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("panel:users")).status_code, 200)
        self.assertNotContains(self.client.get(reverse("panel:user", args=[self.ali.pk])), "Блоклаш")
        self.assertEqual(self.client.post(reverse("panel:user_toggle", args=[self.ali.pk, "is_active"])).status_code, 403)
        self.assertEqual(self.client.get(reverse("panel:work_file", args=["smeta", self.job.id, 0])).status_code, 403)
        self.ali.refresh_from_db()
        self.assertTrue(self.ali.is_active)

    def test_block_user_logs_them_out_and_is_audited(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("panel:user_toggle", args=[self.ali.pk, "is_active"]))
        self.assertRedirects(r, reverse("panel:user", args=[self.ali.pk]))
        self.ali.refresh_from_db()
        self.assertFalse(self.ali.is_active)
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_USER, actor=self.admin, target=self.ali).exists())
        other = self.client_class()
        other.force_login(self.ali)  # блокланган — сессия бўлса ҳам ичкарига кирмайди
        self.assertEqual(other.get(reverse("dashboard")).status_code, 302)

    def test_cannot_change_self_or_superuser(self):
        self.client.force_login(self.admin)
        for pk in (self.admin.pk,):
            self.assertEqual(self.client.post(reverse("panel:user_toggle", args=[pk, "is_active"])).status_code, 403)
        self.assertEqual(self.client.post(reverse("panel:user_toggle", args=[self.ali.pk, "is_superuser"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("panel:user_toggle", args=[self.ali.pk, "is_active"])).status_code, 405)

    def test_admin_download_original_and_result_audited(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("panel:work_result", args=["smeta", self.job.id])).status_code, 404)
        run_job(self.job, None, None, self.job.fuzzy, "smart")  # натижа фақат ҳисоблангандан кейин
        self.assertEqual(self.job.status, Job.Status.DONE)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("panel:work_file", args=["smeta", self.job.id, 0]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), self.local.read_bytes())
        self.assertEqual(self.client.get(reverse("panel:work_file", args=["smeta", self.job.id, 9])).status_code, 404)
        self.assertEqual(self.client.get(reverse("panel:work_result", args=["smeta", self.job.id])).status_code, 200)
        self.assertEqual(Event.objects.filter(kind=Event.Kind.ADMIN_DOWNLOAD, target=self.ali).count(), 2)

    def test_unknown_kind_404(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("panel:work", args=["xxx", self.job.id])).status_code, 404)

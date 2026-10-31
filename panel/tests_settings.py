"""Панел: созламалар (базада, .env стандартини босади), фойдаланувчи диск чегараси, захира нусха."""
import shutil
import tempfile
from pathlib import Path

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import User

from . import backup, conf
from .models import Event, SiteSetting

PASSWORD = "Kuchli-parol-2026"
TMP = tempfile.mkdtemp(prefix="narxlash_settings_")


@override_settings(MEDIA_ROOT=f"{TMP}/media", NARXLASH_BACKUP_DIR=f"{TMP}/backups")
class SettingsTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.boss = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="B", last_name="A")
        self.viewer = User.objects.create_user("v@test.uz", PASSWORD, first_name="V", last_name="V", is_staff=True)
        self.ali = User.objects.create_user("ali@test.uz", PASSWORD, first_name="Ali", last_name="V")

    def _form(self, **kw):
        data = {f.key: conf.shown(f.key) for f in conf.FIELDS if f.kind != "bool"}
        data.update({f.key: "1" for f in conf.FIELDS if f.kind == "bool" and conf.shown(f.key)})
        data.update(kw)
        return data

    def test_change_setting_takes_effect_and_is_logged(self):
        self.client.force_login(self.boss)
        r = self.client.post(reverse("panel:settings_save"), self._form(uploads_per_hour="1", signup="closed"))
        self.assertRedirects(r, reverse("panel:settings"), fetch_redirect_response=False)
        self.assertEqual(conf.get("uploads_per_hour"), 1)
        self.assertEqual(conf.get("signup"), "closed")
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_SETTINGS, detail__contains="Бир соатдаги юклашлар").exists())
        # рўйхатдан ўтиш ёпилди — дарҳол
        self.client.logout()
        self.assertEqual(self.client.post(reverse("register"), {}).status_code, 403)
        # стандартга қайтарилса — базадан ўчади
        self.client.force_login(self.boss)
        self.client.post(reverse("panel:settings_save"), self._form(uploads_per_hour=str(conf.default("uploads_per_hour")), signup="open"))
        self.assertFalse(SiteSetting.objects.filter(key="uploads_per_hour").exists())

    def test_bad_value_rejected_and_viewer_cannot_change(self):
        self.client.force_login(self.boss)
        self.client.post(reverse("panel:settings_save"), self._form(max_upload_mb="abc"))
        self.assertFalse(SiteSetting.objects.exists())
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("panel:settings")).status_code, 200)
        self.assertEqual(self.client.post(reverse("panel:settings_save"), self._form(uploads_per_hour="5")).status_code, 403)
        self.client.force_login(self.ali)
        self.assertEqual(self.client.get(reverse("panel:settings")).status_code, 404)

    def test_texnazor_upload_size_from_panel(self):
        SiteSetting.objects.create(key="texnazor_max_upload_mb", value=1)
        cache.clear()
        self.client.force_login(self.ali)
        r = self.client.post(reverse("texnazor:create"), {"ofyorta": SimpleUploadedFile("o.xlsx", b"0" * (1024 * 1024 + 10))})
        self.assertContains(r, "кўпи билан 1 МБ")

    def test_user_quota_override(self):
        self.client.force_login(self.boss)
        self.client.post(reverse("panel:user_quota", args=[self.ali.pk]), {"quota_mb": "1"})
        self.ali.refresh_from_db()
        self.assertEqual(self.ali.quota_mb, 1)
        self.assertEqual(conf.user_quota(self.ali), 1024 * 1024)
        Path(f"{TMP}/media/texnazor/x").mkdir(parents=True, exist_ok=True)
        from texnazor.models import SiteObject
        o = SiteObject.objects.create(owner=self.ali, name="x", source_name="x.xlsx", source_path="src/x.xlsx")
        o.dir.mkdir(parents=True, exist_ok=True)
        (o.dir / "big.bin").write_bytes(b"0" * (1024 * 1024 + 1))
        self.client.force_login(self.ali)
        r = self.client.post(reverse("texnazor:create"), {"ofyorta": SimpleUploadedFile("o.xlsx", b"x")})
        self.assertContains(r, "Дискдаги жойингиз тўлди")
        self.client.force_login(self.boss)
        self.client.post(reverse("panel:user_quota", args=[self.ali.pk]), {"quota_mb": ""})
        self.ali.refresh_from_db()
        self.assertIsNone(self.ali.quota_mb)
        self.assertContains(self.client.get(reverse("panel:user", args=[self.ali.pk])), "Диск чегараси")



@override_settings(NARXLASH_BACKUP_DIR=f"{TMP}/backups")
class BackupTests(TransactionTestCase):
    """Нусха — тест транзакциясидан ташқарида (SQLite очиқ ёзув транзакциясини кутиб қолади)."""

    def setUp(self):
        self.boss = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="B", last_name="A")
        self.viewer = User.objects.create_user("v@test.uz", PASSWORD, first_name="V", last_name="V", is_staff=True)

    def test_backup_make_list_and_download_needs_password(self):
        name = backup.make()  # testda SQLite — backup API
        self.assertTrue(backup.NAME_RE.match(name))
        self.client.force_login(self.boss)
        r = self.client.get(reverse("panel:backups"))
        self.assertContains(r, name)
        url = reverse("panel:backup_download", args=[name])
        r = self.client.post(url, {"code": "notogri"})
        self.assertRedirects(r, reverse("panel:backups"), fetch_redirect_response=False)
        r = self.client.post(url, {"code": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_DOWNLOAD, detail__contains=name).exists())
        self.assertEqual(self.client.post(reverse("panel:backup_download", args=["..%2F..%2Fdb.sqlite3"]), {"code": PASSWORD}).status_code, 404)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(url, {"code": PASSWORD}).status_code, 403)

"""Сессиялар, IP блок, текширув режими, жонли блок ва статистика."""
import shutil
import tempfile
from pathlib import Path

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.tests.conftest import build_local, build_res
from jobs.models import Batch, Job, Project

from .models import BlockedIP, Event, LoginSession
from .sessions import parse_ua

PASSWORD = "Kuchli-parol-2026"
PHONE_UA = ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/139.0 Mobile Safari/537.36")
TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_sec_media_")


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class SecurityPanelTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = Path(tempfile.mkdtemp(prefix="narxlash_sec_src_"))
        cls.local = build_local(cls.src / "Локал.xlsx")
        cls.res = build_res(cls.src / "Ресурс.xlsx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src, ignore_errors=True)
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="Bosh", last_name="Admin")
        self.ali = User.objects.create_user("ali@test.uz", PASSWORD, first_name="Ali", last_name="Valiyev")
        self.ali_client = Client(HTTP_USER_AGENT=PHONE_UA, REMOTE_ADDR="10.0.0.7")
        self.ali_client.post(reverse("login"), {"email": "ali@test.uz", "password": PASSWORD})

    def _ali_job(self):
        self.ali_client.post(reverse("index"), {"local": SimpleUploadedFile(self.local.name, self.local.read_bytes()),
                                                "res": SimpleUploadedFile(self.res.name, self.res.read_bytes())})
        return Job.objects.get(owner=self.ali)

    def test_login_records_ip_and_device(self):
        s = LoginSession.objects.get(user=self.ali)
        self.assertEqual(s.ip, "10.0.0.7")
        self.assertEqual((s.device, s.os), ("phone", "Android 14"))
        self.assertTrue(Event.objects.filter(kind=Event.Kind.LOGIN, actor=self.ali, device__contains="телефон").exists())
        self.client.force_login(self.admin)
        r = self.client.get(reverse("panel:user", args=[self.ali.pk]))
        self.assertContains(r, "10.0.0.7")
        self.assertContains(r, "Android 14")
        self.assertContains(self.client.get(reverse("panel:users")), "10.0.0.7")

    def test_logout_ends_session(self):
        self.ali_client.post(reverse("logout"))
        self.assertEqual(LoginSession.objects.get(user=self.ali).end_reason, "logout")

    def test_admin_revokes_session(self):
        self.client.force_login(self.admin)
        s = LoginSession.objects.get(user=self.ali)
        self.client.post(reverse("panel:session_revoke", args=[self.ali.pk, s.pk]))
        self.assertEqual(self.ali_client.get(reverse("dashboard")).status_code, 302)  # чиқариб юборилди
        s.refresh_from_db()
        self.assertEqual(s.end_reason, "revoked")
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_SESSION, target=self.ali).exists())

    def test_block_user_from_list_kills_sessions_and_returns_to_list(self):
        self.client.force_login(self.admin)
        here = reverse("panel:users") + "?q=ali"
        r = self.client.post(reverse("panel:user_toggle", args=[self.ali.pk, "is_active"]), {"next": here})
        self.assertRedirects(r, here)
        self.assertEqual(LoginSession.objects.get(user=self.ali).end_reason, "blocked")
        r = self.client.post(reverse("panel:user_toggle", args=[self.ali.pk, "is_active"]),
                             {"next": "https://evil.example/"})
        self.assertRedirects(r, reverse("panel:user", args=[self.ali.pk]))  # бегона манзилга эмас

    def test_ip_block(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("panel:ip_block"), {"ip": "10.0.0.7", "reason": "test"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(BlockedIP.objects.filter(ip="10.0.0.7").exists())
        self.assertEqual(self.ali_client.get(reverse("login")).status_code, 403)  # кириш саҳифаси ҳам
        self.assertTrue(Event.objects.filter(kind=Event.Kind.BLOCKED_IP, ip="10.0.0.7").exists())
        self.assertEqual(Client(REMOTE_ADDR="10.0.0.8").get(reverse("login")).status_code, 200)
        self.assertEqual(LoginSession.objects.get(user=self.ali).end_reason, "blocked")  # шу IP'даги сессия ёпилди
        self.client.post(reverse("panel:ip_block"), {"ip": "10.0.0.7", "action": "unblock"})
        self.assertEqual(self.ali_client.get(reverse("login")).status_code, 200)  # очилди, лекин қайта кириш керак

    def test_cannot_block_own_ip_or_garbage(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("panel:ip_block"), {"ip": "127.0.0.1"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("panel:ip_block"), {"ip": "not-an-ip"}).status_code, 404)
        self.assertFalse(BlockedIP.objects.exists())

    def test_superuser_views_users_work_readonly(self):
        job = self._ali_job()
        self.client.force_login(self.admin)
        r = self.client.get(reverse("job", args=[job.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Текширув режими")
        self.assertEqual(Event.objects.filter(kind=Event.Kind.ADMIN_VIEW, target=self.ali).count(), 1)
        self.client.get(reverse("job", args=[job.id]))  # такрор — журналга яна ёзилмайди
        self.assertEqual(Event.objects.filter(kind=Event.Kind.ADMIN_VIEW, target=self.ali).count(), 1)
        # ўзгартириш мумкин эмас
        self.assertEqual(self.client.post(reverse("job_run", args=[job.id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("work_delete", args=["smeta", job.id])).status_code, 404)
        self.assertTrue(Job.objects.filter(id=job.id).exists())
        # оддий фойдаланувчи бошқанинг ишини кўролмайди
        other = User.objects.create_user("vali@test.uz", PASSWORD, first_name="V", last_name="V")
        c = Client()
        c.force_login(other)
        self.assertEqual(c.get(reverse("job", args=[job.id])).status_code, 404)

    def test_live_and_stats_pages(self):
        pr = Project.objects.create(owner=self.ali, name="Жонли папка", status=Project.Status.PROCESSING,
                                    heartbeat=timezone.now(), run_started=timezone.now())
        Batch.objects.create(owner=self.ali, project=pr, folder="A", name="A", stage=Batch.Stage.PRICING,
                             step="1/3 смета нархланяпти: Блок А", stage_since=timezone.now())
        Batch.objects.create(owner=self.ali, project=pr, folder="B", name="B", stage=Batch.Stage.DONE,
                             durations={"converting": 4, "pricing": 30})
        Batch.objects.create(owner=self.ali, project=pr, folder="C", name="C", stage=Batch.Stage.QUEUED)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("panel:live"))
        self.assertContains(r, "Жонли папка")
        self.assertContains(r, "1/3 смета нархланяпти")
        self.assertContains(r, "Онлайн")
        for days in ("7", "30", "90", "abc"):
            self.assertEqual(self.client.get(reverse("panel:stats"), {"days": days}).status_code, 200)
        self.assertEqual(self.client.get(reverse("panel:processes")).status_code, 200)
        self.assertEqual(self.ali_client.get(reverse("panel:live")).status_code, 404)


class UserAgentTests(TestCase):
    def test_parse(self):
        self.assertEqual(parse_ua(PHONE_UA), {"browser": "Chrome 139", "os": "Android 14", "device": "phone"})
        edge = parse_ua("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/140.0 Safari/537.36 Edg/140.0")
        self.assertEqual((edge["browser"], edge["device"]), ("Edge 140", "computer"))
        self.assertEqual(parse_ua("curl/8.4")["device"], "bot")


class SpeedPageTests(TestCase):
    def test_speed_page(self):
        from jobs.models import FileTiming
        admin = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="B", last_name="A")
        ali = User.objects.create_user("ali@test.uz", PASSWORD, first_name="Ali", last_name="V")
        FileTiming.objects.create(owner=ali, name="Секин смета.xls", ext=".xls", size=3 * 1024 * 1024,
                                  method="libreoffice", convert_s=12.5, read_s=0.4, analyze_s=2.1)
        FileTiming.objects.create(owner=ali, name="Бузуқ.xls", ext=".xls", size=1000, error="ConversionError: x")
        c = Client()
        c.force_login(ali)
        self.assertEqual(c.get(reverse("panel:speed")).status_code, 404)
        c.force_login(admin)
        r = c.get(reverse("panel:speed"), {"days": "30"})
        self.assertContains(r, "Секин смета.xls")
        self.assertContains(r, "15 с")       # жами 12,5 + 0,4 + 2,1
        self.assertContains(r, "ConversionError")
        self.assertContains(r, "LibreOffice")

    def test_dur_filter(self):
        from jobs.templatetags.narx import dur
        self.assertEqual([dur(None), dur(0.42), dur(9.96), dur(45), dur(200), dur(4000)],
                         ["—", "0,4 с", "10,0 с", "45 с", "3 дақ 20 с", "1 соат 6 дақ"])

import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import mail
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from core.tests.conftest import build_local, build_res
from jobs.models import Job

from .models import AuthAttempt, User
from .throttle import LOGIN_PER_EMAIL, client_ip

PASSWORD = "Kuchli-parol-2026"


class RegisterTests(TestCase):
    def _post(self, **over):
        data = {"first_name": "Ali", "last_name": "Valiyev", "email": "Ali@Mail.UZ",
                "password1": PASSWORD, "password2": PASSWORD, **over}
        return self.client.post(reverse("register"), data)

    def test_register_creates_user_and_logs_in(self):
        r = self._post()
        self.assertRedirects(r, reverse("index"))
        u = User.objects.get()
        self.assertEqual(u.email, "ali@mail.uz")  # кичик ҳарфда
        self.assertTrue(u.password.startswith("argon2"))
        self.assertFalse(u.is_staff or u.is_superuser)
        self.assertEqual(self.client.get(reverse("index")).status_code, 200)

    def test_duplicate_email_case_insensitive(self):
        User.objects.create_user("ali@mail.uz", PASSWORD, first_name="A", last_name="B")
        r = self._post(email="ALI@mail.uz")
        self.assertContains(r, "Бу email билан ҳисоб бор")
        self.assertEqual(User.objects.count(), 1)

    def test_weak_passwords_rejected(self):
        for pw, msg in (("12345", "камида 10"), ("1234567890123", "фақат рақам"), ("password123", "кенг тарқалган")):
            r = self._post(password1=pw, password2=pw)
            self.assertContains(r, msg)
        self.assertContains(self._post(password2="boshqa-parol-1"), "бир хил эмас")
        self.assertFalse(User.objects.exists())

    def test_cannot_set_staff_via_form(self):
        self._post(is_staff="1", is_superuser="1")
        self.assertFalse(User.objects.get().is_superuser)


class LoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ali@mail.uz", PASSWORD, first_name="Ali", last_name="Valiyev")

    def test_pages_require_login(self):
        url = reverse("dashboard")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")
        self.assertEqual(self.client.get(reverse("landing")).status_code, 200)  # «Тизим ҳақида» очиқ

    def test_home_is_landing_for_guests(self):
        """Биринчи келган одам бош саҳифада «Тизим ҳақида»ни кўради; юклаш (POST) — фақат киргандан кейин."""
        r = self.client.get(reverse("index"))
        self.assertTemplateUsed(r, "jobs/landing.html")
        self.assertContains(r, reverse("login"))
        url = reverse("index")
        self.assertRedirects(self.client.post(url), f"{reverse('login')}?next={url}", fetch_redirect_response=False)
        self.client.force_login(self.user)
        self.assertTemplateUsed(self.client.get(url), "jobs/index.html")

    def test_login_logout(self):
        r = self.client.post(reverse("login"), {"email": "ALI@mail.uz", "password": PASSWORD, "next": "/dashboard/"})
        self.assertRedirects(r, reverse("dashboard"))
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)  # чиқиш фақат POST
        self.client.post(reverse("logout"))
        self.assertTemplateUsed(self.client.get(reverse("index")), "jobs/landing.html")  # чиққач — бош саҳифа

    def test_open_redirect_blocked(self):
        r = self.client.post(reverse("login"), {"email": "ali@mail.uz", "password": PASSWORD,
                                                "next": "https://evil.example/"})
        self.assertRedirects(r, reverse("index"))

    def test_wrong_password_generic_message(self):
        r1 = self.client.post(reverse("login"), {"email": "ali@mail.uz", "password": "xato"})
        r2 = self.client.post(reverse("login"), {"email": "yoq@mail.uz", "password": "xato"})
        for r in (r1, r2):
            self.assertContains(r, "Email ёки пароль нотўғри")

    def test_inactive_user_cannot_login(self):
        self.user.is_active = False
        self.user.save()
        r = self.client.post(reverse("login"), {"email": "ali@mail.uz", "password": PASSWORD})
        self.assertContains(r, "Email ёки пароль нотўғри")

    def test_brute_force_lockout(self):
        for _ in range(LOGIN_PER_EMAIL):
            self.client.post(reverse("login"), {"email": "ali@mail.uz", "password": "xato"})
        r = self.client.post(reverse("login"), {"email": "ali@mail.uz", "password": PASSWORD})
        self.assertContains(r, "Жуда кўп уриниш")  # тўғри пароль ҳам блок вақтида ўтмайди
        self.assertNotIn("_auth_user_id", self.client.session)
        AuthAttempt.objects.all().delete()
        r = self.client.post(reverse("login"), {"email": "ali@mail.uz", "password": PASSWORD})
        self.assertRedirects(r, reverse("index"))

    def test_session_rotated_on_login(self):
        self.client.get(reverse("login"))
        s = self.client.session
        s["x"] = 1
        s.save()
        before = s.session_key
        self.client.post(reverse("login"), {"email": "ali@mail.uz", "password": PASSWORD})
        self.assertNotEqual(self.client.session.session_key, before)


TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_auth_media_")


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class IsolationTests(TestCase):
    """Бир смета­чи бошқасининг ишини кўролмайди, ўзгартиролмайди, юклаб ололмайди."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = Path(tempfile.mkdtemp(prefix="narxlash_auth_src_"))
        cls.local = build_local(cls.src / "Локал.xlsx")
        cls.res = build_res(cls.src / "Ресурс.xlsx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src, ignore_errors=True)
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def test_other_user_gets_404(self):
        a = User.objects.create_user("a@mail.uz", PASSWORD, first_name="A", last_name="A")
        b = User.objects.create_user("b@mail.uz", PASSWORD, first_name="B", last_name="B")
        self.client.force_login(a)
        self.client.post(reverse("index"), {"local": SimpleUploadedFile(self.local.name, self.local.read_bytes()),
                                            "res": SimpleUploadedFile(self.res.name, self.res.read_bytes())})
        job = Job.objects.get()
        self.assertEqual(job.owner, a)
        self.assertEqual(self.client.get(reverse("job", args=[job.id])).status_code, 200)

        self.client.force_login(b)
        for name in ("job", "job_download", "job_sheet"):
            self.assertEqual(self.client.get(reverse(name, args=[job.id])).status_code, 404, name)
        self.assertEqual(self.client.post(reverse("job_run", args=[job.id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("work_delete", args=["smeta", job.id])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("dashboard")), job.local_name)
        self.assertTrue(Job.objects.filter(id=job.id).exists())


class ClientIPTests(SimpleTestCase):
    def _ip(self, xff=None, remote="10.0.0.1"):
        meta = {"REMOTE_ADDR": remote}
        if xff is not None:
            meta["HTTP_X_FORWARDED_FOR"] = xff
        return client_ip(RequestFactory().get("/", **meta))

    @override_settings(NARXLASH_TRUST_PROXY=0)
    def test_header_ignored_without_proxy(self):
        self.assertEqual(self._ip("1.2.3.4"), "10.0.0.1")

    @override_settings(NARXLASH_TRUST_PROXY=1)
    def test_one_proxy_takes_last(self):
        self.assertEqual(self._ip("6.6.6.6, 1.2.3.4"), "1.2.3.4")  # бошидагини мижоз ўзи ёзган
        self.assertEqual(self._ip(), "10.0.0.1")

    @override_settings(NARXLASH_TRUST_PROXY=2)
    def test_two_proxies(self):
        self.assertEqual(self._ip("6.6.6.6, 1.2.3.4, 104.16.0.1"), "1.2.3.4")  # Cloudflare + nginx
        self.assertEqual(self._ip("1.2.3.4"), "10.0.0.1")  # прокси айланиб ўтилган

    @override_settings(NARXLASH_TRUST_PROXY=1)
    def test_garbage_falls_back(self):
        self.assertEqual(self._ip("not-an-ip"), "10.0.0.1")
        self.assertEqual(self._ip("2001:db8::1"), "2001:db8::1")


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ali@mail.uz", PASSWORD, first_name="Ali", last_name="Valiyev")

    def _link(self):
        self.assertEqual(len(mail.outbox), 1)
        return next(w for w in mail.outbox[0].body.split() if "/password/reset/" in w)

    def test_login_page_links_to_reset(self):
        self.assertContains(self.client.get(reverse("login")), reverse("reset_request"))

    def test_unknown_email_same_answer_no_mail(self):
        known = self.client.post(reverse("reset_request"), {"email": "ALI@mail.uz"})
        unknown = self.client.post(reverse("reset_request"), {"email": "yoq@mail.uz"})
        self.assertContains(known, "ҳаволаси юборилди")
        self.assertContains(unknown, "ҳаволаси юборилди")  # ҳисоб бор-йўқлиги ошкор бўлмайди
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ali@mail.uz"])

    def test_reset_sets_password_once(self):
        other = self.client_class()
        other.force_login(self.user)  # бошқа қурилма
        self.client.post(reverse("reset_request"), {"email": "ali@mail.uz"})
        link = self._link()
        r = self.client.get(link)
        self.assertNotIn(link.rsplit("/", 2)[-2], r["Location"])  # токен манзилда қолмайди (сессияга олинди)
        page = self.client.get(r["Location"])
        self.assertContains(page, "Янги пароль")
        r = self.client.post(r["Location"], {"password1": "Yangi-kuchli-parol-7", "password2": "Yangi-kuchli-parol-7"})
        self.assertRedirects(r, reverse("login"), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Yangi-kuchli-parol-7"))
        self.assertEqual(other.get(reverse("dashboard")).status_code, 302)  # бошқа қурилмадан чиқарилди
        self.assertContains(self.client.get(link), "Ҳавола эскирган")  # иккинчи марта ишламайди

    def test_weak_or_mismatched_password_rejected(self):
        self.client.post(reverse("reset_request"), {"email": "ali@mail.uz"})
        url = self.client.get(self._link())["Location"]
        r = self.client.post(url, {"password1": "12345678901", "password2": "12345678901"})
        self.assertContains(r, "рақамлардан")
        r = self.client.post(url, {"password1": "Yangi-kuchli-parol-7", "password2": "boshqa-parol-77"})
        self.assertContains(r, "бир хил эмас")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_bad_token(self):
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.assertContains(self.client.get(reverse("reset_confirm", args=[uid, "set-password"])), "Ҳавола эскирган")
        self.assertContains(self.client.get(reverse("reset_confirm", args=[uid, "abc-123"])), "Ҳавола эскирган")

    def test_reset_requests_throttled(self):
        from .throttle import RESET_PER_EMAIL
        for _ in range(RESET_PER_EMAIL):
            self.client.post(reverse("reset_request"), {"email": "ali@mail.uz"})
        r = self.client.post(reverse("reset_request"), {"email": "ali@mail.uz"})
        self.assertContains(r, "Жуда кўп уриниш")
        self.assertEqual(len(mail.outbox), RESET_PER_EMAIL)

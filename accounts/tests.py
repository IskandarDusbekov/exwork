import shutil
import tempfile
from pathlib import Path

import pytest

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


# ======== икки босқичли кириш ========
@pytest.mark.staff_2fa
class TwoFactorTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("boss@mail.uz", PASSWORD, first_name="B", last_name="A")
        self.panel = reverse("panel:overview")

    def _code(self, secret, shift=0):
        import time
        from . import totp
        return totp.code_at(secret.replace(" ", ""), int(time.time() // totp.STEP) + shift)

    def test_staff_must_enable_then_code_is_required(self):
        from panel.models import Event
        from .models import TwoFactor
        # parol bilan kirdi, lekin panel — avval ikki bosqichli kirishni yoqish
        r = self.client.post(reverse("login"), {"email": "boss@mail.uz", "password": PASSWORD})
        self.assertRedirects(r, reverse("index"), fetch_redirect_response=False)
        r = self.client.get(self.panel)
        self.assertRedirects(r, f"{reverse('twofa_setup')}?next={self.panel}", fetch_redirect_response=False)
        page = self.client.get(r.url)
        secret = page.context["secret"]
        self.assertContains(page, "Икки босқичли кириш")
        r = self.client.post(reverse("twofa_setup"), {"code": "000000", "next": self.panel})
        self.assertContains(r, "Код нотўғри")
        r = self.client.post(reverse("twofa_setup"), {"code": self._code(secret), "next": self.panel})
        self.assertRedirects(r, self.panel, fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.panel).status_code, 200)
        self.assertTrue(Event.objects.filter(kind=Event.Kind.TWOFA_ON, actor=self.boss).exists())

        # keyingi kirish: parol to'g'ri — hali kirmagan, kod so'raladi
        self.client.logout()
        r = self.client.post(reverse("login"), {"email": "boss@mail.uz", "password": PASSWORD, "next": self.panel})
        self.assertRedirects(r, reverse("login_code"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        r = self.client.post(reverse("login_code"), {"code": "123456"})
        self.assertContains(r, "Код нотўғри")
        self.assertTrue(Event.objects.filter(kind=Event.Kind.TWOFA_FAILED, target=self.boss).exists())
        tf = TwoFactor.objects.get()
        used = self._code(tf.secret)  # o'rnatishda ishlatilgan kod qayta o'tmaydi
        r = self.client.post(reverse("login_code"), {"code": used})
        self.assertNotIn("_auth_user_id", self.client.session)
        r = self.client.post(reverse("login_code"), {"code": self._code(tf.secret, 1)})
        self.assertRedirects(r, self.panel, fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.panel).status_code, 200)

    def test_too_many_wrong_codes_restart_login(self):
        from . import totp
        from .models import TwoFactor
        TwoFactor.objects.create(user=self.boss, secret=totp.new_secret())
        self.client.post(reverse("login"), {"email": "boss@mail.uz", "password": PASSWORD})
        for _ in range(4):
            self.client.post(reverse("login_code"), {"code": "111111"})
        r = self.client.post(reverse("login_code"), {"code": "111111"})
        self.assertRedirects(r, reverse("login"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("login_code")), reverse("login"), fetch_redirect_response=False)

    def test_admin_login_form_does_not_skip_code(self):
        """Django админнинг ўз кириш формаси орқали кирилса ҳам — панел / админ код сўрайди."""
        from . import totp
        from .models import TwoFactor
        TwoFactor.objects.create(user=self.boss, secret=totp.new_secret())
        self.client.force_login(self.boss)  # admin login formasi kabi: parol bilan kirgan, kod kiritilmagan
        self.assertRedirects(self.client.get(reverse("admin:index")), reverse("login_code"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(self.panel), reverse("login_code"), fetch_redirect_response=False)

    def test_ordinary_user_unaffected_and_panel_still_hidden(self):
        u = User.objects.create_user("ali@mail.uz", PASSWORD, first_name="Ali", last_name="V")
        r = self.client.post(reverse("login"), {"email": "ali@mail.uz", "password": PASSWORD})
        self.assertRedirects(r, reverse("index"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(self.panel).status_code, 404)
        self.assertEqual(u.pk, int(self.client.session["_auth_user_id"]))

    def test_reset_command(self):
        from django.core.management import call_command
        from . import totp
        from .models import TwoFactor
        TwoFactor.objects.create(user=self.boss, secret=totp.new_secret())
        call_command("reset_2fa", "BOSS@mail.uz", stdout=open(__import__("os").devnull, "w", encoding="utf8"))
        self.assertFalse(TwoFactor.objects.exists())


def test_totp_rfc6238_vector():
    import base64
    from . import totp
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(secret, 59 // 30) == "287082"  # RFC 6238 (SHA1, T=59) oxirgi 6 raqami
    assert totp.verify(secret, "287 082", now=59) == 1
    assert totp.verify(secret, "287082", last_step=1, now=59) is None

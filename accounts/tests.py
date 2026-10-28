import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.tests.conftest import build_local, build_res
from jobs.models import Job

from .models import AuthAttempt, User
from .throttle import LOGIN_PER_EMAIL

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
        for url in (reverse("index"), reverse("dashboard")):
            r = self.client.get(url)
            self.assertRedirects(r, f"{reverse('login')}?next={url}")
        self.assertEqual(self.client.get(reverse("landing")).status_code, 200)  # «Тизим ҳақида» очиқ

    def test_login_logout(self):
        r = self.client.post(reverse("login"), {"email": "ALI@mail.uz", "password": PASSWORD, "next": "/dashboard/"})
        self.assertRedirects(r, reverse("dashboard"))
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)  # чиқиш фақат POST
        self.client.post(reverse("logout"))
        self.assertEqual(self.client.get(reverse("index")).status_code, 302)

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

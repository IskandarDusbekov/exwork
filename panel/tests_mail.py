"""Панел «Почта»: SMTP созламалари (пароль шифрланган), backend панелдагини ишлатади, синов хати, кўриниш."""
from unittest import mock

from django.core import mail as outbox
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User

from . import mail
from .models import Event, SiteSetting

PASSWORD = "Kuchli-parol-2026"
FORM = {"host": "smtp.gmail.com", "port": "587", "security": "tls", "username": "noreply@mhisobot.uz",
        "password": "abcd efgh ijkl mnop", "strip_spaces": "1", "from_email": "", "from_name": "mhisobot"}


@override_settings(NARXLASH_STAFF_2FA=False)
class MailPanelTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.staff = User.objects.create_user("staff@test.uz", PASSWORD, first_name="S", last_name="T", is_staff=True)

    def test_encrypt_roundtrip_and_tamper(self):
        t = mail.encrypt("сир-пароль 123")
        self.assertNotIn("123", t)
        self.assertEqual(mail.decrypt(t), "сир-пароль 123")
        self.assertEqual(mail.decrypt(t[:-4] + "AAAA"), "")
        with override_settings(SECRET_KEY="boshqa-kalit-" + "x" * 40):
            self.assertEqual(mail.decrypt(t), "")

    def test_save_encrypts_and_never_shows_password(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("panel:mail_save"), FORM, follow=True)
        self.assertContains(r, "Сақланди")
        row = SiteSetting.objects.get(key="smtp")
        self.assertNotIn("abcd", str(row.value))
        cfg = mail.config()
        self.assertEqual((cfg["source"], cfg["host"], cfg["password"]), ("panel", "smtp.gmail.com", "abcdefghijklmnop"))
        self.assertEqual(mail.from_address(), "mhisobot <noreply@mhisobot.uz>")
        self.assertNotContains(r, "abcdefghijklmnop")
        self.assertContains(r, "сақланган — ўзгартириш учун")
        # пароль бўш юборилса — эскиси қолади
        self.client.post(reverse("panel:mail_save"), {**FORM, "password": "", "port": "465", "security": "ssl"})
        cfg = mail.config()
        self.assertEqual((cfg["port"], cfg["security"], cfg["password"]), (465, "ssl", "abcdefghijklmnop"))
        self.assertTrue(Event.objects.filter(detail__startswith="Почта: smtp.gmail.com:465").exists())
        # .env га қайтиш
        self.client.post(reverse("panel:mail_save"), {"action": "env"})
        self.assertFalse(SiteSetting.objects.filter(key="smtp").exists())

    def test_bad_values(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("panel:mail_save"), {**FORM, "host": "smtp gmail"}, follow=True)
        self.assertContains(r, "Сервер номи нотўғри")
        r = self.client.post(reverse("panel:mail_save"), {**FORM, "from_email": "notog'ri"}, follow=True)
        self.assertContains(r, "«Кимдан» манзили")

    def test_backend_uses_panel_settings(self):
        mail.save({"host": "smtp.yandex.ru", "port": 465, "security": "ssl", "username": "u@ya.ru", "from_email": "",
                   "from_name": ""}, self.admin, new_password="sir")
        backend = mail.PanelMailBackend(alias="default")
        inner = backend._inner()
        self.assertEqual((inner.host, inner.port, inner.use_ssl, inner.use_tls, inner.username, inner.password),
                         ("smtp.yandex.ru", 465, True, False, "u@ya.ru", "sir"))
        with mock.patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=1) as sent:
            from django.core.mail import EmailMessage
            m = EmailMessage("s", "b", None, ["x@y.uz"])
            backend.send_messages([m])
            self.assertTrue(sent.called)
            self.assertEqual(m.from_email, "mhisobot <u@ya.ru>")

    def test_backend_without_server_prints(self):
        with mock.patch.dict("os.environ", {"EMAIL_HOST": ""}):
            self.assertEqual(mail.config()["source"], "none")
            from django.core.mail.backends.console import EmailBackend
            self.assertIsInstance(mail.PanelMailBackend(alias="default")._inner(), EmailBackend)

    def test_test_email_and_preview(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("panel:mail_save"), FORM)
        r = self.client.post(reverse("panel:mail_test"), {"to": "men@test.uz"}, follow=True)
        self.assertContains(r, "Синов хати men@test.uz га юборилди")
        self.assertEqual(outbox.outbox[0].subject, "[синов] mhisobot: паролни тиклаш коди 482 913")
        prev = self.client.get(reverse("panel:mail_preview"))
        self.assertContains(prev, "482 913")
        self.assertEqual(prev["X-Frame-Options"], "SAMEORIGIN")
        csp = prev["Content-Security-Policy"]  # умумий «frame-ancestors 'none'» iframe'ни бўш қолдирарди
        self.assertIn("frame-ancestors 'self'", csp)
        self.assertIn("default-src 'none'", csp)

    def test_test_email_error_explained(self):
        import smtplib
        self.client.force_login(self.admin)
        self.client.post(reverse("panel:mail_save"), FORM)
        with mock.patch("django.core.mail.message.EmailMessage.send",
                        side_effect=smtplib.SMTPAuthenticationError(535, b"bad credentials")):
            r = self.client.post(reverse("panel:mail_test"), {"to": "men@test.uz"}, follow=True)
        self.assertContains(r, "илова пароли")

    def test_staff_views_only(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("panel:mail")), "Фойдаланувчига борадиган хат")
        self.assertEqual(self.client.post(reverse("panel:mail_save"), FORM).status_code, 403)
        self.assertEqual(self.client.post(reverse("panel:mail_test"), {"to": "a@b.uz"}).status_code, 403)

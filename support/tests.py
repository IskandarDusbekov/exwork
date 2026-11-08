import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from panel.models import Event

from . import services
from .models import Conversation, Message

PASSWORD = "Kuchli-parol-2026"
TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_support_media_")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class SupportTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="Bosh", last_name="Admin")
        self.viewer = User.objects.create_user("viewer@test.uz", PASSWORD, is_staff=True)
        self.ali = User.objects.create_user("ali@test.uz", PASSWORD, first_name="Ali", email_verified=True)
        self.vali = User.objects.create_user("vali@test.uz", PASSWORD, first_name="Vali")
        self.client.force_login(self.ali)

    def _send(self, text="Салом", **extra):
        return self.client.post(reverse("support:send"), {"text": text, **extra})

    # ---------- фойдаланувчи ----------
    def test_send_and_history_survives(self):
        r = self._send("Нархлаш ишламаяпти")
        self.assertEqual(r.status_code, 200)
        conv = Conversation.objects.get(user=self.ali)
        self.assertEqual(conv.messages.get().text, "Нархлаш ишламаяпти")
        d = self.client.get(reverse("support:messages")).json()  # «саҳифа янгиланди» — тарих базадан
        self.assertEqual([m["text"] for m in d["messages"]], ["Нархлаш ишламаяпти"])
        self.assertFalse(d["messages"][0]["staff"])

    def test_widget_on_every_page_and_page_opens(self):
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, 'id="sup-btn"')
        self.assertContains(r, "data-support-open")  # менюдаги банд — саҳифа эмас, ойнани очади
        r = self.client.get(reverse("support:page"))  # хатдаги ҳавола — «Иш столи», ойна ўзи очилади
        self.assertRedirects(r, reverse("dashboard") + "#support", fetch_redirect_response=False)

    def test_anonymous_cannot_use(self):
        self.client.logout()
        r = self.client.post(reverse("support:send"), {"text": "x"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Message.objects.exists())

    def test_user_sees_only_own_chat(self):
        self._send("Алининг сири")
        self.client.force_login(self.vali)
        self.assertEqual(self.client.get(reverse("support:messages")).json()["messages"], [])
        self._send("Валининг саволи")
        texts = [m["text"] for m in self.client.get(reverse("support:messages")).json()["messages"]]
        self.assertEqual(texts, ["Валининг саволи"])

    def test_attachment_only_owner_or_staff(self):
        self._send("скриншот", file=SimpleUploadedFile("ekran.png", PNG, content_type="image/png"))
        m = Message.objects.get()
        self.assertTrue(m.is_image)
        url = reverse("support:file", args=[m.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.vali)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.viewer)
        r = self.client.get(url + "?dl=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_DOWNLOAD, target=self.ali).exists())

    def test_csrf_required(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.ali)
        self.assertEqual(c.post(reverse("support:send"), {"text": "x"}).status_code, 403)
        self.assertFalse(Message.objects.exists())

    def test_length_limit(self):
        r = self._send("а" * (services.MAX_TEXT + 1))
        self.assertEqual(r.status_code, 400)
        self.assertIn("узун", r.json()["error"])
        self.assertEqual(self._send("   ").status_code, 400)  # бўш хабар
        self.assertFalse(Message.objects.exists())

    def test_rate_limit(self):
        self.assertEqual(self._send("1").status_code, 200)
        self.assertEqual(self._send("2").status_code, 429)  # жуда тез
        Message.objects.update(created=timezone.now() - timedelta(seconds=30))
        conv = Conversation.objects.get()
        Message.objects.bulk_create([Message(conversation=conv, author=self.ali, text=str(i),
                                             created=timezone.now() - timedelta(minutes=1))
                                     for i in range(services.RATE_MAX)])
        self.assertEqual(self._send("ортиқча").status_code, 429)

    def test_file_type_size_and_content_checked(self):
        r = self._send("x", file=SimpleUploadedFile("virus.exe", b"MZ" + b"0" * 10))
        self.assertEqual(r.status_code, 400)
        r = self._send("x", file=SimpleUploadedFile("rasm.png", b"<script>alert(1)</script>"))
        self.assertEqual(r.status_code, 400)  # номи .png, ичи — расм эмас
        r = self._send("x", file=SimpleUploadedFile("rasm.svg", b"<svg onload=alert(1)>"))
        self.assertEqual(r.status_code, 400)
        with mock.patch.object(services, "MAX_FILE", 10):
            r = self._send("x", file=SimpleUploadedFile("katta.png", PNG))
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Message.objects.exists())

    def test_html_escaped_everywhere(self):
        payload = '<img src=x onerror="alert(1)">'
        self._send(payload)
        conv = Conversation.objects.get()
        self.client.force_login(self.admin)
        r = self.client.get(reverse("panel:support_chat", args=[conv.pk]))
        self.assertNotContains(r, payload)
        self.assertContains(r, "&lt;img src=x onerror=")
        r = self.client.get(reverse("panel:support"))
        self.assertNotContains(r, payload)
        # фойдаланувчи ойнаси — JSON'дан фақат textContent билан
        from django.template.loader import get_template
        src = get_template("support/_widget.html").template.source
        self.assertNotIn("innerHTML", src)

    def test_badge_after_admin_reply_and_read(self):
        self._send("Савол")
        conv = Conversation.objects.get()
        self.client.force_login(self.admin)
        self.client.post(reverse("panel:support_reply", args=[conv.pk]), {"text": "Жавоб"})
        self.client.force_login(self.ali)
        self.assertEqual(self.client.get(reverse("support:unread")).json()["unread"], 1)
        r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.context["support_unread"], 1)
        d = self.client.get(reverse("support:messages") + "?seen=1").json()
        self.assertEqual(d["unread"], 0)
        self.assertTrue(conv.messages.get(from_staff=False).read_at)  # админ очганда ўқилди
        self.assertTrue(conv.messages.get(from_staff=True).read_at)

    # ---------- панел ----------
    def test_panel_protected(self):
        self._send("Савол")
        conv = Conversation.objects.get()
        for name, args in (("panel:support", []), ("panel:support_chat", [conv.pk])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404)  # оддий фойдаланувчи
        self.assertEqual(self.client.post(reverse("panel:support_reply", args=[conv.pk]), {"text": "x"}).status_code, 404)
        self.client.force_login(self.viewer)  # ходим — кўради, жавоб ёзолмайди
        self.assertEqual(self.client.get(reverse("panel:support_chat", args=[conv.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("panel:support_reply", args=[conv.pk]), {"text": "x"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("panel:support_status", args=[conv.pk]), {"action": "close"}).status_code, 403)
        self.assertFalse(conv.messages.filter(from_staff=True).exists())

    def test_panel_list_shows_unread_and_sidebar_badge(self):
        self._send("Биринчи")
        Message.objects.update(created=timezone.now() - timedelta(minutes=1))
        self._send("Иккинчи")
        self.client.force_login(self.admin)
        r = self.client.get(reverse("panel:support"))
        self.assertContains(r, "2 янги")
        self.assertEqual(r.context["support_new"], 2)
        self.assertContains(r, "ali@test.uz")

    def test_reply_close_audited_and_mail_sent(self):
        self._send("Ёрдам керак")
        conv = Conversation.objects.get()
        self.client.force_login(self.admin)
        with mock.patch("panel.mail.configured", return_value=True):
            r = self.client.post(reverse("panel:support_reply", args=[conv.pk]),
                                 {"text": "Мана жавоб", "file": SimpleUploadedFile("javob.pdf", b"%PDF-1.4 x")})
        self.assertEqual(r.status_code, 302)
        m = conv.messages.get(from_staff=True)
        self.assertEqual((m.text, m.author, m.attachment_name), ("Мана жавоб", self.admin, "javob.pdf"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ali@test.uz"])
        self.assertNotIn("Мана жавоб", mail.outbox[0].body)  # матн хатда йўқ — сайтда ўқилади
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_SUPPORT, actor=self.admin, target=self.ali,
                                             detail__contains="жавоб").exists())
        with mock.patch("panel.mail.configured", return_value=True):  # кетма-кет жавоб — хат такрорланмайди
            self.client.post(reverse("panel:support_reply", args=[conv.pk]), {"text": "Яна"})
        self.assertEqual(len(mail.outbox), 1)

        self.client.post(reverse("panel:support_status", args=[conv.pk]), {"action": "close"})
        conv.refresh_from_db()
        self.assertEqual((conv.status, conv.closed_by), (Conversation.Status.CLOSED, self.admin))
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_SUPPORT, detail__contains="ҳал қилинди").exists())
        # фойдаланувчи яна ёзса — қайта очилади
        self.client.force_login(self.ali)
        Message.objects.update(created=timezone.now() - timedelta(minutes=1))  # тезлик чеклови халақит бермасин
        self.assertEqual(self._send("Яна савол").status_code, 200)
        conv.refresh_from_db()
        self.assertEqual(conv.status, Conversation.Status.OPEN)

    def test_no_mail_for_unverified_email(self):
        self.client.force_login(self.vali)
        self._send("Савол")
        conv = Conversation.objects.get(user=self.vali)
        self.client.force_login(self.admin)
        with mock.patch("panel.mail.configured", return_value=True):
            self.client.post(reverse("panel:support_reply", args=[conv.pk]), {"text": "Жавоб"})
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(conv.messages.filter(from_staff=True).exists())

    def test_inspect_mode_cannot_write(self):
        """Панелдан «текширув режими»да бировнинг номидан ёзиб бўлмайди."""
        from types import SimpleNamespace
        from django.test import RequestFactory
        from . import views
        req = RequestFactory().post(reverse("support:send"), {"text": "x"})
        req.user, req.viewing_owner = self.admin, SimpleNamespace(pk=self.ali.pk)
        from django.http import Http404
        with self.assertRaises(Http404):
            views.send(req)
        self.assertFalse(Message.objects.exists())

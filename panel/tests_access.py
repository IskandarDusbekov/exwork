"""Кириш уринишлари: сўровлар хотирада йиғилади, бир марта базага ёзилади; турлари; панел саҳифаси."""
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import User

from . import access
from .models import AccessStat, BlockedIP

PASSWORD = "Kuchli-parol-2026"
K = AccessStat.Kind


@override_settings(NARXLASH_ACCESS_LOG=True, NARXLASH_ACCESS_FLUSH_SECONDS=3600, NARXLASH_STAFF_2FA=False)
class AccessTests(TestCase):
    def setUp(self):
        access._buf.clear()
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.user = User.objects.create_user("user@test.uz", PASSWORD, first_name="U", last_name="V")

    def tearDown(self):
        access._buf.clear()

    def test_repeated_requests_buffered_then_one_row(self):
        for _ in range(7):
            self.client.get("/wp-login.php")
        self.assertEqual(AccessStat.objects.count(), 0)  # ҳали базага ёзилмаган
        self.assertEqual(access.pending(), 7)
        access.flush()
        row = AccessStat.objects.get()
        self.assertEqual((row.kind, row.count, row.status, row.path), (K.SCANNER, 7, 404, "/wp-login.php"))
        self.client.get("/wp-login.php")
        access.flush()
        self.assertEqual(AccessStat.objects.get().count, 8)  # ўша қаторга қўшилди

    def test_flush_when_buffer_full(self):
        with override_settings(NARXLASH_ACCESS_FLUSH_SIZE=3):
            for i in range(3):
                self.client.get(f"/x{i}.php")
        self.assertEqual(AccessStat.objects.count(), 3)
        self.assertEqual(access.pending(), 0)

    def test_kinds(self):
        self.client.get("/", HTTP_HOST="185.1.2.3")
        self.client.get("/login/", HTTP_USER_AGENT="python-requests/2.31")
        self.client.get("/login/", HTTP_USER_AGENT="Mozilla/5.0 Chrome/130")
        self.client.post(reverse("login"), {"email": self.user.email, "password": "notogri"},
                         HTTP_USER_AGENT="Mozilla/5.0")
        Client(enforce_csrf_checks=True).post(reverse("login"), {"email": "a@b.c", "password": "x"},
                                              HTTP_USER_AGENT="Mozilla/5.0")
        BlockedIP.objects.create(ip="127.0.0.1")
        from . import sessions
        sessions.blocked_changed()
        self.client.get("/login/")
        BlockedIP.objects.all().delete()
        sessions.blocked_changed()
        self.client.force_login(self.user)
        self.client.get(reverse("dashboard"))
        access.flush()
        got = {r.kind: r for r in AccessStat.objects.all()}
        self.assertEqual(got[K.BAD_HOST].host, "185.1.2.3")
        self.assertEqual(got[K.BAD_HOST].status, 400)
        self.assertIn(K.BOT, got)
        self.assertIn(K.GUEST, got)
        self.assertEqual(got[K.LOGIN_FAIL].method, "POST")
        self.assertEqual(got[K.CSRF].status, 403)
        self.assertEqual(got[K.BLOCKED].status, 403)
        self.assertEqual(got[K.USER].user, self.user)

    def test_uuid_paths_grouped(self):
        self.client.force_login(self.user)
        for u in ("11111111-2222-3333-4444-555555555555", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"):
            self.client.get(f"/object/{u}/")
        access.flush()
        self.assertEqual(AccessStat.objects.get(kind=K.USER).path, "/object/:id/")
        self.assertEqual(AccessStat.objects.get(kind=K.USER).count, 2)

    def test_panel_page(self):
        for _ in range(12):
            self.client.get("/.env", HTTP_USER_AGENT="zgrab/0.x")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("panel:access"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Кириш уринишлари")
        self.assertContains(page, "/.env")
        self.assertContains(page, "Ҳужум")
        self.assertContains(page, "zgrab")
        page = self.client.get(reverse("panel:access"), {"ip": "127.0.0.1", "kind": "scanner", "p": "7d"})
        self.assertContains(page, "Батафсил")

    def test_regular_user_gets_404(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("panel:access")).status_code, 404)

    def test_off_by_default_in_tests(self):
        with override_settings(NARXLASH_ACCESS_LOG=False):
            self.client.get("/wp-login.php")
        self.assertEqual(access.pending(), 0)

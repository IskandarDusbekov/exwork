"""Сервер хатолари: logging → ServerLog → панелдаги «Хатолар»."""
import logging

from django.test import TestCase, override_settings
from django.urls import path, reverse

from accounts.models import User

from .models import ServerLog

PASSWORD = "Kuchli-parol-2026"


def _boom(request):
    raise ZeroDivisionError("синов: нолга бўлиш")


urlpatterns = [path("boom/", _boom)]


class ServerLogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.staff = User.objects.create_user("staff@test.uz", PASSWORD, first_name="S", last_name="T", is_staff=True)
        self.user = User.objects.create_user("user@test.uz", PASSWORD, first_name="U", last_name="V")

    def test_warning_and_error_go_to_panel_info_does_not(self):
        log = logging.getLogger("jobs.folders")
        log.info("оддий маълумот")
        log.warning("эътибор беринг")
        try:
            raise ValueError("бузилган файл")
        except ValueError:
            log.exception("қисм очилмади: %s", "1. Блок")
        rows = list(ServerLog.objects.order_by("created"))
        self.assertEqual([r.level for r in rows], [logging.WARNING, logging.ERROR])
        self.assertEqual(rows[1].message, "қисм очилмади: 1. Блок")
        self.assertIn("ValueError: бузилган файл", rows[1].traceback)
        self.assertEqual(rows[1].title, "ValueError: бузилган файл")

    @override_settings(ROOT_URLCONF="panel.tests_logs")
    def test_server_error_logged_with_request_and_friendly_page(self):
        self.client.force_login(self.user)
        self.client.raise_request_exception = False
        r = self.client.get("/boom/?x=1")
        self.assertEqual(r.status_code, 500)
        self.assertContains(r, "Серверда хато юз берди", status_code=500)
        e = ServerLog.objects.get()
        self.assertEqual((e.status, e.method, e.path, e.user), (500, "GET", "/boom/?x=1", self.user))
        self.assertIn("ZeroDivisionError", e.traceback)

    def test_404_not_logged_to_panel(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/yoq-sahifa/").status_code, 404)
        self.assertFalse(ServerLog.objects.exists())

    def test_errors_page_access(self):
        logging.getLogger("panel").error("кўриниши керак")
        url = reverse("panel:errors")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 404)  # панел борлиги ҳам кўринмайди
        self.client.force_login(self.staff)
        page = self.client.get(url)
        self.assertContains(page, "кўриниши керак")
        self.assertNotContains(page, reverse("panel:errors_clear"))  # тозалаш — фақат суперфойдаланувчига
        self.assertEqual(self.client.post(reverse("panel:errors_clear")).status_code, 403)
        self.assertContains(self.client.get(url + "?level=warning"), "Хато йўқ")

    def test_superuser_test_and_clear(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("panel:errors_test"))
        self.assertTrue(ServerLog.objects.filter(message__startswith="Синов хатоси").exists())
        self.assertContains(self.client.get(reverse("panel:overview")), "Хатолар")
        self.client.post(reverse("panel:errors_clear"))
        self.assertFalse(ServerLog.objects.exists())

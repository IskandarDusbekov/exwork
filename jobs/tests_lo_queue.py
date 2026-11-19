"""LibreOffice навбати: сервер бўйича бир вақтда кўпи билан N та ўгириш; кутаётганга хабар."""
import threading
import time

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core import convert

from . import lo_queue


@override_settings(NARXLASH_LO_SLOTS=2)
class SlotTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_limiter_is_installed(self):
        self.assertIs(convert._LIMITER, lo_queue.slot)

    def test_third_waits_and_is_told(self):
        said, order = [], []
        a, b = lo_queue.slot("a.xls"), lo_queue.slot("b.xls")
        a.__enter__(), b.__enter__()
        self.assertEqual([h["name"] for h in lo_queue.busy()], ["a.xls", "b.xls"])

        def third():
            with lo_queue.notify(lambda *x: said.append(x), max_wait=10):
                with lo_queue.slot("c.xls"):
                    order.append("c")

        t = threading.Thread(target=third)
        t.start()
        time.sleep(0.6)
        self.assertEqual(order, [])  # ўрин йўқ — кутяпти
        self.assertEqual(lo_queue.waiting(), 1)
        self.assertEqual(said[0][0], 2)  # «ҳозир 2 та очилмоқда»
        self.assertIn("Сервер банд", lo_queue.wait_text(*said[0]))
        a.__exit__(None, None, None)
        t.join(5)
        self.assertEqual(order, ["c"])
        self.assertIsNone(said[-1][0])  # навбат келди
        self.assertEqual(lo_queue.waiting(), 0)
        b.__exit__(None, None, None)
        self.assertEqual(lo_queue.busy(), [])

    def test_gives_up_with_clear_message(self):
        with lo_queue.slot("a"), lo_queue.slot("b"):
            with lo_queue.notify(None, max_wait=0.5):
                with self.assertRaisesMessage(convert.ConversionError, "Сервер жуда банд"):
                    with lo_queue.slot("c"):
                        pass
        self.assertEqual(lo_queue.busy(), [])

    def test_slot_freed_on_error(self):
        with self.assertRaises(RuntimeError):
            with lo_queue.slot("x"):
                raise RuntimeError
        self.assertEqual(lo_queue.busy(), [])


class WaitMessageTests(TestCase):
    def test_upload_wait_shows_queue_only_to_owner(self):
        u = User.objects.create_user("u@test.uz", "Kuchli-parol-2026", first_name="U", last_name="V")
        other = User.objects.create_user("o@test.uz", "Kuchli-parol-2026", first_name="O", last_name="P")
        cache.set(f"upload:wait:{u.pk}:tok1", {"text": "Сервер банд: …", "busy": 2, "ahead": 0}, 30)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("upload_wait"), {"t": "tok1"}).json()["busy"], 2)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("upload_wait"), {"t": "tok1"}).json(), {})

    def test_folder_live_step_separates_wait(self):
        from .folders import live_step
        from .models import Batch
        part = Batch(step="2/5 файл очиляпти: смета.xls — " + lo_queue.wait_text(2, 1, 12))
        live = live_step(part)
        self.assertEqual(live["name"], "смета.xls")
        self.assertTrue(live["wait"].startswith("Сервер банд: ҳозир 2 та"))

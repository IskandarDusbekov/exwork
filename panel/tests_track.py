"""Панел: «Эслаб қолинган листлар» ва «Фойдаланувчи йўли» (2-босқич)."""
import uuid

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from jobs.models import SheetMemo

from . import track
from .models import Track

PASSWORD = "Kuchli-parol-2026"


class MemoPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.boss = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="B", last_name="A")
        self.viewer = User.objects.create_user("v@test.uz", PASSWORD, first_name="V", last_name="V", is_staff=True)
        self.ali = User.objects.create_user("ali@test.uz", PASSWORD, first_name="Ali", last_name="V")
        self.memo = SheetMemo.objects.create(owner=self.ali, fp="a" * 40, role="resource",
                                             columns={"name": 3, "unit": 4, "price": 7, "start_off": 2},
                                             sample="RES (2).xlsx · Лист1", uses=3)

    def test_list_shows_who_and_how(self):
        self.client.force_login(self.viewer)
        r = self.client.get(reverse("panel:memos"))
        self.assertContains(r, "ali@test.uz")
        self.assertContains(r, "RES (2).xlsx")
        self.assertContains(r, "лист «Лист1»")
        self.assertContains(r, '<b class="font-mono text-slate-900">G</b>')  # нарх — G устун
        self.assertContains(r, "рақамлаш қаторидан 2 та пастда")
        self.assertNotContains(r, reverse("panel:memo_delete", args=[self.memo.pk]))  # кўрувчи ўчира олмайди

    def test_filter_and_delete(self):
        self.client.force_login(self.boss)
        self.assertNotContains(self.client.get(reverse("panel:memos"), {"role": "local"}), "RES (2).xlsx")
        self.client.post(reverse("panel:memo_delete", args=[self.memo.pk]))
        self.assertFalse(SheetMemo.objects.exists())

    def test_hidden_from_regular_user(self):
        self.client.force_login(self.ali)
        self.assertEqual(self.client.get(reverse("panel:memos")).status_code, 404)


class TrackTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.boss = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="B", last_name="A")
        self.ali = User.objects.create_user("ali@test.uz", PASSWORD, first_name="Ali", last_name="V")
        self.vali = User.objects.create_user("vali@test.uz", PASSWORD, first_name="Vali", last_name="V")

    def test_help_page_is_tracked_once(self):
        self.client.force_login(self.ali)
        self.client.get(reverse("help"))
        self.client.get(reverse("help"))  # янгилаш — икки марта саналмайди
        self.assertEqual(Track.objects.filter(user=self.ali, step="help").count(), 1)

    def test_missing_work_not_tracked(self):
        self.client.force_login(self.ali)
        self.client.get(reverse("batch", args=[uuid.uuid4()]))
        self.assertFalse(Track.objects.filter(step="review").exists())

    def test_beacon_accepts_only_known_steps(self):
        self.client.force_login(self.ali)
        w = uuid.uuid4()
        self.assertEqual(self.client.post(reverse("track"), {"s": "mark_col", "w": str(w), "d": "name=B"}).status_code, 204)
        self.client.post(reverse("track"), {"s": "download", "w": str(w)})  # саҳифадан юклаб олиш ёзиб бўлмайди
        self.client.post(reverse("track"), {"s": "hack"})
        self.assertEqual(list(Track.objects.values_list("step", "work_id", "detail")), [("mark_col", w, "name=B")])

    def test_upload_error_is_tracked_with_reason(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(self.ali)
        self.client.post(reverse("index"), {"files": [SimpleUploadedFile("a.txt", b"x")]},
                         HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        t = Track.objects.get(user=self.ali)
        self.assertEqual(t.step, "upload_error")
        self.assertTrue(t.detail)

    def test_report_funnel_and_stops(self):
        w = uuid.uuid4()
        for step in ("upload_page", "uploaded", "review", "price_start", "result"):
            Track.objects.create(user=self.ali, step=step, work_id=w)
        for step in ("upload_page", "uploaded", "review"):
            Track.objects.create(user=self.vali, step=step, work_id=uuid.uuid4())
        rep = track.report(30)
        f = {s["key"]: s for s in rep["funnel"]}
        self.assertEqual((f["review"]["users"], f["price_start"]["users"]), (2, 1))
        self.assertEqual(f["price_start"]["drop"], 1)
        stop = {s["key"]: s for s in rep["stopped"]}
        self.assertEqual(stop["review"]["users"], [self.vali.pk])  # листларни кўрди, нархлашни бошламади
        self.assertEqual(stop["result"]["users"], [self.ali.pk])

        self.client.force_login(self.boss)
        r = self.client.get(reverse("panel:track"))
        self.assertContains(r, "Қаерда тўхтаб қолишди")
        self.assertContains(r, "vali@test.uz")

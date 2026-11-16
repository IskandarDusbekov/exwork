"""Смета­чи листни қўлда тушунтирса — тизим эслаб қолади ва шундай тузилмали листга кейин ўзи қўллайди."""
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from core.tests.test_batch import OBJ_LOCAL

from .models import Batch, SheetMemo
from .tests import LoggedInTestCase

TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_memo_media_")
SHEET = "202611410_БВ"


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class SheetMemoTests(LoggedInTestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def _upload(self) -> Batch:
        self.client.post(reverse("index"), {"files": [SimpleUploadedFile(OBJ_LOCAL.name, OBJ_LOCAL.read_bytes())]})
        return Batch.objects.order_by("-created").first()

    def _fix_columns(self, batch: Batch) -> int:
        idx = next(i for i, s in enumerate(batch.sheets) if s["sheet"] == SHEET)
        start = batch.sheets[idx]["columns"]["start"]
        form = {f"role_{i}": s["role"] for i, s in enumerate(batch.sheets)}
        form.update({f"cols_{idx}_on": "1", f"cols_{idx}_name": "C", f"cols_{idx}_unit": "D",
                     f"cols_{idx}_start": str(start)})
        r = self.client.post(reverse("batch_replan", args=[batch.id]), form, follow=True)
        self.assertContains(r, "Созлашингиз эслаб қолинди")
        return start

    def test_manual_columns_remembered_and_applied_to_next_upload(self):
        first = self._upload()
        self.assertTrue(first.sheet(f"0:{SHEET}")["fp"])  # тузилма изи бор (номга боғлиқ эмас)
        start = self._fix_columns(first)
        memo = SheetMemo.objects.get(owner=self.user, fp=first.sheet(f"0:{SHEET}")["fp"])
        self.assertEqual((memo.columns["name"], memo.columns["unit"]), (3, 4))
        anchor = first.sheet(f"0:{SHEET}")["anchor"]
        self.assertEqual(memo.columns.get("start_off"), start - anchor)  # рақамлаш қаторига нисбатан

        # худди шундай файл яна келди — смета­чи ҳеч нарса кўрсатмайди, тизим ўзи қўллайди
        second = self._upload()
        s = second.sheet(f"0:{SHEET}")
        self.assertEqual(s["memo"], "fp")
        self.assertFalse(s["manual"])
        self.assertEqual(s["recall"], {"name": 3, "unit": 4, "start": start})
        self.assertEqual(second.columns, {})  # қўлда эмас — эслатмадан
        page = self.client.get(reverse("batch", args=[second.id]))
        self.assertContains(page, "аввалги созламангиз")
        memo.refresh_from_db()
        self.assertGreaterEqual(memo.uses, 1)

        # нархлашга ҳам эслатмадаги устунлар узатилади
        pair = next(n for n, p in enumerate(second.pairs) if p["local"] == f"0:{SHEET}")
        self.client.post(reverse("batch_confirm", args=[second.id]),
                         {f"include_{pair}": "1", f"res_{pair}": second.pairs[pair]["res"]})
        job = second.jobs.get(local_sheet=SHEET)
        self.assertEqual(job.local_columns, {"name": 3, "unit": 4, "start": start})

    def test_forget_removes_memo_and_reanalyzes(self):
        first = self._upload()
        self._fix_columns(first)
        second = self._upload()
        self.assertEqual(second.sheet(f"0:{SHEET}")["memo"], "fp")
        r = self.client.post(reverse("batch_forget", args=[second.id]), {"key": f"0:{SHEET}"}, follow=True)
        self.assertContains(r, "унутилди")
        second.refresh_from_db()
        self.assertEqual(second.sheet(f"0:{SHEET}")["memo"], "")
        self.assertFalse(SheetMemo.objects.filter(owner=self.user).exists())

    def test_role_pick_is_remembered_and_said_to_user(self):
        batch = self._upload()
        d = self.client.post(reverse("batch_pick", args=[batch.id]),
                             {"what": "role", "key": "0:202611400_БВ", "role": "unknown"}).json()
        self.assertTrue(d["ok"])
        self.assertIn("эслаб қолинди", d["remembered"])
        memo = SheetMemo.objects.get(owner=self.user)
        self.assertEqual(memo.role, "unknown")

    def test_memo_is_per_user(self):
        first = self._upload()
        self._fix_columns(first)
        from accounts.models import User
        other = User.objects.create_user("boshqa-memo@test.uz", "Kuchli-parol-2026")
        self.client.force_login(other)
        theirs = self._upload()
        self.assertEqual(theirs.sheet(f"0:{SHEET}")["memo"], "")


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class MarkPageTests(LoggedInTestCase):
    """Офёртадагидек белгилаш: лист тури → биринчи қатор → устунлар; сақланса қайта жуфтланади ва эслаб қолинади."""

    def _upload(self) -> Batch:
        self.client.post(reverse("index"), {"files": [SimpleUploadedFile(OBJ_LOCAL.name, OBJ_LOCAL.read_bytes())]})
        return Batch.objects.order_by("-created").first()

    def test_page_opens_with_sheet_state(self):
        batch = self._upload()
        r = self.client.get(reverse("batch_mark", args=[batch.id]), {"k": f"0:{SHEET}"})
        self.assertContains(r, "Тизимга листни кўрсатинг")
        self.assertContains(r, '"role": "local"')
        self.assertEqual(self.client.get(reverse("batch_mark", args=[batch.id]), {"k": "0:йўқ"}).status_code, 404)

    def test_save_sets_columns_and_remembers(self):
        batch = self._upload()
        key = f"0:{SHEET}"
        start = batch.sheet(key)["columns"]["start"]
        r = self.client.post(reverse("batch_mark", args=[batch.id]),
                             {"key": key, "role": "local", "name": "C", "unit": "D", "qty_total": "F",
                              "start": str(start)}, follow=True)
        self.assertContains(r, "сиз кўрсатгандек ўқилди")
        self.assertContains(r, "Созлашингиз эслаб қолинди")
        batch.refresh_from_db()
        self.assertEqual(batch.columns[key], {"name": 3, "unit": 4, "qty_total": 6, "start": start})
        self.assertEqual(batch.roles[key], "local")
        self.assertTrue(SheetMemo.objects.filter(owner=self.user, fp=batch.sheet(key)["fp"]).exists())

    def test_missing_required_column_is_refused(self):
        batch = self._upload()
        key = f"0:{SHEET}"
        r = self.client.post(reverse("batch_mark", args=[batch.id]),
                             {"key": key, "role": "resource", "name": "C", "start": "5"}, follow=True)
        self.assertContains(r, "Кўрсатилмади")
        batch.refresh_from_db()
        self.assertNotIn(key, batch.columns or {})

    def test_not_needed_skips_sheet(self):
        batch = self._upload()
        key = f"0:{SHEET}"
        self.client.post(reverse("batch_mark", args=[batch.id]), {"key": key, "role": "unknown"})
        batch.refresh_from_db()
        self.assertEqual(batch.sheet(key)["role"], "unknown")
        self.assertNotIn(key, batch.columns or {})

    def test_next_must_be_local(self):
        batch = self._upload()
        r = self.client.post(reverse("batch_mark", args=[batch.id]),
                             {"key": f"0:{SHEET}", "role": "unknown", "next": "https://evil.example/"})
        self.assertEqual(r["Location"], reverse("batch", args=[batch.id]))

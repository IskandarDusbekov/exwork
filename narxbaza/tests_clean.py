"""Нархни паст / ўрта / юқори қилиб белгилаш, нархни ўчириш, ишлатилмайдиган нархларни тозалаш, сервердаги
керакмас файлларни ўчириш."""
import io
import os
import shutil
import tempfile
import time
import zipfile
from datetime import date
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User

from . import services, storage
from .models import Batch, Intake, Item, Log, Price, Stat
from .tests import PASSWORD
from .tests_intake import IntakeUploadTests

DAY = date(2026, 11, 1)


def _prices(values, name="Цемент М400", unit="т"):
    out = []
    item = None
    for v in values:
        p = services.add_manual(name=name, unit=unit, category="M", price=D(v), region="buxoro", day=DAY, item=item)
        item = p.item
        out.append(p)
    return out


def _stat(item):
    return Stat.objects.get(item=item, region="buxoro", year=2026)


class PinAndDeleteTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)

    def test_pin_outlier_as_high_and_back(self):
        ps = _prices([100, 110, 120, 130, 1000])
        big = Price.objects.get(pk=ps[-1].pk)
        self.assertTrue(big.outlier)
        self.assertEqual(_stat(big.item).high, D(130))
        r = self.client.post(reverse("panel:narx_price", args=[big.pk, "pin"]), {"pin": "high"})
        self.assertEqual(r.status_code, 302)
        big.refresh_from_db()
        self.assertEqual((big.pin, big.outlier), ("high", False))
        s = _stat(big.item)
        self.assertEqual((s.low, s.high, s.n), (D(100), D(1000), 5))
        self.assertTrue(Log.objects.filter(action=Log.Action.PIN, price=big).exists())
        page = self.client.get(reverse("panel:narx_item", args=[big.item_id]))
        self.assertContains(page, "юқори нарх — белгиланган")
        # бошқасини «юқори» қилса — олдингисидан белги олинади
        services.set_pin(Price.objects.get(pk=ps[2].pk), "high")
        big.refresh_from_db()
        self.assertEqual(big.pin, "")
        self.assertEqual(_stat(big.item).high, D(120))
        # белгини олиш — яна умумий қоида
        services.set_pin(Price.objects.get(pk=ps[2].pk), "")
        big.refresh_from_db()
        self.assertTrue(big.outlier)
        self.assertEqual(_stat(big.item).high, D(130))

    def test_pin_excluded_price_brings_it_back(self):
        ps = _prices([100, 200, 300])
        p = Price.objects.get(pk=ps[0].pk)
        services.set_excluded(p, True)
        self.assertEqual(_stat(p.item).low, D(200))
        services.set_pin(p, "low")
        p.refresh_from_db()
        self.assertFalse(p.excluded)
        self.assertEqual(_stat(p.item).low, D(100))

    def test_counted_price_needs_word(self):
        ps = _prices([100, 110, 120])
        url = reverse("panel:narx_price", args=[ps[0].pk, "delete"])
        self.client.post(url)
        self.client.post(url, {"confirm": "ha"})
        self.assertTrue(Price.objects.filter(pk=ps[0].pk).exists())
        self.client.post(url, {"confirm": " ўчириш "})
        self.assertFalse(Price.objects.filter(pk=ps[0].pk).exists())

    def test_same_prices_shown_once_and_handled_together(self):
        ps = _prices([100, 100, 100, 130, 5000])
        page = self.client.get(reverse("panel:narx_item", args=[ps[0].item_id]))
        self.assertEqual(len(page.context["prices"]), 3)
        self.assertContains(page, "× 3 жойда")
        self.assertContains(page, "яна 2 та жойдан")
        self.client.post(reverse("panel:narx_price", args=[ps[1].pk, "exclude"]), {"note": "эски"})
        self.assertEqual(Price.objects.filter(item=ps[0].item, excluded=True).count(), 3)
        self.client.post(reverse("panel:narx_price", args=[ps[1].pk, "include"]))
        services.set_pin(Price.objects.get(pk=ps[2].pk), "low")
        self.assertEqual(Price.objects.filter(item=ps[0].item, pin="low").count(), 3)
        self.client.post(reverse("panel:narx_price", args=[ps[0].pk, "delete"]), {"confirm": "ЎЧИРИШ"})
        self.assertEqual(Price.objects.filter(item=ps[0].item).count(), 2)

    def test_delete_price(self):
        ps = _prices([100, 110, 120, 130, 1000])
        big = ps[-1]
        r = self.client.post(reverse("panel:narx_price", args=[big.pk, "delete"]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Price.all_objects.filter(pk=big.pk).exists())
        self.assertFalse(Batch.all_objects.filter(pk=big.batch_id).exists())  # қўлда киритилган бўш тўплам ҳам кетди
        lg = Log.objects.get(action=Log.Action.PRICE_DEL)
        self.assertIn("1 000", lg.detail)
        self.assertEqual(_stat(ps[0].item).n, 4)


class CleanPricesTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)
        self.media = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.media, ignore_errors=True)

    def test_list_select_and_delete(self):
        a = _prices([100, 110, 120, 130, 1000])
        b = _prices([50, 60, 70, 80, 1], name="Қум", unit="м3")
        services.set_excluded(Price.objects.get(pk=a[0].pk), True)
        with override_settings(MEDIA_ROOT=self.media):
            page = self.client.get(reverse("panel:narx_clean"))
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.context["total"], 3)
            self.assertContains(page, "жуда юқори")
            self.assertContains(page, "жуда паст")
            self.assertEqual(self.client.get(reverse("panel:narx_clean") + "?k=out&q=қум").context["total"], 1)
            # танлаб ўчириш — фақат ишлатилмайдиганлар (ҳисобдагини юборса ҳам ўчмайди)
            self.client.post(reverse("panel:narx_clean_prices"), {"id": [a[-1].pk, a[1].pk]})
            self.assertFalse(Price.objects.filter(pk=a[-1].pk).exists())
            self.assertTrue(Price.objects.filter(pk=a[1].pk).exists())
            # фильтрга мос ҳаммаси — тасдиқлаш сўзисиз ўчмайди
            self.client.post(reverse("panel:narx_clean_prices"), {"all": "1", "k": "out"})
            self.assertTrue(Price.objects.filter(pk=b[-1].pk).exists())
            self.client.post(reverse("panel:narx_clean_prices"), {"all": "1", "k": "out", "confirm": "ўчириш"})
            self.assertFalse(Price.objects.filter(pk=b[-1].pk).exists())
            self.assertTrue(Price.objects.filter(pk=a[0].pk).exists())  # ҳисобдан чиқарилгани — «четда» эмас
            self.assertEqual(Price.objects.filter(pin="", excluded=True).count(), 1)

    def test_staff_cannot(self):
        staff = User.objects.create_user("s@test.uz", PASSWORD, is_staff=True)
        self.client.force_login(staff)
        r = self.client.get(reverse("panel:narx_clean"))
        self.assertNotEqual(r.status_code, 200)


class StorageTests(TestCase):
    setUp, tearDown, _xlsx = IntakeUploadTests.setUp, IntakeUploadTests.tearDown, IntakeUploadTests._xlsx

    def _zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("obekt/taklif.xlsx", self._xlsx().read())
            z.writestr("obekt/hujjat.pdf", b"%PDF-1.4 " + b"x" * 5000)
            z.writestr("obekt/bosh.xlsx", self._xlsx([["Салом"], ["дунё"]]).read())
        return SimpleUploadedFile("hammasi.zip", buf.getvalue())

    def test_archive_removed_after_read_view_kept(self):
        with override_settings(MEDIA_ROOT=self.media, NARXBAZA_INLINE=True):
            self.client.post(reverse("panel:narx_upload"), {"file": self._zip(), "kind": "excel", "category": "K"})
            it = Intake.objects.get()
            self.assertEqual(it.status, "open")
            root = os.path.join(self.media, it.progress["root"])
            left = [os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/") for d, _, fs in os.walk(root) for f in fs]
            self.assertEqual(left, [it.files[0]["path"].split(it.progress["root"] + "/")[1]])  # фақат Excel кўриниши
            self.assertTrue(it.progress.get("pruned"))
            self.assertEqual(it.progress["src"], [])
            sheet = self.client.get(reverse("panel:narx_intake_sheet", args=[it.pk, 0]))
            self.assertIn("Ноутбук HP", sheet.json()["html"])
            it.rows.update(state="ok")
            self.client.post(reverse("panel:narx_intake_commit", args=[it.pk]), {"region": "buxoro", "date": "2026-11-01", "fi": ["0"]})
            b = Batch.objects.get()
            self.assertEqual(b.file_path, it.files[0]["path"])
            self.assertEqual(self.client.get(reverse("panel:narx_batch_file", args=[b.pk])).status_code, 200)

    def test_cleanup_page_old_files(self):
        with override_settings(MEDIA_ROOT=self.media, NARXBAZA_INLINE=True):
            self.client.post(reverse("panel:narx_upload"), {"file": self._xlsx(), "kind": "excel", "category": "K"})
            it = Intake.objects.get()
            # аввал юкланган (тозаланмаган) каби: архив қолган
            junk = os.path.join(self.media, it.progress["root"], "src", "eski.zip")
            with open(junk, "wb") as f:
                f.write(b"z" * 3000)
            orphan = os.path.join(self.media, "narxbaza", "up_eski")
            os.makedirs(os.path.join(orphan, "src"))
            with open(os.path.join(orphan, "src", "a.xls"), "wb") as f:
                f.write(b"x" * 2000)
            old = time.time() - 3 * 24 * 3600
            os.utime(orphan, (old, old))
            from .backup import backups_dir
            backups_dir().mkdir(parents=True, exist_ok=True)
            for k in range(5):
                p = backups_dir() / f"tiklashdan-oldin-2026010{k}.zip"
                p.write_bytes(b"b" * 100)
                os.utime(p, (old + k, old + k))
            page = self.client.get(reverse("panel:narx_clean"))
            rows = {r["key"]: r for r in page.context["files"]["rows"]}
            self.assertEqual(rows["prune"]["size"], 3000)
            self.assertEqual(rows["orphans"]["count"], 1)
            self.assertEqual(rows["backups"]["count"], 2)
            r = self.client.post(reverse("panel:narx_clean_files"), {"key": ["prune", "orphans", "backups"]}, follow=True)
            self.assertContains(r, "бўшади")
            self.assertFalse(os.path.exists(junk))
            self.assertFalse(os.path.exists(orphan))
            self.assertEqual(len(list(backups_dir().glob("*.zip"))), storage.KEEP_BACKUPS)
            self.assertTrue(os.path.isfile(os.path.join(self.media, Intake.objects.get().files[0]["path"])))

    def test_drop_views_optional(self):
        with override_settings(MEDIA_ROOT=self.media, NARXBAZA_INLINE=True):
            self.client.post(reverse("panel:narx_upload"), {"file": self._xlsx(), "kind": "excel", "category": "K"})
            it = Intake.objects.get()
            it.rows.update(state="ok")
            self.client.post(reverse("panel:narx_intake_commit", args=[it.pk]), {"region": "buxoro", "date": "2026-11-01", "fi": ["0"]})
            root = os.path.join(self.media, it.progress["root"])
            rows = {r["key"]: r for r in self.client.get(reverse("panel:narx_clean")).context["files"]["rows"]}
            self.assertTrue(rows["views"]["optional"] and rows["views"]["size"])
            self.client.post(reverse("panel:narx_clean_files"), {"key": ["prune", "cache"]})
            self.assertTrue(os.path.isdir(root))  # белгиланмаса — тегмайди
            self.client.post(reverse("panel:narx_clean_files"), {"key": ["views"]})
            self.assertFalse(os.path.isdir(root))
            self.assertEqual(Batch.objects.get().file_path, "")
            self.assertEqual(Price.objects.count(), 2)
            r = self.client.get(reverse("panel:narx_intake_sheet", args=[it.pk, 0]))
            self.assertEqual(r.status_code, 404)


class BackupWithoutFilesTests(TestCase):
    setUp, tearDown, _xlsx = IntakeUploadTests.setUp, IntakeUploadTests.tearDown, IntakeUploadTests._xlsx

    def test_data_only_download(self):
        with override_settings(MEDIA_ROOT=self.media, NARXBAZA_INLINE=True):
            self.client.post(reverse("panel:narx_upload"), {"file": self._xlsx(), "kind": "excel", "category": "K"})
            for q, has in (("?files=0", False), ("", True)):
                r = self.client.get(reverse("panel:narx_backup_download") + q)
                data = b"".join(r.streaming_content)
                r.close()
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    self.assertEqual(any(n.startswith("media/") for n in z.namelist()), has)


class SavedBackupDeleteTests(TestCase):
    setUp, tearDown = IntakeUploadTests.setUp, IntakeUploadTests.tearDown

    def test_delete_needs_word(self):
        with override_settings(MEDIA_ROOT=self.media):
            from .backup import backups_dir
            backups_dir().mkdir(parents=True, exist_ok=True)
            for k in range(3):
                (backups_dir() / f"tiklashdan-oldin-2026010{k}.zip").write_bytes(b"b" * 100)
            outside = os.path.join(self.media, "narxbaza", "boshqa.zip")
            open(outside, "wb").close()
            page = self.client.get(reverse("panel:narx_backup"))
            self.assertContains(page, "Ҳаммасини ўчириш")
            url = reverse("panel:narx_backup_delete")
            self.client.post(url, {"name": "tiklashdan-oldin-20260100.zip"})
            self.assertEqual(len(list(backups_dir().glob("*.zip"))), 3)
            self.client.post(url, {"name": "tiklashdan-oldin-20260100.zip", "confirm": "ўчириш"})
            self.assertEqual(len(list(backups_dir().glob("*.zip"))), 2)
            self.client.post(url, {"name": "../boshqa.zip", "confirm": "ЎЧИРИШ"})  # рўйхатда йўқ — тегмайди
            self.assertTrue(os.path.exists(outside))
            self.client.post(url, {"all": "1", "confirm": "ЎЧИРИШ"})
            self.assertEqual(list(backups_dir().glob("*.zip")), [])


class RekeyAndCheckTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)

    def _old(self, key, name, prices):
        from .models import Item
        it = Item.objects.create(key=key, name=name, unit="т", category="M")
        for v in prices:
            services.add_manual(name=name, unit="т", category="M", price=D(v), region="buxoro", day=DAY, item=it)
        return it

    def test_rekey_merges_punctuation_variants(self):
        from .models import Item
        a = self._old("пиломатериалы хвойных пород. доски 4-6,5 м|т", "ПИЛОМАТЕРИАЛЫ ХВОЙНЫХ ПОРОД. ДОСКИ 4-6,5 М", [100, 110])
        b = self._old("пиломатериалы хвойных пород доски 4-6.5 м|т", "ПИЛОМАТЕРИАЛЫ ХВОЙНЫХ ПОРОД ДОСКИ 4-6.5 М", [120])
        c = self._old("арматура a-ii|т", "Арматура A-II", [5])
        d = self._old("арматура a-iii|т", "Арматура A-III", [6])
        out = services.rekey_all()
        self.assertEqual(out["merged"], 1)
        self.assertFalse(Item.all_objects.filter(pk=b.pk).exists())
        a.refresh_from_db()
        self.assertEqual(a.key, services.item_key(b.name, "т"))
        self.assertEqual(a.prices.count(), 3)
        self.assertTrue(Item.objects.filter(pk=c.pk).exists() and Item.objects.filter(pk=d.pk).exists())
        # кейин нуқтасиз / вергулли ном билан келган нарх — ўша ресурсга
        p = services.add_manual(name="Пиломатериалы хвойных пород, доски 4-6,5 м.", unit="т", category="M",
                                price=D(130), region="buxoro", day=DAY)
        self.assertEqual(p.item_id, a.pk)
        self.assertEqual(services.rekey_all()["merged"], 0)

    def test_check_page_and_bulk(self):
        a = _prices([100, 110], name="Агрегаты окрасочные высокого давления")[0].item
        b = _prices([120], name="Агрегаты окрасочные высокого давлении")[0].item
        _prices([5], name="Арматура класса A-II")
        _prices([6], name="Арматура класса A-III")
        r = self.client.get(reverse("panel:narx_check") + "?s=dup")
        self.assertEqual(len(r.context["pairs"]), 1)
        self.assertContains(r, '<mark class="rounded bg-amber-200 px-0.5 text-slate-900">ДАВЛЕНИИ</mark>'.replace("ДАВЛЕНИИ", "давлении"))
        self.assertEqual(r.context["pairs"][0]["keep"].pk, a.pk)
        self.assertEqual(len(self.client.get(reverse("panel:narx_check") + "?s=dup&q=арматура").context["pairs"]), 0)
        self.client.post(reverse("panel:narx_check_bulk"), {"action": "merge", "pair": [f"{a.pk}-{b.pk}"]})
        self.assertFalse(Item.objects.filter(pk=b.pk).exists())
        self.assertEqual(a.prices.count(), 3)

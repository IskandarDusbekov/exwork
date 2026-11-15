"""Нарх базасининг тўлиқ нусхаси ва тиклаш."""
import io
import shutil
import tempfile
from datetime import date
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from . import backup as B
from . import services
from .models import Batch, Item, Price, Stat
from .tests import PASSWORD


class BackupTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)

    def tearDown(self):
        shutil.rmtree(self.media, ignore_errors=True)

    def _seed(self):
        p = services.add_manual(name="Цемент М400", unit="т", category="K", price=1_200_000, region="toshkent",
                                day=date(2026, 5, 1), user=self.admin)
        services.add_manual(name="Кабель ВВГ", unit="м", category="L", price=12_000, region="buxoro",
                            day=date(2026, 5, 2), user=self.admin)
        f = Path(self.media) / "narxbaza" / "up_x" / "src" / "0.xlsx"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"asl fayl")
        Batch.objects.filter(pk=p.batch_id).update(file_path="narxbaza/up_x/src/0.xlsx")
        return p

    def test_export_restore_roundtrip(self):
        with override_settings(MEDIA_ROOT=self.media):
            p = self._seed()
            r = self.client.get(reverse("panel:narx_backup_download"))
            self.assertEqual(r.status_code, 200)
            data = b"".join(r.streaming_content)
            m = B.read_manifest(io.BytesIO(data))
            self.assertEqual((m["counts"]["item"], m["counts"]["price"], m["files"]), (2, 2, 1))
            # базани бузамиз: ресурс ўчди, бегона нарх қўшилди, файл йўқолди
            Item.all_objects.filter(name="Кабель ВВГ").delete()
            services.add_manual(name="Бегона", unit="шт", category="K", price=5, region="toshkent", day=date(2026, 5, 3))
            (Path(self.media) / "narxbaza/up_x/src/0.xlsx").unlink()
            r = self.client.post(reverse("panel:narx_backup_restore"),
                                 {"file": SimpleUploadedFile("nusxa.zip", data, "application/zip")})
            self.assertRedirects(r, reverse("panel:narx_backup"))
            self.assertEqual(sorted(Item.all_objects.values_list("name", flat=True)), ["Кабель ВВГ", "Цемент М400"])
            self.assertEqual(Price.all_objects.count(), 2)
            self.assertEqual(Stat.objects.get(item__name="Цемент М400").mid, 1_200_000)
            self.assertEqual(Price.objects.get(pk=p.pk).batch.added_by, self.admin)
            self.assertEqual((Path(self.media) / "narxbaza/up_x/src/0.xlsx").read_bytes(), b"asl fayl")
            # олдинги ҳолат сақланди — унда «Бегона» бор
            saved = B.saved()
            self.assertEqual(len(saved), 1)
            self.assertEqual(B.read_manifest(B.backups_dir() / saved[0]["name"])["counts"]["item"], 2)
            # кейин қўшилган ресурс pk тўқнашмайди
            services.add_manual(name="Янги", unit="шт", category="K", price=7, region="toshkent", day=date(2026, 5, 4))
            self.assertContains(self.client.get(reverse("panel:narx_backup")), "Тиклаш")

    def test_restore_missing_user_and_bad_file(self):
        with override_settings(MEDIA_ROOT=self.media):
            other = User.objects.create_superuser("o@test.uz", PASSWORD, first_name="O", last_name="O")
            services.add_manual(name="Қум", unit="м3", category="K", price=90_000, region="toshkent",
                                day=date(2026, 5, 1), user=other)
            dest = Path(self.media) / "n.zip"
            B.export(dest)
            other.delete()
            B.restore(dest)
            self.assertIsNone(Batch.objects.get().added_by)
            self.assertEqual(Item.objects.get().name, "Қум")
            r = self.client.post(reverse("panel:narx_backup_restore"),
                                 {"file": SimpleUploadedFile("x.zip", b"not a zip", "application/zip")}, follow=True)
            self.assertContains(r, "ZIP файл бузуқ")
            self.assertEqual(Item.objects.count(), 1)


class MergeTests(TestCase):
    """Нусхани қўшиш: аралашмайди, такрорланмайди, тасдиқланган тоифа ўзгармайди."""

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)

    def tearDown(self):
        shutil.rmtree(self.media, ignore_errors=True)

    def _other_base(self):
        """«Бошқа сервер»: базани тузиб, нусхасини олиб, кейин тозалаймиз."""
        services.add_manual(name="Цемент М400", unit="т", category="K", price=1_100_000, region="samarqand",
                            day=date(2026, 4, 1), user=self.admin)
        services.add_manual(name="Кабель ВВГ 3х2,5", unit="м", category="L", price=12_000, region="buxoro",
                            day=date(2026, 4, 2), user=self.admin)
        it = Item.objects.get(name="Кабель ВВГ 3х2,5")
        it.verified = True
        it.save()
        services.add_manual(name="Ноутбук", unit="шт", category="O", price=9_000_000, region="toshkent",
                            day=date(2026, 4, 3))
        Item.objects.filter(name="Ноутбук").update(category="M", verified=True)
        dest = Path(self.media) / "other.zip"
        B.export(dest)
        for m in reversed(B.MODELS):
            m._base_manager.all().delete()
        return dest

    def test_merge_rules(self):
        with override_settings(MEDIA_ROOT=self.media):
            dest = self._other_base()
            # бу сервер: цемент бор (бошқа вилоят), кабель — шу нарх билан бор ва тасдиқланган «Материал» деб
            services.add_manual(name="ЦЕМЕНТ  м400", unit="Т", category="K", price=1_200_000, region="toshkent",
                                day=date(2026, 5, 1))
            services.add_manual(name="Кабель ВВГ 3х2,5", unit="м", category="K", price=12_000, region="buxoro",
                                day=date(2026, 4, 2))
            Item.objects.filter(name="Кабель ВВГ 3х2,5").update(verified=True)
            services.add_manual(name="Ноутбук", unit="шт", category="O", price=1, region="toshkent", day=date(2026, 1, 1))
            nb = Item.objects.get(name="Ноутбук")
            services.trash_item(nb)

            dry = B.merge(dest, dry_run=True)
            self.assertEqual(Price.all_objects.count(), 3)  # синов — ҳеч нарса ёзилмади
            self.assertEqual((dry["items_new"], dry["items_matched"], dry["prices_new"], dry["prices_dup"]), (0, 2, 1, 1))
            self.assertEqual(len(dry["cat_conflicts"]), 1)
            self.assertEqual(len(dry["trash_items"]), 1)

            rep = B.merge(dest, user=self.admin)
            self.assertEqual((rep["prices_new"], rep["prices_dup"]), (1, 1))
            cem = Item.objects.get(key=services.item_key("Цемент М400", "т"))
            self.assertEqual(sorted(cem.stats.values_list("region", flat=True)), ["samarqand", "toshkent"])
            self.assertEqual(Price.objects.get(item=cem, region="samarqand").date, date(2026, 4, 1))
            self.assertEqual(Item.objects.get(name="Кабель ВВГ 3х2,5").category, "K")  # сизники қолди
            self.assertFalse(Item.objects.filter(name="Ноутбук").exists())  # саватчадаги тикланмади
            self.assertTrue(rep["safety"].startswith("qoshishdan-oldin"))

            again = B.merge(dest, user=self.admin)  # иккинчи марта — ҳеч нарса қўшилмайди
            self.assertEqual(again.get("prices_new", 0), 0)
            self.assertEqual(Price.all_objects.count(), 4)

    def test_merge_new_items_and_pages(self):
        with override_settings(MEDIA_ROOT=self.media):
            dest = self._other_base()
            r = self.client.post(reverse("panel:narx_backup_merge"),
                                 {"file": SimpleUploadedFile("other.zip", dest.read_bytes(), "application/zip")})
            self.assertContains(r, "янги ресурс")
            self.assertEqual(Item.objects.count(), 0)
            token = r.context["token"]
            r = self.client.post(reverse("panel:narx_backup_merge_go"), {"token": token, "fname": "other.zip"})
            self.assertRedirects(r, reverse("panel:narx_backup"))
            self.assertEqual(Item.objects.count(), 3)
            self.assertEqual(Item.objects.get(name="Ноутбук").category, "M")
            self.assertTrue(Item.objects.get(name="Ноутбук").verified)
            self.assertEqual(Price.objects.count(), 3)
            self.assertEqual(self.client.post(reverse("panel:narx_backup_merge_go"), {"token": token}).status_code, 302)
            self.assertEqual(Price.objects.count(), 3)

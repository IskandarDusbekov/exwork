"""Ярим автомат йиғиш: ҳар қатор аввал текширув навбатига, базага фақат админ тасдиқлагани тушади."""
import io
import json
import shutil
import tempfile
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User

from . import intake as I
from . import services
from .models import Batch, Intake, Item, Price
from .tests import PASSWORD, make_job, row


class IntakeFlowTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.smetachi = User.objects.create_user("s@test.uz", PASSWORD, first_name="S", last_name="V")
        self.job = make_job(self.smetachi, [row(11, "ВОДА", "М3", 760), row(12, "ГРАВИЙ", "М3", 35000, status="FUZZY"),
                                            row(13, "ВОДА", "М3", 760), row(14, "ИШ", "1000 М3", 0, status="WORK_HEADER"),
                                            row(15, "ЗАТРАТЫ ТРУДА РАБОЧИХ", "чел.-ч", 53000, cat="I")])
        self.url = reverse("panel:narx_review", args=["smeta", self.job.pk])
        self.client.force_login(self.admin)

    def _open(self):
        r = self.client.get(self.url)
        intake = Intake.objects.get(status="open")
        self.assertRedirects(r, reverse("panel:narx_intake", args=[intake.pk]), fetch_redirect_response=False)
        return intake

    def _decide(self, intake, ids, status=200, **body):
        r = self.client.post(reverse("panel:narx_intake_decide", args=[intake.pk]), json.dumps({"ids": ids, **body}),
                             content_type="application/json")
        self.assertEqual(r.status_code, status, r.content)
        return r.json()

    def _commit(self, intake, fis=None, **kw):
        intake.refresh_from_db()
        fis = range(len(intake.files)) if fis is None else fis
        return self.client.post(reverse("panel:narx_intake_commit", args=[intake.pk]),
                                {"region": "buxoro", "date": "2026-05-03", "fi": [str(i) for i in fis], **kw})

    def test_pages(self):
        for name in ("panel:narx", "panel:narx_collect", "panel:narx_batches", "panel:narx_how", "panel:narx_check"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("panel:narx_check") + "?s=unver").status_code, 200)

    def test_nothing_reaches_base_until_all_reviewed(self):
        intake = self._open()
        self.assertEqual(self._open().pk, intake.pk)  # қайта очилса — ўша навбат
        rows = {r.row: r for r in intake.rows.all()}
        self.assertEqual(sorted(rows), [11, 12, 13, 15])  # иш сарлавҳаси йўқ
        self.assertEqual(rows[15].suggested, "I")
        self.assertContains(self.client.get(reverse("panel:narx_intake", args=[intake.pk])), "lokal.xlsx")
        self.assertContains(self.client.get(reverse("panel:narx_collect")), "Текширилаётганлар")
        self._commit(intake)
        self.assertFalse(Price.objects.exists())  # кўрилмаганлар бор — ёзилмади
        d = self._decide(intake, [rows[11].pk], state="ok", cat="M")
        self.assertEqual({r["row"]: r["cat"] for r in d["rows"]}, {11: "M", 13: "M"})  # тоифа ресурс бўйича
        self.assertEqual(d["c"]["new"], 3)
        self._decide(intake, [rows[12].pk], state="skip")
        self._decide(intake, [rows[13].pk, rows[15].pk], state="ok")
        r = self._commit(intake)
        b = Batch.objects.get()
        self.assertRedirects(r, reverse("panel:narx_batch", args=[b.pk]))
        self.assertEqual(sorted(Price.objects.values_list("name", flat=True)), ["ВОДА", "ЗАТРАТЫ ТРУДА РАБОЧИХ"])
        voda = Item.objects.get(name="ВОДА")
        self.assertEqual((voda.category, voda.verified), ("M", True))
        self.assertEqual(Price.objects.get(item=voda).seen, 2)
        self.assertFalse(Item.objects.filter(name="ГРАВИЙ").exists())  # рад этилган
        intake.refresh_from_db()
        self.assertEqual((intake.status, intake.batch_id), ("done", b.pk))
        self.assertNotContains(self.client.get(reverse("panel:narx_collect")), self.url)
        self._decide(intake, [rows[11].pk], status=409, state="new")  # ёпилган навбат ўзгармайди

    def test_second_time_known_rows_and_price_warning(self):
        intake = self._open()
        self._decide(intake, list(intake.rows.values_list("pk", flat=True)), state="ok", cat="K")
        self._commit(intake)
        job2 = make_job(self.smetachi, [row(5, "вода", "м3", 770, cat="M"), row(6, "ГРАВИЙ", "М3", 900_000)], name="b.xlsx")
        it2 = I.open_for_work("smeta", job2, self.admin)
        r = {x.row: x for x in it2.rows.all()}
        self.assertTrue(r[5].known)  # сиз тасдиқлагансиз (K) — смета M деса ҳам
        self.assertEqual(r[5].category, "K")
        self.assertIn("қиммат", r[6].warn)  # 35 000 → 900 000
        self.assertFalse(r[6].known)
        self.client.post(reverse("panel:narx_intake_known", args=[it2.pk]))
        self.assertEqual(list(it2.rows.filter(state="ok").values_list("row", flat=True)), [5])

    def test_cancel_and_staff_has_no_access(self):
        intake = self._open()
        self.client.post(reverse("panel:narx_intake_cancel", args=[intake.pk]))
        intake.refresh_from_db()
        self.assertEqual(intake.status, "cancelled")
        staff = User.objects.create_user("st@test.uz", PASSWORD, first_name="S", last_name="T", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 200)  # фақат кўриш
        self.assertNotEqual(self.client.get(reverse("panel:narx_intake", args=[intake.pk])).status_code, 200)

    def test_manual_add_is_verified(self):
        self.client.post(reverse("panel:narx_add"), {"name": "Ноутбук", "unit": "шт", "category": "M",
                                                     "price": "10 000 000", "region": "buxoro", "date": "2026-11-01"})
        self.assertTrue(Item.objects.get().verified)

    def test_verify_old_items(self):
        from datetime import date
        it = services.add_manual(name="КАБЕЛЬ ВВГ", unit="м", category="K", price=D(12000), region="buxoro",
                                 day=date(2026, 1, 1)).item
        Item.objects.filter(pk=it.pk).update(verified=False)
        self.assertContains(self.client.get(reverse("panel:narx_check") + "?s=unver"), "КАБЕЛЬ ВВГ")
        self.client.post(reverse("panel:narx_verify"), {"ids": [it.pk], f"c{it.pk}": "L"})
        it.refresh_from_db()
        self.assertEqual((it.category, it.verified), ("L", True))


class IntakeUploadTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)

    def tearDown(self):
        shutil.rmtree(self.media, ignore_errors=True)

    def _xlsx(self, rows=None, name="taklif.xlsx"):
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        for r in rows or [["Тижорий таклиф"], ["№", "Наименование", "Ед. изм.", "Кол-во", "Цена", "Тоифа"],
                          [1, "Ноутбук HP", "шт", 2, 10_000_000, "ускуна"], [2, "Кабель ВВГ 3х2.5", "м", 100, 12_000, "кабель"],
                          [3, "Итого", None, None, None, None]]:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return SimpleUploadedFile(name, buf.getvalue())

    def test_excel_upload_review_and_commit(self):
        with override_settings(MEDIA_ROOT=self.media, NARXBAZA_INLINE=True):
            r = self.client.post(reverse("panel:narx_upload"), {"file": self._xlsx(), "kind": "excel", "category": "K"})
            intake = Intake.objects.get()
            self.assertRedirects(r, reverse("panel:narx_intake", args=[intake.pk]))
            self.assertEqual(list(intake.rows.values_list("category", flat=True)), ["M", "L"])
            self.assertFalse(Batch.objects.exists())
            sheet = self.client.get(reverse("panel:narx_intake_sheet", args=[intake.pk, 0]))
            self.assertIn("Ноутбук HP", sheet.json()["html"])
            intake.rows.update(state="ok")
            self.client.post(reverse("panel:narx_intake_commit", args=[intake.pk]),
                             {"region": "buxoro", "date": "2026-11-01", "fi": ["0"]})
            b = Batch.objects.get()
            self.assertEqual((b.kind, b.title, b.prices.count()), ("file", "taklif.xlsx", 2))
            self.assertEqual(Price.objects.get(item__name="Ноутбук HP").qty, D(2))
            self.assertEqual(self.client.get(reverse("panel:narx_batch_file", args=[b.pk])).status_code, 200)

    def test_priced_local_smeta_upload(self):
        """Ўзбекча сарлавҳали нархли локал смета: иш сарлавҳалари ўтказиб юборилади, чел.-ч — иш ҳақи."""
        rows = [["ОБЪЕКТ"], [], ["Т/р", "Иш ва ресурслар", "Бирл.", "Шифр", "Жами", "1 бирликка", "Баҳо, сўм", "Қиймати, сўм"],
                [1, 2, 3, 4, 5, 6, 7, 8],
                ["1", "Разработка грунта экскаватором", "1000 м3", "Е01-01-013-11", 0.84, None, None, None],
                ["1.1", "Затраты труда рабочих-строителей", "чел.-ч", None, 11.592, 13.8, 53339, 618300],
                ["1.2", "Экскаваторы на гусеничном ходу", "маш.-ч", None, 17.976, 21.4, 247451, 4448000],
                ["2", "Укладка трубопроводов", "км", "Е22-01-021-01", 0.35, None, None, None],
                ["2.1", "Затраты труда рабочих-строителей", "чел.-ч", None, 144.2, 412, 53339, 7691000],
                ["2.2", "Трубы стальные электросварные 219х6 мм", "м", None, 350, 1000, 685000, 239750000],
                [], ["Итого по локальной смете"]]
        with override_settings(MEDIA_ROOT=self.media, NARXBAZA_INLINE=True):
            self.client.post(reverse("panel:narx_upload"), {"file": self._xlsx(rows, "lokal.xlsx"), "kind": "excel"})
            intake = Intake.objects.get()
            self.assertEqual(list(intake.rows.values_list("row", "category")), [(6, "I"), (7, "J"), (9, "I"), (10, "K")])

    def test_upload_without_resources(self):
        with override_settings(MEDIA_ROOT=self.media, NARXBAZA_INLINE=True):
            r = self.client.post(reverse("panel:narx_upload"), {"file": self._xlsx([["Салом"], ["дунё"]]), "kind": "excel"},
                                 follow=True)
            self.assertContains(r, "ресурс топилмади")
            self.assertEqual(Intake.objects.get().status, "failed")
            self.assertFalse(Batch.objects.exists())

    def test_xhr_upload_and_status(self):
        """Модал XHR билан юборади: жавоб JSON (саҳифа манзили), ҳолатни алоҳида сўраб туради."""
        with override_settings(MEDIA_ROOT=self.media, NARXBAZA_INLINE=True):
            r = self.client.post(reverse("panel:narx_upload"), {"file": self._xlsx(), "kind": "excel"},
                                 HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            it = Intake.objects.get()
            self.assertEqual(r.json(), {"url": reverse("panel:narx_intake", args=[it.pk])})
            st = self.client.get(reverse("panel:narx_intake_status", args=[it.pk])).json()
            self.assertEqual(st["status"], "open")
            r = self.client.post(reverse("panel:narx_upload"), {"file": SimpleUploadedFile("a.pdf", b"%PDF"), "kind": "excel"},
                                 HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(r.status_code, 400)
            self.assertIn("Excel", r.json()["error"])

    def test_reading_page_and_background_thread(self):
        from django.db import connection
        with override_settings(MEDIA_ROOT=self.media):
            self.client.post(reverse("panel:narx_upload"), {"file": self._xlsx(), "kind": "excel"})
            it = Intake.objects.get()
            self.assertEqual(it.status, "reading")  # TestCase'да on_commit ишламайди — оқим бошланмади
            self.assertContains(self.client.get(reverse("panel:narx_intake", args=[it.pk])), "data-stage")
            I.read_upload(it.pk)  # оқим шу функцияни чақиради
            it.refresh_from_db()
            self.assertEqual((it.status, it.rows.count()), ("open", 2))
        self.assertTrue(connection.is_usable() if hasattr(connection, "is_usable") else True)

    def test_zip_and_many_files(self):
        """Архив (ичида бир нечта Excel) ва бир нечта файл бирданига — битта текширув, ҳар файл алоҳида."""
        import zipfile
        a = self._xlsx(name="a.xlsx").read()
        b = self._xlsx([["Наименование", "Ед. изм.", "Цена"], ["Цемент М400", "т", 1_200_000]], "b.xlsx").read()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("смета/a.xlsx", a)
            z.writestr("смета/b.xlsx", b)
            z.writestr("смета/чизма.pdf", b"%PDF")
        with override_settings(MEDIA_ROOT=self.media, NARXBAZA_INLINE=True):
            self.client.post(reverse("panel:narx_upload"), {"file": [SimpleUploadedFile("7.zip", buf.getvalue()),
                                                                     SimpleUploadedFile("c.xlsx", b)], "kind": "excel"})
            intake = Intake.objects.get()
            self.assertEqual(len(intake.files), 3)
            self.assertEqual(intake.rows.count(), 4)
            for i in range(3):
                self.assertEqual(self.client.get(reverse("panel:narx_intake_sheet", args=[intake.pk, i])).status_code, 200)
            self.assertEqual(self.client.get(reverse("panel:narx_intake", args=[intake.pk])).status_code, 200)


class PerFileCommitTests(TestCase):
    """Папкада ҳар файлни алоҳида вилоят / сана билан базага ёзиш."""

    def setUp(self):
        from jobs.models import Batch as JobBatch, Job
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="D")
        self.client.force_login(self.admin)
        self.obj = JobBatch.objects.create(owner=self.admin, name="Объект")
        for n, rows in (("a.xlsx", [row(5, "ВОДА", "М3", 760)]), ("b.xlsx", [row(6, "ЩЕБЕНЬ", "М3", 90000),
                                                                             row(7, "ПЕСОК", "М3", 50000)])):
            Job.objects.create(owner=self.admin, batch=self.obj, local_name=n, res_name="r.xlsx", local_path="x",
                               res_path="y", status=Job.Status.DONE, result={"rows": rows})

    def test_commit_files_separately(self):
        it = I.open_for_work("object", self.obj, self.admin)
        fa = next(i for i, f in enumerate(it.files) if f["file"] == "a.xlsx")
        fb = 1 - fa
        url = reverse("panel:narx_intake_commit", args=[it.pk])
        self.client.post(url, {"region": "buxoro", "date": "2026-05-03", "fi": [str(fb)]})
        self.assertFalse(Batch.objects.exists())  # b.xlsx ҳали кўрилмаган
        I.decide(it, list(it.rows.filter(fi=fa).values_list("pk", flat=True)), state="ok")
        self.client.post(url, {"region": "buxoro", "date": "2026-05-03", "fi": [str(fa)]})
        it.refresh_from_db()
        self.assertEqual((Batch.objects.count(), it.status), (1, "open"))
        self.assertTrue(it.files[fa]["done"])
        self.assertEqual(Price.objects.get().region, "buxoro")
        with self.assertRaises(ValueError):  # ёзилган файл қаторлари ўзгармайди
            I.decide(it, list(it.rows.filter(fi=fa).values_list("pk", flat=True)), state="skip")
        ids = list(it.rows.filter(fi=fb).values_list("pk", flat=True))
        I.decide(it, ids[:1], state="ok")
        I.decide(it, ids[1:], state="skip")
        self.client.post(url, {"region": "andijon", "date": "2026-06-01", "fi": [str(fb)]})
        it.refresh_from_db()
        self.assertEqual(it.status, "done")
        self.assertEqual(sorted(Price.objects.values_list("region", flat=True)), ["andijon", "buxoro"])
        self.assertEqual(self.client.get(reverse("panel:narx_intake", args=[it.pk])).status_code, 200)



class UploadsPageTests(TestCase):
    """«Юкланганлар» саҳифаси: юкланган файл кўринади, бекор қилингани қайта очилади."""
    setUp, tearDown, _xlsx = IntakeUploadTests.setUp, IntakeUploadTests.tearDown, IntakeUploadTests._xlsx

    def test_uploads_list_and_reopen(self):
        with override_settings(MEDIA_ROOT=self.media, NARXBAZA_INLINE=True):
            self.client.post(reverse("panel:narx_upload"), {"file": self._xlsx(), "kind": "excel", "category": "K"})
            it = Intake.objects.get()
            r = self.client.get(reverse("panel:narx_uploads"))
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "taklif.xlsx")
            self.assertContains(r, "Давом этиш")
            self.assertRedirects(self.client.post(reverse("panel:narx_intake_cancel", args=[it.pk])),
                                 reverse("panel:narx_uploads"))
            it.refresh_from_db()
            self.assertEqual(it.status, "cancelled")
            self.assertContains(self.client.get(reverse("panel:narx_uploads") + "?s=cancelled"), "Қайта очиш")
            self.client.post(reverse("panel:narx_intake_reopen", args=[it.pk]))
            it.refresh_from_db()
            self.assertEqual(it.status, "open")

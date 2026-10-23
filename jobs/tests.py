import json
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from core.tests.conftest import build_local, build_res

from .models import Batch, Job

TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_media_")


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class FlowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = Path(tempfile.mkdtemp(prefix="narxlash_src_"))
        cls.local = build_local(cls.src / "Локал 3.xlsx")
        cls.res = build_res(cls.src / "Ресурс 3.xlsx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src, ignore_errors=True)
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def _upload(self, **files):
        data = {k: SimpleUploadedFile(p.name, p.read_bytes()) for k, p in files.items()}
        return self.client.post(reverse("index"), data)

    def test_index_page(self):
        r = self.client.get(reverse("index"))
        self.assertContains(r, 'name="files"')
        self.assertContains(r, "1 та катта файл")

    # ---------------- объект: бир/кўп файл ----------------
    def _upload_files(self, *paths):
        files = [SimpleUploadedFile(Path(p).name, Path(p).read_bytes()) for p in paths]
        return self.client.post(reverse("index"), {"files": files})

    def test_object_two_files_review_confirm_and_combined_download(self):
        from core.tests.test_batch import OBJ_LOCAL, OBJ_RES
        r = self._upload_files(OBJ_LOCAL, OBJ_RES)
        batch = Batch.objects.get()
        self.assertRedirects(r, reverse("batch", args=[batch.id]))
        self.assertEqual(len(batch.pairs), 4)
        page = self.client.get(reverse("batch", args=[batch.id]))
        self.assertContains(page, "🏗 Стройка:")
        self.assertContains(page, "МОСТ НА ПК 75+34")
        self.assertContains(page, "Нусхалар — бир марта олинди (6)")
        self.assertContains(page, "мослик 93%")

        # smetachi: birinchisini o'chiradi, oxirgisining nomini o'zgartiradi
        data = {}
        for n, p in enumerate(batch.pairs):
            data.update({f"res_{n}": p["res"], f"label_{n}": p["label"]})
            if n:
                data[f"include_{n}"] = "1"
        data["label_3"] = "Мост ПК 75+34"
        self.client.post(reverse("batch_confirm", args=[batch.id]), data)
        batch.refresh_from_db()
        jobs = list(batch.jobs.order_by("batch_order"))
        self.assertEqual(batch.status, Batch.Status.DONE)
        self.assertEqual([j.status for j in jobs], ["done"] * 3)
        self.assertEqual(jobs[-1].batch_label, "Мост ПК 75+34")
        self.assertEqual((jobs[-1].local_sheet, jobs[-1].res_sheet), ("202611450_БВ", "202611450_БР"))
        page = self.client.get(reverse("batch", args=[batch.id]))
        self.assertContains(page, "Ҳаммасини битта файлда")
        job_page = self.client.get(reverse("job", args=[jobs[0].id]))
        self.assertContains(job_page, "Объект қисмлари")

        dl = self.client.get(reverse("batch_download", args=[batch.id]))
        self.assertEqual(dl.status_code, 200)
        out = self.src / "obj.xlsx"
        out.write_bytes(b"".join(dl.streaming_content))
        names = load_workbook(out).sheetnames
        self.assertEqual([n for n in names if n.startswith("LOG")],
                         ["LOG 202611410_БВ", "LOG 202611420_БВ", "LOG 202611450_БВ"])

    def test_object_one_file_and_manual_role(self):
        from core.tests.test_batch import OBJ_LOCAL
        self._upload_files(OBJ_LOCAL)
        batch = Batch.objects.get()
        most = next(p for p in batch.pairs if p["local"] == "0:202611450_БВ")
        self.assertIsNone(most["res"])                       # bu faylda 450_БР yo'q
        self.assertContains(self.client.get(reverse("batch", args=[batch.id])), "жуфт топилмади")
        # smetachi 400_БВ ni «o'tkazish» qiladi
        idx = next(i for i, s in enumerate(batch.sheets) if s["sheet"] == "202611400_БВ")
        roles = {f"role_{i}": s["role"] for i, s in enumerate(batch.sheets)}
        roles[f"role_{idx}"] = "unknown"
        self.client.post(reverse("batch_replan", args=[batch.id]), roles)
        batch.refresh_from_db()
        self.assertEqual(len(batch.pairs), 3)
        self.assertEqual(batch.roles, {"0:202611400_БВ": "unknown"})

    def test_upload_errors(self):
        r = self.client.post(reverse("index"), {"files": [SimpleUploadedFile("a.csv", b"x")]})
        self.assertContains(r, "фақат .xls")
        self.assertFalse(Batch.objects.exists())

    def test_full_flow(self):
        r = self._upload(local=self.local, res=self.res)
        job = Job.objects.get()
        self.assertRedirects(r, reverse("job", args=[job.id]))
        self.assertEqual((job.local_sheet, job.res_sheet), ("202611450_БВ", "202611450_БР"))

        page = self.client.get(reverse("job", args=[job.id]))
        self.assertContains(page, "Нархлаш")

        r = self.client.post(reverse("job_run", args=[job.id]), {
            "local_sheet": job.local_sheet, "res_sheet": job.res_sheet, "fuzzy": "95"})
        self.assertRedirects(r, reverse("job", args=[job.id]))
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.DONE, job.error)
        self.assertEqual(job.mode, "smart")
        self.assertEqual(job.result["stats"]["FUZZY"], 1)
        self.assertEqual(job.result["stats"]["MACHINIST"], 1)

        page = self.client.get(reverse("job", args=[job.id]))
        self.assertNotContains(page, "#ЗНАЧ!")
        self.assertContains(page, "29 818")       # ишчилар нархи
        self.assertContains(page, "машинист")

        # compat: эски дастур каби — «--» туфайли Excel'дагидек #ЗНАЧ!
        self.client.post(reverse("job_run", args=[job.id]), {
            "local_sheet": job.local_sheet, "res_sheet": job.res_sheet, "fuzzy": "95", "mode": "compat"})
        job.refresh_from_db()
        self.assertEqual(job.mode, "compat")
        self.assertContains(self.client.get(reverse("job", args=[job.id])), "#ЗНАЧ!")

        dl = self.client.get(reverse("job_download", args=[job.id]))
        self.assertEqual(dl.status_code, 200)
        self.assertIn("narxlandi.xlsx", dl["Content-Disposition"])
        out = self.src / "dl.xlsx"
        out.write_bytes(b"".join(dl.streaming_content))
        self.assertEqual(load_workbook(out).sheetnames, ["LOG", "202611450_БВ"])

    def test_edit_percents(self):
        self._upload(local=self.local, res=self.res)
        job = Job.objects.get()
        url = reverse("job_run", args=[job.id])
        base = {"local_sheet": job.local_sheet, "res_sheet": job.res_sheet, "fuzzy": "95", "mode": "smart"}
        self.client.post(url, base)
        job.refresh_from_db()
        default_total = job.result["totals"]["s_nds"]
        self.assertEqual(job.result["percents"]["prochie"], 0.19)

        self.client.post(url, dict(base, pct_prochie="17,27", pct_s_nds="20", pct_strahovanie="0,5"))
        job.refresh_from_db()
        self.assertEqual(job.percents, {"prochie": 0.1727, "strahovanie": 0.005, "s_nds": 0.2})
        # noto'g'ri yozilgan foiz avvalgi qiymatda qoladi
        self.client.post(url, dict(base, pct_prochie="17,27", pct_s_nds="20", pct_strahovanie="abc"))
        job.refresh_from_db()
        self.assertEqual(job.percents["strahovanie"], 0.005)
        t = job.result["totals"]
        self.assertAlmostEqual(t["prochie"], (t["pryamye"] - t["transport_obor"] - t["oborudovanie"]) * 0.1727)
        self.assertAlmostEqual(t["s_nds"], t["bez_nds"] * 1.2)
        page = self.client.get(reverse("job", args=[job.id]))
        self.assertContains(page, 'value="17,27"')
        self.assertContains(page, "Всего с НДС      20 %")

        # yuqoridagi forma (pct_ maydonlarisiz) foizlarni saqlab qoladi
        self.client.post(url, dict(base, fuzzy="90"))
        job.refresh_from_db()
        self.assertEqual(job.result["percents"]["prochie"], 0.1727)

        self.client.post(url, dict(base, reset_percents="1"))
        job.refresh_from_db()
        self.assertEqual(job.percents, {})
        self.assertAlmostEqual(job.result["totals"]["s_nds"], default_total)

    def test_move_rows_between_categories(self):
        self._upload(local=self.local, res=self.res)
        job = Job.objects.get()
        self.client.post(reverse("job_run", args=[job.id]), {
            "local_sheet": job.local_sheet, "res_sheet": job.res_sheet, "fuzzy": "95", "mode": "smart"})
        job.refresh_from_db()
        rows = {r["row"]: r for r in job.result["rows"]}
        water = next(r for r, x in rows.items() if x["name"] == "ВОДА")
        cloth = next(r for r, x in rows.items() if x["name"] == "ТКАНЬ МЕШОЧНАЯ")
        url = reverse("job_categories", args=[job.id])
        post = lambda payload: self.client.post(url, json.dumps(payload), content_type="application/json")

        r = post({"rows": [water, cloth], "category": "M"})
        self.assertEqual(r.json(), {"ok": True, "changed": 2, "overrides": 2})
        job.refresh_from_db()
        self.assertEqual(job.categories, {str(water): "M", str(cloth): "M"})
        rows = {x["row"]: x for x in job.result["rows"]}
        self.assertTrue(rows[water]["override"] and rows[water]["category"] == "M")
        self.assertAlmostEqual(job.result["totals"]["oborudovanie"], rows[water]["h"] + rows[cloth]["h"])
        page = self.client.get(reverse("job", args=[job.id]))
        self.assertContains(page, 'title="Қўлда ўзгартирилган (асли K)"')  # M ustunidagi ✎ belgisi
        self.assertContains(page, 'data-filter="OVERRIDE"')

        # o'zining asl ustuniga qaytarish — qo'lda belgi olib tashlanadi
        post({"rows": [cloth], "category": "K"})
        job.refresh_from_db()
        self.assertEqual(job.categories, {str(water): "M"})
        post({"rows": [water], "category": ""})
        job.refresh_from_db()
        self.assertEqual(job.categories, {})
        self.assertEqual(job.result["totals"]["oborudovanie"], 0)

        self.assertEqual(post({"rows": [water], "category": "Z"}).status_code, 400)
        self.assertEqual(post({"rows": [99999], "category": "M"}).status_code, 400)
        self.assertEqual(post({"rows": "x"}).status_code, 400)

        # lokal list almashsa qo'lda kategoriyalar tozalanadi
        post({"rows": [water], "category": "L"})
        job.refresh_from_db()
        from .services import run_job
        run_job(job, "boshqa", None, 95)
        self.assertEqual(job.categories, {})

    def test_prices_accept_set_clear(self):
        from core.tests.test_smart import TESTS
        d = TESTS / "excel_calc/bolnitsa7_kr_blok2"
        self._upload(local=d / "input_local.xlsx", res=d / "input_res.xlsx")
        job = Job.objects.get()
        self.client.post(reverse("job_run", args=[job.id]), {
            "local_sheet": job.local_sheet, "res_sheet": job.res_sheet, "fuzzy": "95", "mode": "smart"})
        job.refresh_from_db()
        red = [r["row"] for r in job.result["rows"] if r.get("suggested_price") is not None]
        exact = next(r["row"] for r in job.result["rows"] if r["status"] == "EXACT")
        self.assertEqual(len(red), 12)
        url = reverse("job_prices", args=[job.id])
        post = lambda payload: self.client.post(url, json.dumps(payload), content_type="application/json")

        # 10 tasini taklif bo'yicha, bittasini qo'lda, bitta EXACT qatorda taklif yo'q — o'tkazib yuboriladi
        r = post({"rows": red[:10] + [exact], "action": "accept"})
        self.assertEqual(r.json(), {"ok": True, "changed": 10, "skipped": 1})
        r = post({"rows": [red[10]], "action": "set", "price": "4 500 000"})
        self.assertTrue(r.json()["ok"])
        job.refresh_from_db()
        self.assertEqual(job.result["stats"]["MANUAL"], 11)
        self.assertEqual(job.result["stats"]["NOT_PRICED"], 1)
        self.assertEqual(job.prices[str(red[10])], 4_500_000)
        page = self.client.get(reverse("job", args=[job.id]))
        self.assertContains(page, "қўлда ✓")
        self.assertContains(page, "1 та қатор нархланмади")

        post({"rows": [red[11]], "action": "accept"})
        job.refresh_from_db()
        self.assertContains(self.client.get(reverse("job", args=[job.id])), "Ҳамма қаторлар нархланган ✓")

        post({"rows": red, "action": "clear"})
        job.refresh_from_db()
        self.assertEqual(job.prices, {})
        self.assertEqual(job.result["stats"]["NOT_PRICED"], 12)
        self.assertEqual(post({"rows": [red[0]], "action": "set", "price": "abc"}).status_code, 400)
        self.assertEqual(post({"rows": [red[0]], "action": "boshqa"}).status_code, 400)

    def test_single_file_for_both(self):
        self._upload(local=self.res)
        job = Job.objects.get()
        self.assertEqual(job.local_path, job.res_path)

    def test_bad_extension(self):
        r = self.client.post(reverse("index"), {"local": SimpleUploadedFile("a.csv", b"x")})
        self.assertContains(r, "фақат .xls")
        self.assertFalse(Job.objects.exists())

    def test_wrong_sheet_shows_error(self):
        self._upload(local=self.local, res=self.res)
        job = Job.objects.get()
        self.client.post(reverse("job_run", args=[job.id]), {"local_sheet": "йўқ", "res_sheet": "", "fuzzy": "95"})
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertContains(self.client.get(reverse("job", args=[job.id])), "Нархлашда хато")

    def test_download_before_run_404(self):
        self._upload(local=self.local, res=self.res)
        job = Job.objects.get()
        self.assertEqual(self.client.get(reverse("job_download", args=[job.id])).status_code, 404)

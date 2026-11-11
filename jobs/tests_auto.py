"""«Автоматик» усул: юклашда фоизлар тасдиқланади, нархлашдан кейин таклифлар қабул қилинади ва L/M ажратилади."""
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from .models import Batch, Job
from .tests import LoggedInTestCase

TMP_MEDIA = Path(tempfile.mkdtemp(prefix="narxlash_auto_"))
UNPRICED = ("NOT_FOUND", "FUZZY", "AMBIGUOUS", "UNIT_MISMATCH")


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class AutoModeTests(LoggedInTestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def _price_object(self):
        from core.tests.test_batch import OBJ_LOCAL, OBJ_RES
        files = [SimpleUploadedFile(Path(p).name, Path(p).read_bytes()) for p in (OBJ_LOCAL, OBJ_RES)]
        self.client.post(reverse("index"), {"files": files})
        batch = Batch.objects.latest("created")
        data = {}
        for n, p in enumerate(batch.pairs):
            if p["res"]:
                data.update({f"include_{n}": "1", f"res_{n}": p["res"]})
        self.client.post(reverse("batch_confirm", args=[batch.id]), data)
        return list(Job.objects.filter(batch=batch, status=Job.Status.DONE))

    @staticmethod
    def _open_suggestions(job):
        """Ҳали қабул қилинмаган нарх таклифлари (бирлиги қандай бўлмасин — ҳаммаси бир йўла олинади)."""
        return [r for r in job.result["rows"] if r.get("status") in UNPRICED and not r.get("cleared")
                and (r.get("near") or {}).get("price") is not None and str(r["row"]) not in (job.prices or {})]

    def test_percents_confirmed_on_upload_page(self):
        page = self.client.get(reverse("index"), HTTP_REFERER="http://testserver/dashboard/")
        self.assertContains(page, 'id="auto-pct"')
        self.assertContains(page, "Сводка фоизлари")
        r = self.client.post(reverse("upload_percents"), {"pct_prochie": "17,5"})
        self.assertEqual(r.json(), {"ok": True})
        jobs = self._price_object()
        self.assertTrue(jobs)
        self.assertTrue(all(abs(j.percents["prochie"] - 0.175) < 1e-9 for j in jobs))
        self.assertEqual(self.client.post(reverse("upload_percents"), {}).status_code, 400)

    def test_xhr_upload_answers_json(self):
        from core.tests.test_batch import OBJ_LOCAL, OBJ_RES
        files = [SimpleUploadedFile(Path(p).name, Path(p).read_bytes()) for p in (OBJ_LOCAL, OBJ_RES)]
        r = self.client.post(reverse("index"), {"files": files}, headers={"X-Requested-With": "XMLHttpRequest"})
        batch = Batch.objects.latest("created")
        self.assertEqual(r.json(), {"ok": True, "url": reverse("batch", args=[batch.id]), "timing": r.json()["timing"]})
        r = self.client.post(reverse("index"), {}, headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual((r.status_code, r.json()["ok"]), (400, False))

    def test_auto_accepts_suggestions_and_splits_lm(self):
        from .views import _lm_stage, _res_groups, column_labels
        jobs = self._price_object()
        self.assertTrue(jobs)
        self.assertTrue(any(j.prices or j.categories for j in jobs))  # ҳақиқатан бирор нарса ўзи бажарилди
        for job in jobs:
            self.assertTrue(job.table_checked)
            self.assertEqual(self._open_suggestions(job), [], job.local_sheet)
            labels = column_labels(job)
            self.assertEqual(_lm_stage(job.result["rows"], _res_groups(job.result, labels), labels)["pending"], 0)
            # қабул қилинган таклиф натижада нархланган
            for row in (job.prices or {}):
                r = next(x for x in job.result["rows"] if str(x["row"]) == row)
                self.assertNotIn(r.get("status"), UNPRICED, r)

    def test_lm_base_learns_only_real_choices(self):
        """Ҳақиқий оқим: «Автоматик» қўйган ва смета­чи қабул қилган таклиф — овоз эмас; ўзи кўчиргани — овоз;
        бир хил RES файли бир неча сметада — битта RES овози."""
        import json
        from resbase.models import LMEntry, LMVote
        jobs = self._price_object()
        auto_set = [(j, int(r)) for j in jobs for r, c in (j.categories or {}).items() if c in ("L", "M")]
        self.assertTrue(all(r in (j.lm_accepted or []) for j, r in auto_set))
        self.assertFalse(LMVote.objects.filter(kind="user").exists())  # «Автоматик» ўз таклифига овоз бермайди
        # RES овозлари файл бўйича: битта RES файлдан келган ҳар ёзувда RES овози ≤ 1
        srcs = {j.id: j.res_path for j in jobs}
        for e in LMEntry.objects.all():
            for k, n in (e.votes or {}).items():
                if not k.startswith("user_"):
                    files = set(LMVote.objects.filter(entry=e, kind="res", category=k).values_list("src", flat=True))
                    self.assertEqual(n, len(files), e.name)
        self.assertTrue(srcs)

        job = jobs[0]
        rows = [r for r in job.result["rows"] if r.get("unit") and r.get("category") == "K" and r.get("status") not in
                ("WORK_HEADER", "MACHINIST", "CLEARED") and not r.get("suggested_category")
                and r.get("default_category") == "K"]
        self.assertTrue(rows)
        mine = rows[0]
        url = reverse("job_categories", args=[job.id])
        r = self.client.post(url, json.dumps({"rows": [mine["row"]], "category": "M"}), content_type="application/json")
        self.assertTrue(r.json()["ok"])
        job.refresh_from_db()
        self.assertNotIn(mine["row"], job.lm_accepted)
        vote = LMVote.objects.get(kind="user", job_id=job.id)  # смета­чи ўзи кўчирди — овоз
        self.assertEqual((vote.category, vote.entry.category), ("M", ""))  # битта смета­чи — тасдиқлашни кутади
        # қайта ҳисоблаш (бошқа ўзгариш билан) — овоз иккиланмайди ва йўқолмайди
        self.client.post(url, json.dumps({"rows": [mine["row"]], "category": "M"}), content_type="application/json")
        self.assertEqual(LMVote.objects.filter(kind="user", job_id=job.id).count(), 1)

    def test_manual_does_nothing_by_itself(self):
        self.client.post(reverse("nf_mode"), {"nf_mode": "manual"})
        self.assertContains(self.client.get(reverse("index"), HTTP_REFERER="http://testserver/dashboard/"), 'value="manual" checked')
        jobs = self._price_object()
        self.assertTrue(jobs)
        self.assertTrue(all(not j.table_checked and not j.prices and not j.categories for j in jobs))

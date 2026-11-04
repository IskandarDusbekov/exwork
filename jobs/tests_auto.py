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
        """Бирлиги мос, ҳали қабул қилинмаган нарх таклифлари."""
        return [r for r in job.result["rows"] if r.get("status") in UNPRICED and not r.get("cleared")
                and (r.get("near") or {}).get("same_unit") and str(r["row"]) not in (job.prices or {})]

    def test_percents_confirmed_on_upload_page(self):
        page = self.client.get(reverse("index"))
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
        self.assertEqual(r.json(), {"ok": True, "url": reverse("batch", args=[batch.id])})
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

    def test_manual_does_nothing_by_itself(self):
        self.client.post(reverse("nf_mode"), {"nf_mode": "manual"})
        self.assertContains(self.client.get(reverse("index")), 'value="manual" checked')
        jobs = self._price_object()
        self.assertTrue(jobs)
        self.assertTrue(all(not j.table_checked and not j.prices and not j.categories for j in jobs))

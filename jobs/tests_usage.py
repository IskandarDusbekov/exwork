"""Қатор ҳисоби (jobs/usage.py, RowUsage): нархлангач ёзилади, қайта ҳисоблашда иккинчи марта саналмайди,
смета ўчирилса ҳам қолади, давр бўйича тўғри йиғилади."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from jobs import usage
from jobs.models import Job, RowUsage


def _result(statuses):
    return {"rows": [{"row": i + 10, "status": s} for i, s in enumerate(statuses)], "stats": {}, "totals": {}}


class RowUsageTests(TestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_user(email="row@example.com", password="x-test-123")

    def _job(self, statuses, **kw):
        j = Job(owner=self.u, local_name="Локал", res_name="RES", local_path="a.xlsx", res_path="b.xlsx",
                status=Job.Status.DONE, rows=len(statuses), result=_result(statuses), **kw)
        j.save()
        return j

    def test_processed_rows_skip_empty(self):
        self.assertEqual(usage.processed_rows(_result(["EXACT", "", "NOT_FOUND", "WORK_HEADER", ""])), 3)
        # ҳолат ёзилмаган эски режим — stats'дан, ҳисоб устунлари саналмайди
        self.assertEqual(usage.processed_rows({"rows": [{}, {}, {}], "stats": {"EXACT": 2, "NOT_PRICED": 1}}), 2)

    def test_recorded_once_and_updated(self):
        j = self._job(["EXACT", "", "FUZZY"])
        self.assertEqual(j.rows_done, 2)
        rec = RowUsage.objects.get(job_id=j.id)
        self.assertEqual((rec.rows_total, rec.rows_done, rec.runs, rec.kind), (3, 2, 1, "smeta"))
        first = rec.created
        j.result = _result(["EXACT", "EXACT", "FUZZY", "BASE"])
        j.rows = 4
        j.save()
        rec.refresh_from_db()
        self.assertEqual((RowUsage.objects.count(), rec.rows_total, rec.rows_done, rec.runs), (1, 4, 4, 2))
        self.assertEqual(rec.created, first)  # сана ўзгармайди — бошқа ойга ўтиб кетмайди

    def test_not_recorded_without_result_or_failed(self):
        j = self._job(["EXACT"])
        j.save(update_fields=["status"])  # result сақланмади — ёзув янгиланмайди
        self.assertEqual(RowUsage.objects.get(job_id=j.id).runs, 1)
        Job(owner=self.u, local_name="x", res_name="y", local_path="a", res_path="b", status=Job.Status.FAILED,
            rows=5, result=_result(["EXACT"] * 5)).save()
        self.assertEqual(RowUsage.objects.count(), 1)

    def test_survives_job_delete_and_periods(self):
        a = self._job(["EXACT"] * 5)
        b = self._job(["EXACT"] * 3)
        old = timezone.now() - timedelta(days=70)
        RowUsage.objects.filter(job_id=b.id).update(created=old)
        a.delete()
        s = usage.summary(self.u)
        self.assertEqual(s["all"], {"done": 8, "total": 8, "n": 2})
        self.assertEqual(s["month"]["done"], 5)
        self.assertEqual(usage.by_user(usage.period_starts()["month"])[self.u.pk]["done"], 5)
        self.assertEqual(sum(m["done"] for m in usage.by_month(self.u)), 8)

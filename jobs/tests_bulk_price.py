"""Папка бўйича бир хил нархланмаганлар: гуруҳлаш (ном + бирлик), бир йўла нарх, устидан ёзмаслик, бекор қилиш."""
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core.tests.conftest import build_local, build_res

from . import bulk_price
from .models import Batch, Job, Project
from .services import recompute

TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_bulk_")


@override_settings(MEDIA_ROOT=TMP_MEDIA, NARXLASH_TASKS="worker")
class BulkPriceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = Path(tempfile.mkdtemp(prefix="narxlash_bulk_src_"))
        cls.local = build_local(cls.src / "Локал.xlsx")
        cls.res = build_res(cls.src / "Ресурс.xlsx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src, ignore_errors=True)
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user("bulk@test.uz", "Kuchli-parol-2026", first_name="B", last_name="P")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Объект", status=Project.Status.DONE)
        self.jobs = []
        for folder in ("1. Блок-А", "2. Блок-Б"):
            batch = Batch.objects.create(owner=self.user, name=folder, project=self.project, folder=folder)
            job = Job.objects.create(owner=self.user, batch=batch, batch_label=folder, local_name="l.xlsx",
                                     res_name="r.xlsx", local_path="in/local.xlsx", res_path="in/res.xlsx",
                                     local_sheet="202611450_БВ", res_sheet="202611450_БР")
            job.abs("in").mkdir(parents=True, exist_ok=True)
            shutil.copy(self.local, job.abs(job.local_path))
            shutil.copy(self.res, job.abs(job.res_path))
            recompute(job)
            job.status = Job.Status.DONE
            job.save()
            self.jobs.append(job)
        self.url = reverse("project_unpriced", args=[self.project.pk])

    def _groups(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        return r.json()["groups"]

    def _post(self, items, undo=False):
        return self.client.post(self.url, json.dumps({"items": items, "undo": undo}), content_type="application/json")

    def test_same_resource_in_two_files_is_one_group(self):
        groups = self._groups()
        self.assertTrue(groups, "намунада нархланмаган қатор бўлиши керак")
        g = groups[0]
        self.assertEqual(len(g["files"]), 2)  # иккала смета бир хил — ҳар гуруҳ иккала файлда
        self.assertEqual({f["job"] for f in g["files"]}, {str(j.pk) for j in self.jobs})
        self.assertEqual(g["rows"], sum(len(f["rows"]) for f in g["files"]))

    def test_apply_prices_selected_files_and_undo(self):
        g = self._groups()[0]
        first = g["files"][0]
        r = self._post([{"price": "4 600 000", "rows": {first["job"]: first["rows"]}}])
        self.assertEqual(r.json(), {"ok": True, "rows": len(first["rows"]), "files": 1, "skipped": 0})
        job = Job.objects.get(pk=first["job"])
        self.assertEqual({job.prices[str(x)] for x in first["rows"]}, {4600000.0})
        other = Job.objects.get(pk=g["files"][1]["job"])
        self.assertFalse(any(str(x) in (other.prices or {}) for x in g["files"][1]["rows"]))  # белгиланмаган — тегилмади
        self.assertEqual(self.client.get(self.url + "?pending=1").json(), {"pending": 0})  # тестда ҳисоб дарҳол

        # нархланган қатор — рўйхатда йўқ ва устидан ёзилмайди
        self.assertNotIn(first["job"], {f["job"] for gg in self._groups() if gg["name"] == g["name"] for f in gg["files"]})
        r = self._post([{"price": "1", "rows": {first["job"]: first["rows"]}}])
        self.assertEqual(r.json()["rows"], 0)
        self.assertEqual(r.json()["skipped"], len(first["rows"]))

        r = self._post([{"price": 4600000, "rows": {first["job"]: first["rows"]}}], undo=True)
        self.assertEqual(r.json()["rows"], len(first["rows"]))
        job.refresh_from_db()
        self.assertFalse(any(str(x) in job.prices for x in first["rows"]))

    def test_recalc_writes_price(self):
        g = self._groups()[0]
        items = [{"price": "1000", "rows": {f["job"]: f["rows"] for f in g["files"]}}]
        self.assertEqual(self._post(items).json()["files"], 2)
        for j in self.jobs:
            j.refresh_from_db()
            rows = {r["row"]: r for r in j.result["rows"]}
            for x in next(f["rows"] for f in g["files"] if f["job"] == str(j.pk)):
                self.assertEqual(rows[x]["g"], 1000)
        self.assertNotIn(g["name"], {gg["name"] for gg in self._groups()})

    def test_other_users_project_and_bad_input(self):
        g = self._groups()[0]
        self.assertEqual(self._post([{"price": "abc", "rows": {}}]).status_code, 400)
        self.assertEqual(self._post([{"price": "1", "rows": {"not-a-uuid": [1]}}]).status_code, 400)
        self.assertEqual(self._post([]).status_code, 400)
        self.project.status = Project.Status.PROCESSING
        self.project.save()
        self.assertEqual(self._post([{"price": "1", "rows": {g["files"][0]["job"]: g["files"][0]["rows"]}}]).status_code, 400)
        stranger = User.objects.create_user("x@test.uz", "Kuchli-parol-2026", first_name="X", last_name="Y")
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        # бошқа папканинг сметаси — тегилмайди
        other = Project.objects.create(owner=self.user, name="Бошқа", status=Project.Status.DONE)
        self.client.force_login(self.user)
        r = self.client.post(reverse("project_unpriced", args=[other.pk]),
                             json.dumps({"items": [{"price": "1", "rows": {g["files"][0]["job"]: g["files"][0]["rows"]}}]}),
                             content_type="application/json")
        self.assertEqual(r.json()["rows"], 0)

    @pytest.mark.recalc_queue
    def test_priced_but_not_recalculated_row_is_not_offered(self):
        g = self._groups()[0]
        first = g["files"][0]
        self._post([{"price": "5", "rows": {first["job"]: first["rows"]}}])
        self.assertTrue(Job.objects.get(pk=first["job"]).recalc_pending)
        self.assertEqual(self.client.get(self.url + "?pending=1").json(), {"pending": 1})
        names = {gg["name"]: {f["job"] for f in gg["files"]} for gg in self._groups()}
        self.assertNotIn(first["job"], names.get(g["name"], set()))
        self.assertEqual(self._post([{"price": "7", "rows": {first["job"]: first["rows"]}}]).json()["rows"], 0)

    def test_key_ignores_case_spaces_and_unit_spelling(self):
        a = {"name": "Бетон  М-300 ", "unit": "м³"}
        b = {"name": "бетон м-300", "unit": "М3"}
        self.assertEqual(bulk_price._key(a), bulk_price._key(b))
        self.assertNotEqual(bulk_price._key(a), bulk_price._key({"name": "бетон м-300", "unit": "т"}))

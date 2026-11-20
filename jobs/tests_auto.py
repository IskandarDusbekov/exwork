"""«Автоматик» усул: юклашда фоизлар тасдиқланади, нархлашдан кейин таклифлар қабул қилинади ва L/M ажратилади."""
import shutil
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from .services import nf_auto_of
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
        if not nf_auto_of(self.user):  # «Қўлда»: листлар ажратилмаган — смета­чи «Автоматик ажратиш»ни босади
            self.client.post(reverse("batch_pick", args=[batch.id]), {"what": "auto"})
            batch.refresh_from_db()
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
        r = self.client.post(reverse("upload_percents"), {"pct_prochie": "17,5"})
        self.assertEqual(r.json(), {"ok": True})
        jobs = self._price_object()
        self.assertTrue(jobs)
        self.assertTrue(all(abs(j.percents["prochie"] - 0.175) < 1e-9 for j in jobs))
        # фоизлар «Бошлаш» олдида (листлар билан бирга) кўрсатилади
        picker = self.client.get(reverse("batch", args=[jobs[0].batch_id]) + "?partial=picker")
        self.assertContains(picker, "Сводка фоизлари")
        self.assertContains(picker, 'name="pct_prochie" value="17,5"')
        self.assertEqual(self.client.post(reverse("upload_percents"), {}).status_code, 400)

    def test_xhr_upload_answers_json(self):
        from core.tests.test_batch import OBJ_LOCAL, OBJ_RES
        files = [SimpleUploadedFile(Path(p).name, Path(p).read_bytes()) for p in (OBJ_LOCAL, OBJ_RES)]
        r = self.client.post(reverse("index"), {"files": files}, headers={"X-Requested-With": "XMLHttpRequest"})
        batch = Batch.objects.latest("created")
        self.assertEqual(r.json(), {"ok": True, "url": reverse("batch", args=[batch.id]), "timing": r.json()["timing"], "id": str(batch.id),
                                    "picker": reverse("batch", args=[batch.id]) + "?partial=picker",
                                    "delete": reverse("work_delete", args=["object", batch.id])})
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

    def test_auto_with_lm_shown_leaves_lm_to_user(self):
        """«Автоматик» иш усули + «Ускуна ва кабель: Менга кўрсатилсин»: нарх таклифлари ўзи қабул қилинади,
        лекин L/M га ҳеч нарса ўзи ўтмайди — таклифлар 2-босқичда туради, жадвал «текширилди» эмас."""
        from .services import lm_auto_of, remember_lm_auto
        from .views import _lm_stage, _res_groups, column_labels
        remember_lm_auto(self.user, False)
        self.assertFalse(lm_auto_of(self.user))
        jobs = self._price_object()
        self.assertTrue(jobs)
        pending = 0
        for job in jobs:
            self.assertFalse(job.table_checked, job.local_sheet)
            self.assertFalse([c for c in (job.categories or {}).values() if c in ("L", "M")])
            self.assertEqual(job.lm_accepted or [], [])
            self.assertEqual(self._open_suggestions(job), [], job.local_sheet)  # нарх қисми — олдингидек автоматик
            labels = column_labels(job)
            pending += _lm_stage(job.result["rows"], _res_groups(job.result, labels), labels)["pending"]
        self.assertGreater(pending, 0)  # кабель / ускуна қаторлари кўринади — фойдаланувчи ҳал қилади

    def test_lm_base_learns_only_real_choices(self):
        """Ҳақиқий оқим: «Автоматик» қўйган ва смета­чи қабул қилган таклиф — овоз эмас; ўзи кўчиргани — овоз;
        бир хил RES файли бир неча сметада — битта RES овози."""
        import json
        from resbase.models import LMEntry, LMVote
        jobs = self._price_object()
        auto_set = [(j, int(r)) for j in jobs for r, c in (j.categories or {}).items() if c in ("L", "M")]
        self.assertTrue(all(r in (j.lm_accepted or []) for j, r in auto_set))
        # смета саҳифасида L/M картаси: «таклиф йўқ» эмас — нечтаси қабул қилингани кўринади
        if auto_set:
            page = self.client.get(reverse("job", args=[auto_set[0][0].id]))
            self.assertContains(page, "тизим таклифи қабул қилинди")
            self.assertContains(page, 'class="cat-chip lm-see"')
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
        self.assertFalse(nf_auto_of(self.user))
        # листлар белгиланмагунча нархлаш бошланмайди
        from core.tests.test_batch import OBJ_LOCAL, OBJ_RES
        files = [SimpleUploadedFile(Path(p).name, Path(p).read_bytes()) for p in (OBJ_LOCAL, OBJ_RES)]
        self.client.post(reverse("index"), {"files": files})
        batch = Batch.objects.latest("created")
        page = self.client.get(reverse("batch", args=[batch.id]) + "?partial=picker")
        self.assertContains(page, "белгиланмаган")
        self.client.post(reverse("batch_confirm", args=[batch.id]), {"include_0": "1"})
        self.assertFalse(Job.objects.filter(batch=batch).exists())
        jobs = self._price_object()
        self.assertTrue(jobs)
        self.assertTrue(all(not j.table_checked and not j.prices and not j.categories for j in jobs))

    def _manual_upload(self):
        from core.tests.test_batch import OBJ_LOCAL, OBJ_RES
        self.client.post(reverse("nf_mode"), {"nf_mode": "manual"})
        files = [SimpleUploadedFile(Path(p).name, Path(p).read_bytes()) for p in (OBJ_LOCAL, OBJ_RES)]
        self.client.post(reverse("index"), {"files": files})
        return Batch.objects.latest("created")

    def test_manual_upload_skips_analysis_until_asked(self):
        """«Қўлда»: юклашда листлар ажратилмайди; «Автоматик ажратиш» — тизим ажратади, смета­чи танлагани қолади."""
        batch = self._manual_upload()
        self.assertTrue(batch.sheets and all(s.get("pending") and s["role"] == "unknown" for s in batch.sheets))
        self.assertEqual(batch.pairs, [])
        page = self.client.get(reverse("batch", args=[batch.id]) + "?partial=picker")
        self.assertContains(page, "Автоматик ажратиш")
        self.assertNotContains(page, "тизим таклифи:")  # тизим ҳали ҳеч нарса таклиф қилмаган
        pick = reverse("batch_pick", args=[batch.id])
        keep = batch.sheets[0]["key"]
        self.assertTrue(self.client.post(pick, {"what": "role", "key": keep, "role": "unknown"}).json()["ok"])
        r = self.client.post(pick, {"what": "auto"}).json()
        self.assertTrue(r["ok"])
        self.assertEqual(r["left"], 0)
        batch.refresh_from_db()
        self.assertFalse(any(s.get("pending") for s in batch.sheets))
        self.assertEqual(batch.roles[keep], "unknown")  # смета­чи белгилагани ўзгармади
        self.assertTrue(any(s["role"] == "local" for s in batch.sheets) and any(p.get("res") for p in batch.pairs))
        # адашса — бир босишда ортга: автоматик ажратишдан олдинги ҳолат
        r = self.client.post(pick, {"what": "undo"}).json()
        self.assertTrue(r["ok"])
        batch.refresh_from_db()
        self.assertEqual(batch.roles, {keep: "unknown"})
        self.assertGreater(r["left"], 0)  # листлар яна белгиланмаган

    def test_bulk_roles_and_unpick(self):
        """Бир нечта листни бирданига «керак эмас» қилиш, кейин белгини олиш ва ортга қайтариш."""
        batch = self._manual_upload()
        pick = reverse("batch_pick", args=[batch.id])
        keys = [s["key"] for s in batch.sheets][:2]
        r = self.client.post(pick, {"what": "role", "key": keys, "role": "unknown"}).json()
        self.assertTrue(r["ok"])
        self.assertIn("2 та лист", r["undo"])
        batch.refresh_from_db()
        self.assertEqual({k: batch.roles[k] for k in keys}, {k: "unknown" for k in keys})
        page = self.client.get(reverse("batch", args=[batch.id]) + "?partial=picker")
        self.assertContains(page, "Сиз «керак эмас» деганлар")
        self.assertContains(page, "Ортга қайтариш")
        # бир хил танлов қайта юборилса — ўзгариш йўқ
        self.assertEqual(self.client.post(pick, {"what": "role", "key": keys, "role": "unknown"}).status_code, 400)
        self.assertTrue(self.client.post(pick, {"what": "role", "key": keys[:1], "role": ""}).json()["ok"])
        batch.refresh_from_db()
        self.assertNotIn(keys[0], batch.roles)
        self.client.post(pick, {"what": "undo"})
        batch.refresh_from_db()
        self.assertEqual(batch.roles.get(keys[0]), "unknown")
        self.assertEqual(self.client.post(pick, {"what": "role", "key": ["нет:такого"], "role": "local"}).status_code, 400)

    def test_role_change_rereads_only_that_file(self):
        """Битта листни белгилаш — фақат ўша файл қайта ўқилади, қолганлари кешдан."""
        from unittest import mock
        from . import services
        self.client.post(reverse("nf_mode"), {"nf_mode": "auto"})
        from core.tests.test_batch import OBJ_LOCAL, OBJ_RES
        files = [SimpleUploadedFile(Path(p).name, Path(p).read_bytes()) for p in (OBJ_LOCAL, OBJ_RES)]
        self.client.post(reverse("index"), {"files": files})
        batch = Batch.objects.latest("created")
        before = {s["key"]: (s["role"], s.get("rows")) for s in batch.sheets}
        s0 = next(s for s in batch.sheets if s["file"] == 0)
        real = services.lean_load
        with mock.patch.object(services, "lean_load", side_effect=real) as m:
            self.client.post(reverse("batch_pick", args=[batch.id]), {"what": "role", "key": s0["key"], "role": s0["role"]})
        opened = {Path(str(c.args[0])).name for c in m.call_args_list}
        self.assertEqual(opened, {Path(batch.files[0]["path"]).name})
        batch.refresh_from_db()
        self.assertEqual({s["key"]: (s["role"], s.get("rows")) for s in batch.sheets}, before)

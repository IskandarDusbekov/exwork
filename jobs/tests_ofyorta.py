"""Папка офёртаси: тартиб (папкалар дарахти), сариқ сарлавҳа, ҳар смета ўз сводкаси билан, умумий жами."""
import json
import re
import shutil
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from accounts.models import User
from core.svodka import make_evaluator
from core.tests.conftest import build_local, build_res

from . import ofyorta
from .models import Batch, Job, Project, ProjectOfyorta
from .services import recompute

TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_of_")
YELLOW = "FFFFFF00"


@override_settings(MEDIA_ROOT=TMP_MEDIA, NARXLASH_TASKS="worker")
class ProjectOfyortaTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = Path(tempfile.mkdtemp(prefix="narxlash_of_src_"))
        cls.local = build_local(cls.src / "Локал.xlsx")
        cls.res = build_res(cls.src / "Ресурс.xlsx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src, ignore_errors=True)
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user("of@test.uz", "Kuchli-parol-2026", first_name="O", last_name="F")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Нурафшон МТТ", status=Project.Status.DONE)
        self.jobs = {}
        # папкалар атайлаб аралаш: «10.» «2.» дан кейин, ички папкалар ҳам бор
        for folder, percents in (("2. Блок-А/2. Надземный", {}), ("10. Благоустройство", {"prochie": 0.1}),
                                 ("2. Блок-А/1. Подземный", {}), ("1. Архитектура", {})):
            batch = Batch.objects.create(owner=self.user, name=folder, project=self.project, folder=folder)
            job = Job.objects.create(owner=self.user, batch=batch, batch_label=folder.split("/")[-1],
                                     local_name=f"{folder.split('/')[-1]}.xlsx", res_name="r.xlsx",
                                     local_path="in/local.xlsx", res_path="in/res.xlsx", percents=percents,
                                     local_sheet="202611450_БВ", res_sheet="202611450_БР")
            job.abs("in").mkdir(parents=True, exist_ok=True)
            shutil.copy(self.local, job.abs(job.local_path))
            shutil.copy(self.res, job.abs(job.res_path))
            recompute(job)
            job.status = Job.Status.DONE
            job.save()
            self.jobs[folder] = job
        self.assertTrue(all(j.output_path and j.sum_total for j in self.jobs.values()),
                        [(j.status, j.error[:200], j.output_path, j.sum_total, (j.result or {}).get("totals")) for j in self.jobs.values()])

    def _folders(self, doc):
        by_id = {str(j.pk): f for f, j in self.jobs.items()}
        return [by_id[p["job"]] for p in doc.parts]

    def _build(self, parts=None, name="Нурафшон МТТ"):
        doc = ofyorta.for_project(self.project)
        parts = parts if parts is not None else doc.parts
        r = self.client.post(reverse("project_ofyorta_save", args=[self.project.pk]),
                             {"parts": json.dumps(parts), "name": name, "action": "build"})
        self.assertRedirects(r, reverse("project_ofyorta", args=[self.project.pk]), fetch_redirect_response=False)
        doc.refresh_from_db()
        self.assertEqual(doc.status, ProjectOfyorta.Status.BUILDING)
        ofyorta.make(doc)  # worker ўрнига
        doc.refresh_from_db()
        self.assertEqual(doc.status, ProjectOfyorta.Status.READY, doc.error)
        return doc

    def _read(self, doc):
        ws = load_workbook(self.project.abs(doc.file)).active
        written = {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}
        ev = make_evaluator(written, lambda a: None)
        # сметалар орасида — бўш сариқ қатор (биринчисидан олдин йўқ); остида сметанинг ўз қатори, бўш жойсиз
        fill = lambda r: ws.cell(r, 1).fill.fgColor.rgb if ws.cell(r, 1).fill.fill_type else None  # noqa: E731
        yellow = [r for r in range(1, ws.max_row + 1) if fill(r) == YELLOW]
        self.assertFalse([r for r in yellow if ws.cell(r, 1).value], "сариқ қатор ичида сўз бўлмасин")
        self.assertFalse([r for r in yellow if ws.cell(r - 1, 3).value is None], "сводка билан сариқ орасида бўш жой")
        self.assertFalse([r for r in yellow if not any(ws.cell(r + 1, c).value for c in range(1, 9))],
                         "сариқ остида бўш қатор")
        nds = [ev.value(f"H{r}") for r in range(1, ws.max_row + 1)
               if str(ws.cell(r, 3).value or "").startswith("Всего с НДС")]
        return ws, ev, yellow, nds

    def test_default_order_follows_folder_numbers_and_tree(self):
        doc = ofyorta.for_project(self.project)
        self.assertEqual(self._folders(doc), ["1. Архитектура", "2. Блок-А/1. Подземный", "2. Блок-А/2. Надземный",
                                              "10. Благоустройство"])
        nodes = ofyorta.tree(doc.parts, {str(j.pk): j for j in self.jobs.values()})
        self.assertEqual([n["name"] for n in nodes], ["1. Архитектура", "2. Блок-А", "10. Благоустройство"])
        self.assertEqual([c["name"] for c in nodes[1]["children"]], ["1. Подземный", "2. Надземный"])
        page = self.client.get(reverse("project_ofyorta", args=[self.project.pk]))
        self.assertContains(page, "Офёрта тузиш")
        self.assertContains(page, 'data-folder-check')
        self.assertContains(self.client.get(reverse("project", args=[self.project.pk])),
                            reverse("project_ofyorta", args=[self.project.pk]))

    def test_build_in_users_order_each_with_own_svodka_and_grand_total(self):
        doc = ofyorta.for_project(self.project)
        by_folder = dict(zip(self._folders(doc), doc.parts))
        parts = [dict(by_folder["10. Благоустройство"], title="Ободонлаштириш"),
                 by_folder["2. Блок-А/2. Надземный"], by_folder["2. Блок-А/1. Подземный"],
                 dict(by_folder["1. Архитектура"], include=False)]
        doc = self._build(parts)
        # тартиб сақланди (Благоустройство — бошида, Блок-А ичида Надземный — биринчи), киритилмагани йўқ
        self.assertEqual(self._folders(doc), ["10. Благоустройство", "2. Блок-А/2. Надземный",
                                              "2. Блок-А/1. Подземный", "1. Архитектура"])
        ws, ev, yellow, nds = self._read(doc)
        self.assertEqual(len(yellow), 3)  # 3 смета: орасида 2 та + умумий сводка олдида 1 та
        self.assertEqual(ws.cell(yellow[-1] + 1, 1).value, "УМУМИЙ СВОДКА (3 та смета)")
        grand = range(yellow[-1] + 2, ws.max_row + 1)  # умумий сводка қаторлари — ҳаммаси қалин
        self.assertEqual([r for r in grand if not (ws.cell(r, 3).font.b and ws.cell(r, 8).font.b)], [])
        # ўзимиз қўшадиган сарлавҳа йўқ — сметанинг ўз сарлавҳаси қолади (икки марта чиқмасин)
        self.assertFalse(any("Ободонлаштириш" in str(c.value) for row in ws.iter_rows() for c in row if c.value))
        expected = [self.jobs[f].sum_total for f in ("10. Благоустройство", "2. Блок-А/2. Надземный", "2. Блок-А/1. Подземный")]
        for got, want in zip(nds, expected):  # ҳар блокнинг ўз сводкаси = нархлашдаги сумма
            self.assertAlmostEqual(got, want, places=2)
        self.assertNotAlmostEqual(expected[0], expected[1], places=0)  # фоизи бошқа — сводкаси ҳам ўзиники
        self.assertAlmostEqual(nds[-1], sum(expected), places=2)  # умумий жами
        self.assertAlmostEqual(doc.total, sum(expected), places=2)
        # натижа файлда сақланган — Excel ҳисобламайдиган кўрувчида ҳам рақам кўринади
        stored = load_workbook(self.project.abs(doc.file), data_only=True).active
        vals = [stored.cell(r, 8).value for r in range(1, stored.max_row + 1)
                if str(stored.cell(r, 3).value or "").startswith("Всего с НДС")]
        for got, want in zip(vals, expected + [sum(expected)]):
            self.assertAlmostEqual(got, want, places=2)
        self.assertEqual(ws.freeze_panes[0], "A")
        # тепаси — биринчи сметанинг ўз тепаси ўзгаришсиз (қурилиш номи … жадвал сарлавҳаси, 1 2 3 … қатори)
        first_job = self.jobs["10. Благоустройство"]
        src = load_workbook(first_job.abs(first_job.output_path))[first_job.local_sheet]
        head = ofyorta.numbering_row(src, first_job.result["start_row"])
        self.assertEqual([[ws.cell(r, c).value for c in range(1, 9)] for r in range(1, head + 1)],
                         [[src.cell(r, c).value for c in range(1, 9)] for r in range(1, head + 1)])
        self.assertEqual([ws.cell(head, c).value for c in (1, 2, 3)], [1, 2, 3])
        r = self.client.get(reverse("project_ofyorta_file", args=[self.project.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        r.close()

    def test_formulas_follow_moved_rows(self):
        """Блок пастга сурилганда формулалар ўз қаторига боғланади: H = F × G, сумма — ўз блоки."""
        doc = self._build()
        ws, ev, yellow, nds = self._read(doc)
        second = yellow[0]  # иккинчи смета олдидаги сариқ қатор
        sums = [r for r in range(second, ws.max_row + 1) if str(ws.cell(r, 8).value or "").startswith("=SUM(H")]
        self.assertTrue(sums)
        top = int(re.match(r"=SUM\(H(\d+):", ws.cell(sums[0], 8).value)[1])
        self.assertGreater(top, second)  # сумма — ўз блокидан: сариқ ва олдинги сводка қўшилмайди
        self.assertEqual(ws.cell(sums[0] + 1, 8).value, f"=I{sums[0]}")  # сводка «Зарплата» = шу блок сумма қатори

    def test_new_priced_job_appended_into_its_folder_and_bad_input_ignored(self):
        doc = ofyorta.for_project(self.project)
        first = doc.parts[0]
        r = self.client.post(reverse("project_ofyorta_save", args=[self.project.pk]),
                             {"parts": json.dumps([{"job": first["job"], "title": "  Янги   ном ", "include": True},
                                                   {"job": "00000000-0000-0000-0000-000000000000", "title": "x"}]),
                              "name": "", "action": "save"})
        self.assertEqual(r.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.parts[0]["title"], "Янги ном")
        self.assertEqual(len(doc.parts), 4)  # бегона id олинмади, қолганлари қайтиб қўшилди
        self.assertEqual(doc.name, self.project.name)

    def test_only_owner_can_change_or_download(self):
        other = User.objects.create_user("boshqa@test.uz", "Kuchli-parol-2026")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("project_ofyorta", args=[self.project.pk])).status_code, 404)
        r = self.client.post(reverse("project_ofyorta_save", args=[self.project.pk]), {"parts": "[]", "action": "build"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.get(reverse("project_ofyorta_file", args=[self.project.pk])).status_code, 404)

    def test_nothing_selected(self):
        doc = ofyorta.for_project(self.project)
        parts = [dict(p, include=False) for p in doc.parts]
        r = self.client.post(reverse("project_ofyorta_save", args=[self.project.pk]),
                             {"parts": json.dumps(parts), "name": "X", "action": "build"}, follow=True)
        self.assertContains(r, "Камида битта сметани белгиланг")
        doc.refresh_from_db()
        self.assertEqual(doc.status, ProjectOfyorta.Status.DRAFT)

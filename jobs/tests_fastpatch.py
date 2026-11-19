"""Смета­чи ўзгариши Excel'нинг ўзида (``fastpatch``): ҳар бир амалдан кейин натижа тўлиқ қайта нархлаш билан
катакма-катак бир хил — нарх, бекор қилиш, L/M, «ТОПИЛМАДИ»ни олиш / сариқ / қайтариш, сводка фоизлари."""
import json
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from accounts.models import User
from core.pipeline import UNPRICED_STATUSES
from core.tests.conftest import build_local, build_res
from core.tests.test_smart import TESTS

from .fastpatch import KIT_NAME
from .models import Batch, Job, Project
from .services import recompute

TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_fastpatch_")
ROW_KEYS = ("row", "g", "h", "status", "manual", "orig_status", "note", "kind", "category", "override",
            "cleared", "marked", "suggested_category", "suggest_reason", "suggested_price")
RESULT_KEYS = ("stats", "problems", "warnings", "checks", "prices", "totals", "categories", "cleared", "marked", "percents")


def _cells(path, sheet, data_only):
    ws = load_workbook(path, data_only=data_only)[sheet]
    return {c.coordinate: c.value for row in ws.iter_rows() for c in row if c.value is not None}


def _log(path):
    return {row[0]: (row[3], row[9], row[10]) for row in load_workbook(path)["LOG"].iter_rows(min_row=2, values_only=True)}


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class FastPatchTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = Path(tempfile.mkdtemp(prefix="narxlash_fastpatch_src_"))
        cls.local = build_local(cls.src / "Локал.xlsx")
        cls.res = build_res(cls.src / "Ресурс.xlsx")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.src, ignore_errors=True)
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user("patch@test.uz", "Kuchli-parol-2026", first_name="P", last_name="T")
        self.client.force_login(self.user)

    def _job(self, local, res, local_sheet, res_sheet):
        project = Project.objects.create(owner=self.user, name="Объект", status=Project.Status.DONE)
        batch = Batch.objects.create(owner=self.user, name="1", project=project, folder="1")
        job = Job.objects.create(owner=self.user, batch=batch, batch_label="1", local_name="l.xlsx", res_name="r.xlsx",
                                 local_path="in/local.xlsx", res_path="in/res.xlsx", local_sheet=local_sheet,
                                 res_sheet=res_sheet, mode="smart", auto_cleared=True)
        job.abs("in").mkdir(parents=True, exist_ok=True)
        shutil.copy(local, job.abs(job.local_path))
        shutil.copy(res, job.abs(job.res_path))
        recompute(job)
        job.save()
        self.assertEqual(job.status, Job.Status.DONE, job.error)
        self.assertTrue(job.abs(KIT_NAME).is_file())
        return job

    def _bolnitsa(self):
        d = TESTS / "excel_calc/bolnitsa7_kr_blok2"
        return self._job(d / "input_local.xlsx", d / "input_res.xlsx", "LRV", "RES")

    def _post(self, job, name, payload):
        r = self.client.post(reverse(name, args=[job.id]), json.dumps(payload), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertTrue(r.json().get("patched"), r.json())  # навбатсиз — Excel'нинг ўзида
        return r.json()

    def assertSameAsFull(self, job):
        """Ямалган натижа (файл ва саҳифа маълумоти) — шу созламалар билан тўлиқ қайта нархлаш натижаси."""
        job.refresh_from_db()
        self.assertFalse(job.recalc_pending)
        sheet, out = job.local_sheet, job.abs(job.output_path)
        patched = deepcopy(job.result)
        values, cached, log = _cells(out, sheet, False), _cells(out, sheet, True), _log(out)
        full = recompute(Job.objects.get(pk=job.pk))
        for key in RESULT_KEYS:
            self.assertEqual(patched.get(key), full.result.get(key), key)
        self.assertEqual([{k: x.get(k) for k in ROW_KEYS} for x in patched["rows"]],
                         [{k: x.get(k) for k in ROW_KEYS} for x in full.result["rows"]])
        out2 = full.abs(full.output_path)
        self.assertEqual(values, _cells(out2, sheet, False))
        want = _cells(out2, sheet, True)
        self.assertEqual(cached.keys(), want.keys())
        for addr, v in want.items():
            self.assertEqual(cached[addr], pytest.approx(v) if isinstance(v, float) else v, addr)
        self.assertEqual(log, _log(out2))
        full.save()  # кейинги амал — тўлиқ ҳисоб тўплами билан ҳам ишлайди

    def test_prices_set_accept_and_cancel(self):
        job = self._bolnitsa()
        red = [r["row"] for r in job.result["rows"] if r.get("suggested_price") is not None]
        self._post(job, "job_prices", {"rows": red[:4], "action": "accept"})
        self._post(job, "job_prices", {"rows": red[4:6], "action": "set", "price": "987 654 321"})
        self._post(job, "job_prices", {"rows": [red[0], red[4]], "action": "clear"})  # бекор — асл ҳолатга
        self.assertSameAsFull(job)
        exact = next(r["row"] for r in job.result["rows"] if r["status"] == "EXACT")
        self._post(job, "job_prices", {"rows": [exact], "action": "set", "price": "5"})  # аниқ топилганни ҳам
        self._post(job, "job_prices", {"rows": [exact], "action": "clear"})
        self.assertSameAsFull(job)

    def test_categories_move_and_back(self):
        job = self._bolnitsa()
        rows = [r["row"] for r in job.result["rows"] if r.get("status") == "EXACT" and r.get("category") == "K"][:3]
        self._post(job, "job_categories", {"rows": rows[:2], "category": "L"})
        self._post(job, "job_categories", {"rows": rows[2:], "category": "M"})
        self.assertSameAsFull(job)
        self._post(job, "job_categories", {"rows": rows[:1], "category": ""})  # асл ҳолига
        self._post(job, "job_categories", {"rows": rows[1:2], "category": "M"})  # L → M
        self.assertSameAsFull(job)

    def test_not_found_clear_mark_restore_and_price(self):
        job = self._job(self.local, self.res, "202611450_БВ", "202611450_БР")
        nf = [r["row"] for r in job.result["rows"] if r["status"] in UNPRICED_STATUSES]
        self.assertGreaterEqual(len(nf), 2)
        self._post(job, "job_not_found", {"rows": nf, "action": "clear"})
        self.assertSameAsFull(job)
        self._post(job, "job_not_found", {"rows": nf[:1], "action": "mark"})
        self._post(job, "job_not_found", {"rows": nf[1:2], "action": "restore"})
        self.assertSameAsFull(job)
        with_unit = [r["row"] for r in job.result["rows"] if r["row"] in nf and r.get("cleared") and r["status"] != "CLEARED"]
        self._post(job, "job_prices", {"rows": with_unit[:1], "action": "set", "price": "1 000"})
        self.assertSameAsFull(job)
        self._post(job, "job_not_found", {"rows": nf, "action": "restore"})
        self.assertSameAsFull(job)

    def test_group_price_goes_to_review_queue_not_base(self):
        """Ресурс бўйича нарх: гуруҳнинг ҳамма қаторига ёзилади; «базага ҳам сақлаш» — фақат админ навбатига."""
        from narxbaza.models import Intake, IntakeRow, Price
        from .views import _has_unit, _not_found_stage
        job = self._job(self.local, self.res, "202611450_БВ", "202611450_БР")
        nf = _not_found_stage(deepcopy(job.result["rows"]))
        self.assertTrue(nf["groups"])
        self.assertEqual(sum(g["count"] for g in nf["groups"]), sum(1 for r in nf["rows"] if _has_unit(r)))
        g = nf["groups"][0]
        r = self._post(job, "job_prices", {"rows": g["rows"], "action": "set", "price": "12 500", "to_base": True})
        self.assertEqual(r["to_base"], g["count"])
        job.refresh_from_db()
        rows = {x["row"]: x for x in job.result["rows"]}
        self.assertTrue(all(rows[x]["status"] == "MANUAL" and rows[x]["g"] == 12500 for x in g["rows"]))
        it = Intake.objects.get(kind="manual", work_id=job.pk, status=Intake.Status.OPEN)
        self.assertEqual(sorted(it.rows.values_list("row", flat=True)), sorted(g["rows"]))
        self.assertTrue(all(x.state == IntakeRow.State.NEW and x.src_status == "MANUAL" for x in it.rows.all()))
        self.assertFalse(Price.objects.exists())  # базага тўғридан-тўғри ҳеч нарса тушмади
        # қайта юборилса — шу қаторлар янгиланади, такрорланмайди; белгисиз — навбатга тушмайди
        self._post(job, "job_prices", {"rows": g["rows"], "action": "set", "price": "13 000", "to_base": True})
        self.assertEqual(it.rows.count(), g["count"])
        self.assertEqual({float(p) for p in it.rows.values_list("price", flat=True)}, {13000.0})
        r = self._post(job, "job_prices", {"rows": g["rows"], "action": "set", "price": "14 000"})
        self.assertEqual(r["to_base"], 0)
        self.assertEqual({float(p) for p in it.rows.values_list("price", flat=True)}, {13000.0})
        self.assertSameAsFull(job)

    def test_grid_loads_in_parts(self):
        """Катта смета: саҳифада жадвалнинг боши, қолгани ``job_grid`` бўлакларида — бирга тўлиқ жадвалнинг ўзи."""
        import re
        from unittest import mock
        from . import views
        job = self._bolnitsa()
        rows = [str(r["row"]) for r in job.result["rows"]]
        for view in ("xl_out", "local"):
            with mock.patch.object(views, "GRID_FIRST", 7), mock.patch.object(views, "GRID_CHUNK", 11):
                page = self.client.get(reverse("job", args=[job.id]), {"view": view}).content.decode()
                body = re.search(r'<tbody id="rows-body"[^>]*data-next="(\d*)"[^>]*>(.*?)</tbody>', page, re.S)
                html, nxt, parts = body.group(2), body.group(1), 0
                while nxt:
                    d = self.client.get(reverse("job_grid", args=[job.id]), {"view": view, "from": nxt}).json()
                    html += d["html"]
                    nxt = "" if d["next"] is None else str(d["next"])
                    parts += 1
            got = re.findall(r'<tr data-row="(\d+)"', html)
            self.assertGreater(parts, 1)
            want = rows if view == "local" else [str(r["row"]) for r in job.result["rows"] if r.get("name")]
            self.assertEqual(sorted(got, key=int), sorted(want, key=int), view)  # ҳар қатор бир марта
            if view == "local":
                self.assertEqual(got, rows)  # смета тартибида
            else:
                self.assertIn('data-group-head="', html)  # RES бўлимлари бўйича, сарлавҳалари билан
        sel = re.search(r'id="grid-selectable"[^>]*>(.*?)</script>', page, re.S).group(1)
        self.assertEqual(set(json.loads(sel)), {r["row"] for r in job.result["rows"] if r.get("name")})

    def test_auto_choices_are_patched_not_priced_twice(self):
        """«Автоматик»: биринчи нархлашдан кейинги таклифлар (нарх, L/M) — иккинчи тўлиқ нархлаш эмас, Excel'нинг
        ўзида; натижа тўлиқ ҳисоб билан бир хил. Прогресс босқичлари ҳам айтилади."""
        from unittest import mock
        from . import services
        d = TESTS / "excel_calc/bolnitsa7_kr_blok2"
        project = Project.objects.create(owner=self.user, name="Объект", status=Project.Status.DONE)
        batch = Batch.objects.create(owner=self.user, name="1", project=project, folder="1")
        job = Job.objects.create(owner=self.user, batch=batch, batch_label="1", local_name="l.xlsx", res_name="r.xlsx",
                                 local_path="in/local.xlsx", res_path="in/res.xlsx", local_sheet="LRV",
                                 res_sheet="RES", mode="smart", auto_cleared=False)
        job.abs("in").mkdir(parents=True, exist_ok=True)
        shutil.copy(d / "input_local.xlsx", job.abs(job.local_path))
        shutil.copy(d / "input_res.xlsx", job.abs(job.res_path))
        phases = []
        job.progress_cb = lambda ph, done, total: phases.append(ph)
        with mock.patch.object(services, "run", wraps=services.run) as run:
            services.recompute(job)
        job.save()
        self.assertEqual(job.status, Job.Status.DONE, job.error)
        self.assertEqual(run.call_count, 1)  # иккинчи марта нархланмади
        self.assertTrue(job.categories or job.prices)
        self.assertEqual([p for p in ("read", "price", "write", "save", "auto") if p in phases],
                         ["read", "price", "write", "save", "auto"])
        self.assertSameAsFull(job)

    def test_percents(self):
        job = self._bolnitsa()
        self._post(job, "job_percents", {"pct": {"s_nds": "15", "prochie": "10"}})
        self.assertSameAsFull(job)
        self._post(job, "job_percents", {"reset": True})
        self.assertSameAsFull(job)

    def test_price_then_move_to_equipment(self):
        job = self._bolnitsa()
        red = [r["row"] for r in job.result["rows"] if r.get("suggested_price") is not None]
        self._post(job, "job_prices", {"rows": red[:2], "action": "accept"})
        self._post(job, "job_categories", {"rows": red[:2], "category": "M"})
        self._post(job, "job_percents", {"pct": {"s_nds": "20"}})
        self.assertSameAsFull(job)

    def test_falls_back_to_full_recalc_without_kit(self):
        job = self._bolnitsa()
        job.abs(KIT_NAME).unlink()
        red = [r["row"] for r in job.result["rows"] if r.get("suggested_price") is not None]
        r = self.client.post(reverse("job_prices", args=[job.id]), json.dumps({"rows": red[:1], "action": "accept"}),
                             content_type="application/json").json()
        self.assertEqual((r["queued"], r["patched"]), (True, False))
        job.refresh_from_db()
        self.assertEqual(job.result["stats"]["MANUAL"], 1)  # тестда навбат шу ерда бажарилади
        self.assertTrue(job.abs(KIT_NAME).is_file())  # тўлиқ ҳисоб тўпламни қайта ёзди


def test_nf_groups_by_name_and_unit():
    """«КОЛЕНО…» 3 та қаторда (ёзилиши бир оз бошқача) — битта ресурс; бирлиги бошқа — алоҳида."""
    from .views import _nf_groups
    near = {"name": "Колено 25", "price": 900, "unit": "шт", "score": 0.9, "same_unit": True}
    rows = [
        {"row": 10, "name": "КОЛЕНО PP-R ДН-25", "unit": "шт", "code": "К1", "qty": 2, "near": near},
        {"row": 11, "name": "колено  pp-r дн-25", "unit": "ШТ", "code": "К2", "qty": 3, "near": near},
        {"row": 12, "name": "КОЛЕНО PP-R ДН-25", "unit": "шт", "code": "К1", "qty": None, "near": dict(near, price=950)},
        {"row": 13, "name": "КОЛЕНО PP-R ДН-25", "unit": "м", "code": "", "qty": 1, "near": None},
    ]
    gs = _nf_groups(rows)
    assert [g["rows"] for g in gs] == [[10, 11, 12], [13]]
    g = gs[0]
    assert g["count"] == 3 and g["qty"] == 5 and g["codes"] == ["К1", "К2"]
    assert g["near"] is None and g["some_near"] == 3  # таклифлар ҳар хил — гуруҳга битта таклиф берилмайди
    assert _nf_groups(rows[:2])[0]["near"]["price"] == 900


def test_step_progress_text_round_trip():
    """Нархлаш жараёни: Batch.step'даги «… — қаторлар нархланмоқда 12 250 / 20 859 қатор» қайта ўқилади."""
    from .services import parse_step_tail
    head = "1/2 смета нархланяпти: Локалка"
    assert parse_step_tail(head) == {"text": head, "phase": "", "done": 0, "total": 0}
    got = parse_step_tail(head + " — қаторлар нархланмоқда 12 250 / 20 859 қатор")
    assert got == {"text": head, "phase": "price", "done": 12250, "total": 20859}
    got = parse_step_tail(head + " — Excel'га ёзилмоқда · 20 859 қатор")
    assert (got["phase"], got["done"], got["total"]) == ("write", 0, 20859)  # сонсиз — «қолди 0» эмас
    got = parse_step_tail(head + " — Excel'га ёзилмоқда 5 000 / 20 855 қатор")
    assert (got["phase"], got["done"], got["total"]) == ("write", 5000, 20855)


def test_step_reporter_switch_is_not_throttled(monkeypatch):
    """Бир босқич ичида сонлидан сонсизга ўтиш дарҳол ёзилади (охирги сон қотиб қолмасин)."""
    from . import services
    seen = []
    monkeypatch.setattr(services.Batch.objects, "filter", lambda **k: type("Q", (), {"update": lambda s, step, **_: seen.append(step)})())
    rep = services.step_reporter(1, "1/1 смета нархланяпти: A", every=60)
    rep("write", 0, 0)
    rep("write", 500, 20855)
    rep("write", 1000, 20855)  # шу сония ичида — ташланади
    rep("write", 0, 0)
    assert [x.split(" — ")[-1] for x in seen] == ["Excel'га ёзилмоқда", "Excel'га ёзилмоқда 500 / 20 855 қатор",
                                                 "Excel'га ёзилмоқда · 20 855 қатор"]


def test_replace_file_waits_for_reader(tmp_path, monkeypatch):
    """Windows: файлни кимдир ўқиётган бўлса алмаштириш рад этилади — бир оз кутиб қайта уринилади."""
    import os
    from . import services
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("new")
    b.write_text("old")
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("busy")
        real(src, dst)
    monkeypatch.setattr(services.os, "replace", flaky)
    services.replace_file(a, b, wait=5)
    assert b.read_text() == "new" and len(calls) == 3


def test_lm_fuzzy_lookup_cached():
    """Бир ном юзлаб қаторда: ўхшаш номни қидириш бир марта, жавоб ҳар сафар бир хил."""
    from unittest import mock
    from core import lmbase
    base = lmbase.LMBase([{"key": "насос центробежный консольный км 80", "category": "M", "reason": "r", "strong": True}])
    with mock.patch.object(lmbase.process, "extractOne", wraps=lmbase.process.extractOne) as one:
        hits = [base.lookup("Насос центробежный консольный КМ-80-50") for _ in range(50)]
    assert one.call_count == 1
    assert all(h and h.kind == lmbase.EQUIPMENT and h.how == "fuzzy" for h in hits)


def test_lean_load_same_values_and_merges(tmp_path):
    """Тез очиш (таҳлил учун): қийматлар ва бирлашмалар ҳар доимгидек, фақат чегаралар тикланмайди."""
    from openpyxl import Workbook, load_workbook
    from openpyxl.cell.cell import MergedCell
    from core.xlfast import lean_load
    wb = Workbook()
    ws = wb.active
    for r in range(1, 30):
        ws.cell(r, 1, f"ном {r}")
        ws.cell(r, 4, r * 1.5)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
    path = tmp_path / "m.xlsx"
    wb.save(path)
    a, b = load_workbook(path), lean_load(path)
    cells = lambda w: [(c.coordinate, c.value, isinstance(c, MergedCell)) for row in w.active.iter_rows() for c in row]
    assert cells(a) == cells(b)
    assert sorted(map(str, a.active.merged_cells.ranges)) == sorted(map(str, b.active.merged_cells.ranges))
    assert load_workbook(path).active["B1"].border is not None  # одатий очиш ўзгармади

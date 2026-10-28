"""Навбат ва worker: эгаллаш (lease), эскирган ишни қайта олиш, ``run_worker --once`` билан бутун папка."""
import io
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from . import folders
from .models import Batch, Job, Project, Worker

TMP_MEDIA = tempfile.mkdtemp(prefix="narxlash_worker_media_")
GOLDEN = Path(__file__).resolve().parent.parent / "core/tests/golden/202611450"


@override_settings(MEDIA_ROOT=TMP_MEDIA, NARXLASH_TASKS="worker")
class WorkerQueueTests(TransactionTestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user("ali@test.uz", "Kuchli-parol-2026", first_name="Ali", last_name="V")
        self.client.force_login(self.user)

    def _project(self, **kw):
        return Project.objects.create(owner=self.user, name="P", status=Project.Status.PROCESSING, **kw)

    def test_claim_is_exclusive(self):
        p = self._project()
        self.assertEqual(folders.claim_next("w1"), p.id)
        self.assertIsNone(folders.claim_next("w2"))  # бошқаси ололмайди
        p.refresh_from_db()
        self.assertEqual(p.worker, "w1")
        self.assertTrue(folders.is_working(p))

    def test_dead_worker_lease_is_taken_over(self):
        p = self._project(worker="dead:1#0", heartbeat=timezone.now() - folders.STALE_AFTER - timedelta(seconds=1))
        self.assertFalse(folders.is_working(p))
        self.assertEqual(folders.claim_next("w2"), p.id)
        fresh = self._project(worker="alive:1#0", heartbeat=timezone.now())
        self.assertIsNone(folders.claim_next("w3"))  # тирик worker'ники — тегилмайди
        self.assertTrue(folders.is_working(fresh))

    def test_finished_or_other_status_not_claimed(self):
        Project.objects.create(owner=self.user, name="D", status=Project.Status.DONE)
        self.assertIsNone(folders.claim_next("w1"))

    def test_start_does_not_spawn_thread_in_worker_mode(self):
        p = self._project()
        with mock.patch.object(folders.threading, "Thread") as thread:
            folders.start(p)
        thread.assert_not_called()

    def test_stop_waits_for_active_worker(self):
        p = self._project(worker="w1", heartbeat=timezone.now())
        Batch.objects.create(owner=self.user, project=p, folder="A", name="A", stage=Batch.Stage.QUEUED)
        folders.stop_project(p)
        p.refresh_from_db()
        self.assertTrue(p.stop_requested)  # worker кейинги файлдан олдин ўзи тўхтайди
        self.assertEqual(p.parts.get().stage, Batch.Stage.QUEUED)
        idle = self._project()
        Batch.objects.create(owner=self.user, project=idle, folder="B", name="B", stage=Batch.Stage.QUEUED)
        folders.stop_project(idle)  # ҳеч ким ишламаяпти — дарҳол
        idle.refresh_from_db()
        self.assertFalse(idle.stop_requested)
        self.assertEqual(idle.parts.get().stage, Batch.Stage.STOPPED)

    def test_run_worker_processes_uploaded_folder(self):
        local, res = (GOLDEN / "input_local.xlsx").read_bytes(), (GOLDEN / "input_res.xlsx").read_bytes()
        files = {"МТТ/А/a_lokal.xlsx": local, "МТТ/А/a_res.xlsx": res,
                 "МТТ/Б/b_lokal.xlsx": local, "МТТ/Б/b_res.xlsx": res}
        r = self.client.post(reverse("project_create"), {
            "files": [SimpleUploadedFile(p.split("/")[-1], b) for p, b in files.items()], "paths": list(files)})
        self.assertEqual(r.status_code, 200, r.content)
        project = Project.objects.get()
        # сайт ўзи ишламайди — навбатда турибди
        self.assertEqual(project.status, Project.Status.PROCESSING)
        self.assertEqual({p.stage for p in project.parts.all()}, {Batch.Stage.QUEUED})

        out = io.StringIO()
        call_command("run_worker", once=True, threads=2, poll=0.1, stdout=out, stderr=io.StringIO())
        project.refresh_from_db()
        self.assertEqual({p.stage for p in project.parts.all()}, {Batch.Stage.READY}, out.getvalue())
        self.assertEqual(project.status, Project.Status.READY)
        self.assertEqual(project.worker, "")  # lease бўшатилди
        self.assertTrue(all(p.durations.get("converting") is not None for p in project.parts.all()))
        self.assertFalse(Worker.objects.exists())  # чиққанда рўйхатдан ўчди
        # ҳар бир файл очиш вақти ёзилди (.xlsx — ўгирилмайди)
        from .models import FileTiming
        timings = list(FileTiming.objects.filter(project=project))
        self.assertEqual(sorted(t.name for t in timings), sorted(p.split("/")[-1] for p in files))
        self.assertTrue(all(t.convert_s is None and t.read_s is not None and t.analyze_s is not None for t in timings))
        self.assertTrue(all(t.size and t.owner_id == self.user.pk and not t.error for t in timings))

        # «Ҳисоблашни бошлаш» → яна навбатга, worker нархлайди
        self.client.post(reverse("project_price", args=[project.id]))
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PROCESSING)
        call_command("run_worker", once=True, threads=1, poll=0.1, stdout=io.StringIO(), stderr=io.StringIO())
        project.refresh_from_db()
        self.assertEqual({p.stage for p in project.parts.all()}, {Batch.Stage.DONE})
        self.assertEqual(project.status, Project.Status.DONE)
        job = project.parts.first().jobs.first()
        self.assertTrue(job.rows and job.duration is not None)  # тезлик: қатор сони ва вақт


@override_settings(MEDIA_ROOT=TMP_MEDIA, NARXLASH_TASKS="thread")
class ThreadModeLeaseTests(TransactionTestCase):
    """Сайт ичидаги оқим ҳам lease олади: ёнида worker ҳам ишласа — битта папкани иккаласи олмайди."""

    def test_thread_skips_project_held_by_worker(self):
        user = User.objects.create_user("ali@test.uz", "Kuchli-parol-2026", first_name="A", last_name="V")
        p = Project.objects.create(owner=user, name="P", status=Project.Status.PROCESSING,
                                   worker="srv:1#0", heartbeat=timezone.now())
        with mock.patch.object(folders.threading, "Thread") as thread:
            folders.start(p)
        thread.assert_not_called()
        self.assertNotIn(p.id, folders._running)

    def test_thread_takes_lease(self):
        user = User.objects.create_user("ali@test.uz", "Kuchli-parol-2026", first_name="A", last_name="V")
        p = Project.objects.create(owner=user, name="P", status=Project.Status.PROCESSING)
        with mock.patch.object(folders.threading, "Thread") as thread:
            folders.start(p)
        thread.assert_called_once()
        folders._running.discard(p.id)
        p.refresh_from_db()
        self.assertTrue(p.worker.startswith("thread:"))
        self.assertIsNone(folders.claim_next("w1"))  # worker уни ололмайди


class UnpricedResTests(TransactionTestCase):
    """RES'да нарх умуман йўқ бўлса (ЦЕНА ҳамма жойда 0) — нархлашдан олдин айтилади."""

    def test_count_prices(self):
        from openpyxl import Workbook

        from .services import count_prices
        ws = Workbook().active
        for r, v in enumerate([None, 0, "0", "0,00", 0.0], start=2):
            ws.cell(r, 3, v)
        cols = {"price": 3, "start": 2, "end": 6}
        self.assertEqual(count_prices(ws, cols), 0)
        ws.cell(4, 3, "12 500,50")
        self.assertEqual(count_prices(ws, cols), 1)
        self.assertIsNone(count_prices(ws, {"name": 2}))  # нарх устуни аниқланмаган — номаълум

    def test_ready_stage_warns_before_pricing(self):
        part = Batch(pairs=[{"local": "0:L", "res": "0:R", "include": True, "label": "A"}],
                     sheets=[{"key": "0:L", "role": "local"}, {"key": "0:R", "role": "resource", "prices": 0}])
        stage, msg = folders.ready_stage(part)
        self.assertEqual(stage, Batch.Stage.REVIEW)
        self.assertIn("нарх йўқ", msg)
        part.sheets[1]["prices"] = 40
        self.assertEqual(folders.ready_stage(part), (Batch.Stage.READY, ""))
        part.sheets[1].pop("prices")  # эски таҳлил (маълумот йўқ) — огоҳлантирилмайди
        self.assertEqual(folders.ready_stage(part)[0], Batch.Stage.READY)


@override_settings(MEDIA_ROOT=TMP_MEDIA, NARXLASH_TASKS="worker")
class PartQueueTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user("ali@test.uz", "Kuchli-parol-2026", first_name="Ali", last_name="V")
        self.client.force_login(self.user)

    def test_part_confirm_goes_to_queue_not_request(self):
        local, res = (GOLDEN / "input_local.xlsx").read_bytes(), (GOLDEN / "input_res.xlsx").read_bytes()
        files = {"МТТ/А/a_lokal.xlsx": local, "МТТ/А/a_res.xlsx": res}
        self.client.post(reverse("project_create"), {
            "files": [SimpleUploadedFile(p.split("/")[-1], b) for p, b in files.items()], "paths": list(files)})
        call_command("run_worker", once=True, threads=1, poll=0.1, stdout=io.StringIO(), stderr=io.StringIO())
        project = Project.objects.get()
        part = project.parts.get()
        self.assertEqual(part.stage, Batch.Stage.READY)
        self.assertTrue(part.sheets and any(s.get("prices") for s in part.sheets))  # RES'да нарх бор

        with mock.patch("jobs.folders.price_part") as price_part:
            r = self.client.post(reverse("batch_confirm", args=[part.id]),
                                 {"include_0": "1", "res_0": part.pairs[0]["res"], "label_0": "Янги ном"})
        price_part.assert_not_called()  # сўров ичида нархланмайди
        self.assertRedirects(r, reverse("project", args=[project.id]), fetch_redirect_response=False)
        part.refresh_from_db()
        project.refresh_from_db()
        self.assertEqual((part.stage, project.status), (Batch.Stage.PRICING, Project.Status.PROCESSING))
        self.assertEqual(part.pairs[0]["label"], "Янги ном")

        call_command("run_worker", once=True, threads=1, poll=0.1, stdout=io.StringIO(), stderr=io.StringIO())
        part.refresh_from_db()
        self.assertEqual(part.stage, Batch.Stage.DONE)
        self.assertEqual(part.jobs.get().local_name, "Янги ном")

    def test_interrupted_part_is_requeued(self):
        project = Project.objects.create(owner=self.user, name="P", status=Project.Status.READY)
        Batch.objects.create(owner=self.user, project=project, folder="A", name="A", stage=Batch.Stage.PRICING)
        folders.ensure_running(project)  # иш узилган: қисм «нархланяпти», лекин ҳеч ким ишламаяпти
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.PROCESSING)
        self.assertEqual(folders.claim_next("w1"), project.id)


class DownloadNameTests(TransactionTestCase):
    def test_newline_in_label_is_removed(self):
        """Лист номида қатор ташланиши бўлса — Windows'да натижа файли яратилмасди (OSError)."""
        name = Job(batch_label="ДАННЫМ ЛОКАЛЬНЫХ СМЕТ \nНА ЦЕНТРАЛЬНЫЙ СКЛАД", local_name="x.xlsx").download_name
        self.assertEqual(name, "ДАННЫМ ЛОКАЛЬНЫХ СМЕТ НА ЦЕНТРАЛЬНЫЙ СКЛАД_narxlandi.xlsx")

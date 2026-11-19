"""«Тизим қандай ишлайди»: дарслардаги код ҳаволалари тирик, код фақат рухсат этилган файллардан, фақат суперфойдаланувчига."""
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User

from .tizim_lessons import LESSONS, code_refs
from .views_tizim import resolve

PASSWORD = "Kuchli-parol-2026"


def cache_clear():
    from django.core.cache import cache
    cache.clear()


class LessonDataTests(TestCase):
    def test_every_code_ref_points_to_real_code(self):
        """Функция ўчирилса ёки номи ўзгарса — шу тест йиқилади ва tizim_lessons.py янгиланади."""
        for ref in code_refs():
            _src, (start, end) = resolve(ref["f"], ref.get("fn", ""), ref.get("find", ""))
            self.assertIsNotNone(start, ref)
            self.assertLessEqual(start, end, ref)

    def test_flows_use_known_nodes(self):
        kinds = {"req", "res", "job", "data", "err"}
        for lesson in LESSONS:
            ids = {n["id"] for n in lesson["nodes"]}
            for step in lesson["steps"]:
                for a, b, kind in step.get("flow", []):
                    self.assertIn(a, ids, (lesson["id"], step["t"]))
                    self.assertIn(b, ids, (lesson["id"], step["t"]))
                    self.assertIn(kind, kinds)
                self.assertTrue(set(step.get("on", [])) <= ids, (lesson["id"], step["t"]))


@override_settings(NARXLASH_STAFF_2FA=False)
class PageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@test.uz", PASSWORD, first_name="A", last_name="B")
        self.staff = User.objects.create_user("staff@test.uz", PASSWORD, first_name="S", last_name="T", is_staff=True)
        self.user = User.objects.create_user("u@test.uz", PASSWORD, first_name="U", last_name="V")

    def test_only_superuser(self):
        for u, codes in ((self.user, {404}), (self.staff, {403})):
            self.client.force_login(u)
            for name in ("panel:tizim", "panel:tizim_live"):
                self.assertIn(self.client.get(reverse(name)).status_code, codes, (u.email, name))
            r = self.client.get(reverse("panel:tizim_code"), {"f": "core/pipeline.py"})
            self.assertIn(r.status_code, codes)

    def test_page_and_live(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("panel:tizim"))
        self.assertContains(r, 'id="tz-lessons"')
        self.assertContains(r, '"id": "pricing"')
        d = self.client.get(reverse("panel:tizim_live")).json()
        for key in ("requests", "server", "db", "cache", "lo", "nginx", "workers", "tasks", "alerts", "today"):
            self.assertIn(key, d)
        self.assertEqual(d["gunicorn"]["slots"], d["gunicorn"]["workers"] * d["gunicorn"]["threads"])
        self.assertEqual(len(d["requests"]["series"]), 60)
        self.assertEqual(set(d["tasks"]), {"running", "waiting"})
        for level, node, text in d["alerts"]:
            self.assertIn(level, {"bad", "warn", "info"})
            self.assertTrue(text)

    def test_monitor_records_requests(self):
        """Сўров вақти, база сўровлари ва функция номи ёзилади; шу саҳифанинг ўз сўрови ҳисобланмайди."""
        from django.core.cache import cache
        cache.clear()
        self.client.force_login(self.admin)
        self.client.get(reverse("panel:overview"))
        cache.delete("mon:live")
        rq = self.client.get(reverse("panel:tizim_live")).json()["requests"]
        hit = [r for r in rq["recent"] if r["path"] == reverse("panel:overview")]
        self.assertTrue(hit, rq["recent"])
        self.assertEqual(hit[0]["status"], 200)
        self.assertTrue(hit[0]["view"].startswith("panel."), hit[0]["view"])
        self.assertGreater(hit[0]["q"], 0)
        self.assertFalse([r for r in rq["recent"] if r["path"].startswith(reverse("panel:tizim_live"))])

    def test_task_shows_running_code(self):
        """Ишлаётган иш: қайси босқич (нархлаш), қайси смета, қатор прогресси ва шу босқич коди."""
        from jobs.models import Batch
        sheets = [{"key": "0:L", "file": 0, "sheet": "Локал"}, {"key": "1:R", "file": 1, "sheet": "Ресурс"}]
        pairs = [{"local": "0:L", "res": "1:R", "include": True, "label": "Смета А"},
                 {"local": "0:L", "res": "1:R", "include": True, "label": "Смета Б"}]
        Batch.objects.create(owner=self.user, name="Объект-1", pricing={}, worker="host:1#0", sheets=sheets, pairs=pairs,
                             files=[{"name": "лок.xls"}, {"name": "рес.xlsx"}],
                             step="2/2 смета нархланяпти: Смета Б — қаторлар нархланмоқда 10 / 40 қатор")
        cache_clear()
        self.client.force_login(self.admin)
        t = self.client.get(reverse("panel:tizim_live")).json()["tasks"]
        item = [x for x in t["running"] if x["name"] == "Объект-1"][0]
        self.assertEqual(item["user"], "u@test.uz")
        self.assertEqual(item["rows"], [10, 40])
        d = item["detail"]
        self.assertEqual(d["stages"][d["cur"]]["key"], "price")
        self.assertEqual([m["state"] for m in d["smetas"]], ["done", "now"])
        self.assertEqual(d["smetas"][1]["local"], "лок.xls")
        for st in d["stages"]:
            self.assertIsNotNone(resolve(st["code"]["f"], st["code"]["fn"])[1][0], st)

    def test_own_polling_is_not_user_activity(self):
        """Саҳифанинг ҳар 3 с сўрови «сайтда вақт» ва кириш журналига қўшилмайди."""
        from . import access
        from .models import LoginSession
        self.client.force_login(self.admin)
        cache_clear()
        before = LoginSession.objects.count()
        for _ in range(3):
            self.client.get(reverse("panel:tizim_live"))
        self.assertEqual(LoginSession.objects.count(), before)
        self.assertFalse([k for k in access._buf if "tizim/live" in str(k)])

    def test_code_view_shows_callers(self):
        self.client.force_login(self.admin)
        d = self.client.get(reverse("panel:tizim_code"), {"f": "jobs/folders.py", "fn": "create_project"}).json()
        self.assertTrue(any(c["f"] == "jobs/views.py" for c in d["callers"]), d["callers"])

    def test_code_view_marks_function(self):
        self.client.force_login(self.admin)
        d = self.client.get(reverse("panel:tizim_code"), {"f": "jobs/pricing.py", "fn": "claim_next"}).json()
        lines = d["src"].splitlines()
        self.assertTrue(lines[d["start"] - 1].startswith("def claim_next"))
        d = self.client.get(reverse("panel:tizim_code"), {"f": "jobs/management/commands/run_worker.py", "fn": "Command._loop"}).json()
        self.assertIn("def _loop", d["src"].splitlines()[d["start"] - 1])
        files = self.client.get(reverse("panel:tizim_code"), {"list": "1"}).json()["files"]
        self.assertIn("core/pipeline.py", files)
        self.assertFalse([f for f in files if ".env" in f or f.startswith(".")])

    def test_code_view_refuses_everything_else(self):
        self.client.force_login(self.admin)
        for bad in ("../manage.py", "manage.py", ".env", "web/../.env", "core/../../etc/passwd", "venv/Lib/site.py",
                    "/etc/passwd", "C:/Windows/win.ini", "jobs/.hidden.py", "media/x.xlsx", "core/tests/../../.env",
                    "deploy/secret.conf", "jobs\\..\\.env"):
            self.assertEqual(self.client.get(reverse("panel:tizim_code"), {"f": bad}).status_code, 404, bad)

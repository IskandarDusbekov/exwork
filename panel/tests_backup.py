"""Захира нусха: тиклаш синови, муаммолар ва огоҳлантириш хати, backup_check буйруғи, панел саҳифаси."""
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import User

from . import backup

PASSWORD = "Kuchli-parol-2026"
TMP = tempfile.mkdtemp(prefix="narxlash_backup_")


class _Sync:
    """threading.Thread ўрнига: фон иши шу заҳоти бажарилади (тест базасини кўрсин)."""

    def __init__(self, target, **kw):
        self.target = target

    def start(self):
        self.target()


@override_settings(NARXLASH_BACKUP_DIR=f"{TMP}/backups")
class RestoreTestTests(TransactionTestCase):
    """Нусха — тест транзакциясидан ташқарида (SQLite очиқ ёзув транзакциясини кутиб қолади)."""

    def setUp(self):
        shutil.rmtree(f"{TMP}/backups", ignore_errors=True)
        self.boss = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="B", last_name="A")

    def test_restore_test_of_fresh_dump_passes(self):
        backup.make()
        res = backup.restore_test()
        self.assertTrue(res["ok"], res.get("error"))
        self.assertGreater(res["tables"], 10)
        self.assertEqual(res["users"], 1)
        self.assertEqual(res["behind"], 0)
        self.assertEqual(backup.test_state()["file"], res["file"])
        codes = {c for c, _ in backup.problems()}
        self.assertNotIn("never_tested", codes)
        self.assertNotIn("stale", codes)
        self.assertFalse(list((backup.root() / "tmp").iterdir()))  # вақтинча нусха ўчирилди

    def test_broken_dump_fails_and_is_reported(self):
        d = backup.root() / "db"
        d.mkdir(parents=True)
        (d / "narxlash_2026-01-01_0300.sqlite3").write_bytes(b"bu baza emas" * 100)
        res = backup.restore_test()
        self.assertFalse(res["ok"])
        self.assertIn("SQLite", res["error"])
        self.assertIn("test_failed", {c for c, _ in backup.problems()})

    def test_panel_shows_status_and_runs_test(self):
        backup.make()
        self.client.force_login(self.boss)
        r = self.client.get(reverse("panel:backups"))
        self.assertContains(r, "Ҳали синалмаган")
        self.assertContains(r, "Нусхадан тиклаш ҳали бирор марта синалмаган")
        self.assertContains(r, "deploy/restore.sh")
        self.assertNotContains(r, "pg_restore --clean")
        with mock.patch.object(backup.threading, "Thread", _Sync):
            r = self.client.post(reverse("panel:backup_test"))
        self.assertRedirects(r, reverse("panel:backups"), fetch_redirect_response=False)
        self.assertTrue(backup.test_state()["ok"])
        self.assertContains(self.client.get(reverse("panel:backups")), "Ўтди")

    def test_only_superuser_can_run_test(self):
        viewer = User.objects.create_user("v@test.uz", PASSWORD, first_name="V", last_name="V", is_staff=True)
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(reverse("panel:backup_test")).status_code, 403)


@override_settings(NARXLASH_BACKUP_DIR=f"{TMP}/alerts")
class AlertTests(TestCase):
    def setUp(self):
        shutil.rmtree(f"{TMP}/alerts", ignore_errors=True)
        self.boss = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="B", last_name="A")
        User.objects.create_user("ali@test.uz", PASSWORD, first_name="A", last_name="V")

    def _dump(self, age_hours=0):
        d = backup.root() / "db"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "narxlash_2026-11-18_0315.dump"
        p.write_bytes(b"x")
        t = time.time() - age_hours * 3600
        os.utime(p, (t, t))
        return p

    def test_no_backups_mail_once_a_day_to_superusers(self):
        codes = {c for c, _ in backup.problems()}
        self.assertEqual(codes, {"no_dump", "never_tested"})
        self.assertTrue(backup.alert())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["boss@test.uz"])
        self.assertIn("cron", mail.outbox[0].body)
        self.assertFalse(backup.alert())  # шу куни — қайта юборилмайди
        self.assertEqual(len(mail.outbox), 1)

    def test_new_problem_sends_again_same_day(self):
        self._dump()
        backup._put(backup.TEST_KEY, {"ok": True, "time": time.time(), "file": "x"})
        self.assertEqual(backup.problems(), [])
        self.assertFalse(backup.alert())
        self._dump(age_hours=30)
        self.assertEqual([c for c, _ in backup.problems()], ["stale"])
        self.assertTrue(backup.alert())
        backup._put(backup.TEST_KEY, {"ok": True, "time": time.time() - 40 * 86400, "file": "x"})
        self.assertTrue(backup.alert())  # янги муаммо (test_old) — шу куни ҳам
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(DEBUG=True)
    def test_no_mail_in_development(self):
        self.assertFalse(backup.alert())
        self.assertEqual(mail.outbox, [])

    def test_backup_sh_failure_command(self):
        self._dump(age_hours=1)
        backup._put(backup.TEST_KEY, {"ok": True, "time": time.time(), "file": "x"})
        call_command("backup_check", "--failed", "backup.sh 31-qatorda to'xtadi", stdout=open(os.devnull, "w"))
        self.assertIn("failed", {c for c, _ in backup.problems()})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("31-qatorda", mail.outbox[0].body)
        self._dump()  # кейинги муваффақиятли нусха — хато эскирди
        os.utime(backup.root() / "db" / "narxlash_2026-11-18_0315.dump", (time.time() + 5, time.time() + 5))
        self.assertNotIn("failed", {c for c, _ in backup.problems()})

    def test_verify_allows_older_dump_but_not_missing_tables(self):
        tables = {t: 1 for t in backup._expected_tables()}
        tables["django_migrations"] = 1
        live = backup._live_migrations()
        self.assertEqual(backup._verify(tables, live)["behind"], 0)
        del tables["accounts_user"]
        with self.assertRaisesRegex(RuntimeError, "accounts_user"):
            backup._verify(tables, live)
        older = set(sorted(live)[1:])  # нусха битта миграция эскироқ — янги жадвал йўқлиги табиий
        self.assertEqual(backup._verify({**tables, "accounts_user": 1}, older)["behind"], 1)
        with self.assertRaisesRegex(RuntimeError, "миграциялар"):
            backup._verify({"x": 1}, set())


class PgEnvTests(TestCase):
    def test_password_goes_to_env_not_argv(self):
        env = backup.pg_env("postgres://nar%40x:p%27a%24s@127.0.0.1:5433/narx?sslmode=require")
        self.assertEqual((env["PGHOST"], env["PGPORT"], env["PGUSER"], env["PGPASSWORD"], env["PGDATABASE"],
                          env["PGSSLMODE"]), ("127.0.0.1", "5433", "nar@x", "p'a$s", "narx", "require"))

    @override_settings(DATABASE_URL="postgres://u:p@h:5432/narxlash")
    def test_test_database_name(self):
        with mock.patch.dict(os.environ, {"RESTORE_TEST_DATABASE_URL": ""}):
            self.assertEqual(backup.test_url(), "postgres://u:p@h:5432/narxlash_restore_test")
        with mock.patch.dict(os.environ, {"RESTORE_TEST_DATABASE_URL": "postgres://u:p@h/boshqa"}):
            self.assertEqual(backup.test_url(), "postgres://u:p@h/boshqa")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


@override_settings(MEDIA_ROOT=f"{TMP}/live_media", NARXLASH_BACKUP_DIR=f"{TMP}/media_bk")
class MediaDownloadTests(TestCase):
    def setUp(self):
        for d in ("live_media", "media_bk"):
            shutil.rmtree(f"{TMP}/{d}", ignore_errors=True)
        self.boss = User.objects.create_superuser("boss@test.uz", PASSWORD, first_name="B", last_name="A")
        snap = backup.root() / "media" / "2026-11-18"
        (snap / "projects" / "a").mkdir(parents=True)
        (snap / "projects" / "a" / "smeta.xlsx").write_bytes(b"x" * 1500)
        (snap / "Локал 3.xlsx").write_bytes(b"")
        os.link(snap / "projects" / "a" / "smeta.xlsx", snap / "projects" / "nusxa.xlsx")
        live = Path(f"{TMP}/live_media")
        live.mkdir(parents=True)
        (live / "yangi.txt").write_bytes(b"bugun")

    def _tar(self, data):
        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as t:
            return {m.name: (t.extractfile(m).read() if m.isreg() else ("link", m.linkname)) for m in t.getmembers()}

    def test_archive_opens_with_media_root_and_hardlinks(self):
        files = self._tar(b"".join(backup.media_tar(backup.media_dir("2026-11-18"), chunk=1000)))
        self.assertEqual(files["media/Локал 3.xlsx"], b"")
        # қаттиқ ҳавола: биринчи учрагани — маълумот, иккинчиси — унга ҳавола (жой икки марта кетмасин)
        self.assertEqual(files["media/projects/nusxa.xlsx"], b"x" * 1500)
        self.assertEqual(files["media/projects/a/smeta.xlsx"], ("link", "media/projects/nusxa.xlsx"))

    def test_download_needs_code_and_is_logged(self):
        from .models import Event
        self.client.force_login(self.boss)
        r = self.client.get(reverse("panel:backups"))
        self.assertContains(r, "media_2026-11-18.tar")
        self.assertContains(r, "Ҳозирги файллар")
        url = reverse("panel:backup_media_download", args=["2026-11-18"])
        r = self.client.post(url, {"code": "notogri"})
        self.assertRedirects(r, reverse("panel:backups"), fetch_redirect_response=False)
        r = self.client.post(url, {"code": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertIn('filename="media_2026-11-18.tar"', r["Content-Disposition"])
        self.assertIn("media/projects/a/smeta.xlsx", self._tar(b"".join(r.streaming_content)))
        self.assertTrue(Event.objects.filter(kind=Event.Kind.ADMIN_DOWNLOAD, detail__contains="media_2026-11-18.tar").exists())
        r = self.client.post(reverse("panel:backup_media_download", args=["current"]), {"code": PASSWORD})
        self.assertEqual(self._tar(b"".join(r.streaming_content)), {"media/yangi.txt": b"bugun"})

    def test_bad_names_and_non_superuser(self):
        self.client.force_login(self.boss)
        for name in ("..", "2026-11-19", "db", "..%2F..%2Fmedia"):
            r = self.client.post(reverse("panel:backups") + f"media/{name}/", {"code": PASSWORD})
            self.assertEqual(r.status_code, 404, name)
        viewer = User.objects.create_user("v@test.uz", PASSWORD, first_name="V", last_name="V", is_staff=True)
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(reverse("panel:backup_media_download", args=["current"]),
                                          {"code": PASSWORD}).status_code, 403)

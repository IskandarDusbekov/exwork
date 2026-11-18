"""Базанинг захира нусхаси (панелдан): рўйхат, ҳозир олиш, юклаб олиш, тиклаш синови ва огоҳлантириш.

Ҳар кунги нусхани серверда ``deploy/backup.sh`` (cron) олади — у ``media/`` файлларини ҳам сақлайди; панел уни
кўрсатади ва зарур бўлса базадан қўшимча нусха олади (масалан, катта ўзгаришдан олдин).

Тиклаш синови (:func:`restore_test`) — охирги нусха алоҳида бўш базага ҳақиқатан ёзилади ва текширилади (асосий
базага тегилмайди). Муаммолар (:func:`problems`) — нусха эски, бошқа жойда нусха йўқ, синалмаган, backup.sh хатоси —
панелда кўринади ва суперфойдаланувчиларга кунига бир марта хат кетади (:func:`alert`; worker соатда текширади)."""
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from django.conf import settings
from django.db import close_old_connections, connection
from django.utils import timezone

log = logging.getLogger(__name__)
KEEP_DAYS = 14  # backup.sh ҳам шунча сақлайди
STATE_KEY = "_backup_state"
NAME_RE = re.compile(r"^narxlash_\d{4}-\d{2}-\d{2}_\d{4,6}\.(dump|sqlite3)$")
STALE_HOURS = 26  # кунлик нусха шундан эски бўлса — огоҳлантириш (cron ишламаяпти)
TEST_KEY = "_restore_test"
FAILED_KEY = "_backup_failed"  # backup.sh хатоси (manage.py backup_check --failed)
ALERT_KEY = "_backup_alert"  # охирги юборилган огоҳлантириш (кунига бир марта)
TEST_MAX_DAYS = 35  # тиклаш синови шундан эски бўлса — огоҳлантириш (cron ҳар якшанба)
MEDIA_STALE_DAYS = 2


def root() -> Path:
    return Path(settings.NARXLASH_BACKUP_DIR)


def dumps() -> List[dict]:
    d = root() / "db"
    out = []
    if d.is_dir():
        for p in d.iterdir():
            if p.is_file() and NAME_RE.match(p.name):
                st = p.stat()
                out.append({"name": p.name, "size": st.st_size,
                            "time": datetime.fromtimestamp(st.st_mtime, tz=timezone.get_current_timezone())})
    return sorted(out, key=lambda x: x["time"], reverse=True)


def media_snapshots() -> List[dict]:
    d = root() / "media"
    if not d.is_dir():
        return []
    return [{"name": p.name} for p in sorted(d.iterdir(), reverse=True) if p.is_dir() and re.match(r"^20\d\d-\d\d-\d\d$", p.name)]


def path_of(name: str) -> Optional[Path]:
    """Фақат рўйхатдаги ном (йўл ўйини — ``../`` — бўлмайди)."""
    if not NAME_RE.match(name or ""):
        return None
    p = root() / "db" / name
    return p if p.is_file() else None


def _get(key) -> dict:
    from .models import SiteSetting
    s = SiteSetting.objects.filter(key=key).first()
    return s.value if s and isinstance(s.value, dict) else {}


def _put(key, value: dict):
    from .models import SiteSetting
    SiteSetting.objects.update_or_create(key=key, defaults={"value": value})


def state() -> dict:
    return _get(STATE_KEY)


def _set_state(**kw):
    _put(STATE_KEY, kw)


def remote() -> str:
    """Нусха кўчириладиган бошқа сервер (.env → BACKUP_REMOTE; сайт ҳам .env ни ўқийди)."""
    return os.environ.get("BACKUP_REMOTE", "").strip()


def test_state() -> dict:
    return _get(TEST_KEY)


def summary() -> dict:
    items = dumps()
    last = items[0] if items else None
    total, _used, free = shutil.disk_usage(root() if root().exists() else settings.BASE_DIR)
    stale = last is None or (timezone.now() - last["time"]).total_seconds() > STALE_HOURS * 3600
    test = test_state()
    if test.get("time"):
        test["when"] = datetime.fromtimestamp(test["time"], tz=timezone.get_current_timezone())
    return {"dir": root(), "dumps": items, "last": last, "stale": stale, "media": media_snapshots(),
            "free": free, "total": total, "state": state(), "engine": connection.vendor,
            "remote": remote(), "test": test, "problems": problems()}


# ===================== муаммолар ва огоҳлантириш =====================
def problems() -> List[tuple]:
    """[(код, матн)]. Код — хат қайта юборилишини аниқлаш учун (матндаги соатлар ўзгарса ҳам — бир хил)."""
    out = []
    items = dumps()
    now = timezone.now()
    if not items:
        out.append(("no_dump", "База нусхаси умуман йўқ — серверда cron (deploy/backup.sh) ўрнатилмаган"))
    elif (now - items[0]["time"]).total_seconds() > STALE_HOURS * 3600:
        hours = int((now - items[0]["time"]).total_seconds() // 3600)
        out.append(("stale", f"Охирги база нусхаси {hours} соат олдин олинган — cron (deploy/backup.sh) ишламаяпти"))
    failed = _get(FAILED_KEY)
    if failed.get("time") and (not items or failed["time"] > items[0]["time"].timestamp()):
        out.append(("failed", f"Кунлик нусха хато билан тугади: {failed.get('msg', '')}"))
    if connection.vendor == "postgresql":  # ишлаб чиқишда (SQLite) cron ва бошқа сервер йўқ
        media = media_snapshots()
        oldest_ok = (timezone.localdate() - timedelta(days=MEDIA_STALE_DAYS)).isoformat()
        if not media or media[0]["name"] < oldest_ok:
            out.append(("media", "Файллар (media) сурати олинмаяпти — фақат база сақланяпти"))
        if not remote():
            out.append(("no_remote", "Нусха фақат шу серверда (.env да BACKUP_REMOTE йўқ) — диск бузилса ҳаммаси "
                                     "бирга йўқолади"))
    t = test_state()
    if t.get("running") and time.time() - t.get("started", 0) < 6 * 3600:
        pass  # ҳозир синаляпти
    elif not t.get("time"):
        out.append(("never_tested", "Нусхадан тиклаш ҳали бирор марта синалмаган"))
    elif not t.get("ok"):
        out.append(("test_failed", f"Охирги тиклаш синови муваффақиятсиз: {t.get('error', '')}"))
    elif time.time() - t["time"] > TEST_MAX_DAYS * 86400:
        out.append(("test_old", f"Тиклаш синови {int((time.time() - t['time']) // 86400)} кундан бери ўтказилмаган"))
    return out


def record_failure(msg: str):
    _put(FAILED_KEY, {"time": time.time(), "msg": (msg or "")[:500]})


def alert(force=False) -> bool:
    """Муаммо бўлса — суперфойдаланувчиларга хат (кунига бир марта; янги муаммо чиқса — дарҳол). Ишлаб чиқишда
    (DEBUG) юборилмайди. Юборилса — True."""
    if settings.DEBUG and not force:
        return False
    probs = problems()
    if not probs:
        if _get(ALERT_KEY):
            _put(ALERT_KEY, {})
        return False
    sig = sorted(code for code, _ in probs)
    last = _get(ALERT_KEY)
    today = timezone.localdate().isoformat()
    if not force and last.get("day") == today and set(sig) <= set(last.get("codes") or []):
        return False
    from django.contrib.auth import get_user_model
    from django.core.mail import send_mail
    to = list(get_user_model().objects.filter(is_superuser=True, is_active=True).exclude(email="")
              .values_list("email", flat=True))
    if not to:
        return False
    body = ("Захира нусхада муаммо бор:\n\n" + "\n".join(f"• {text}" for _, text in probs)
            + "\n\nБатафсил: Панел → Захира нусха. Тиклаш тартиби: deploy/README.md → «Zaxiradan tiklash».")
    try:
        send_mail("mhisobot: захира нусхада муаммо", body, None, to, fail_silently=False)
    except Exception:  # noqa: BLE001 — почта ишламаса ҳам панелда кўринади
        log.exception("захира огоҳлантириши юборилмади")
        return False
    _put(ALERT_KEY, {"day": today, "codes": sig, "time": time.time()})
    log.warning("захира нусха: огоҳлантириш юборилди — %s", ", ".join(sig))
    return True


def start(user) -> bool:
    """Фонда нусха олиш. Аввалгиси тугамаган бўлса — False."""
    st = state()
    if st.get("running") and time.time() - st.get("started", 0) < 3600:
        return False
    _set_state(running=True, started=time.time(), by=user.email)
    threading.Thread(target=_run, args=(user.email,), daemon=True, name="panel-backup").start()
    return True


def _run(by: str):
    try:
        name = make()
        _set_state(running=False, finished=time.time(), file=name, by=by)
    except Exception as e:  # noqa: BLE001 — сабаби панелда кўринади
        log.exception("захира нусха олинмади")
        _set_state(running=False, finished=time.time(), error=str(e)[:500], by=by)
    finally:
        close_old_connections()


def make() -> str:
    """Базанинг нусхаси ``backups/db/`` га (PostgreSQL — pg_dump, ишлаб чиқишда SQLite — backup API)."""
    d = root() / "db"
    d.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(d, 0o700)
    stamp = timezone.localtime().strftime("%Y-%m-%d_%H%M%S")
    if connection.vendor == "postgresql":
        dest = d / f"narxlash_{stamp}.dump"
        tmp = dest.with_suffix(".part")
        subprocess.run(["pg_dump", "--format=custom", "--no-owner", f"--file={tmp}"],
                       env=pg_env(settings.DATABASE_URL), check=True, timeout=3600, capture_output=True)
        subprocess.run(["pg_restore", "--list", str(tmp)], check=True, timeout=600, capture_output=True)
    else:
        dest = d / f"narxlash_{stamp}.sqlite3"
        tmp = dest.with_suffix(".part")
        name = str(connection.settings_dict["NAME"])  # тестда — «file:memorydb_…?mode=memory» (URI)
        src = sqlite3.connect(name, uri=name.startswith("file:"))
        out = sqlite3.connect(tmp)
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
    os.replace(tmp, dest)
    if os.name != "nt":
        os.chmod(dest, 0o600)  # ичида парол хешлари ва икки босқичли кириш калитлари
    cutoff = time.time() - KEEP_DAYS * 86400
    for x in d.iterdir():
        if NAME_RE.match(x.name) and x.stat().st_mtime < cutoff:
            x.unlink(missing_ok=True)
    return dest.name


# ===================== тиклаш синови =====================
def pg_env(url: str) -> dict:
    """DATABASE_URL → PG* ўзгарувчилари: пароль буйруқ қаторида (``ps`` да бошқаларга) кўринмасин."""
    p = urlsplit(url)
    env = {**os.environ, "PGHOST": p.hostname or "localhost", "PGPORT": str(p.port or 5432),
           "PGUSER": unquote(p.username or ""), "PGPASSWORD": unquote(p.password or ""),
           "PGDATABASE": unquote(p.path.lstrip("/"))}
    q = dict(parse_qsl(p.query))
    if q.get("sslmode"):
        env["PGSSLMODE"] = q["sslmode"]
    return env


def test_url() -> str:
    """Синов базаси: RESTORE_TEST_DATABASE_URL ёки асосийсининг номи + «_restore_test» (бир марта очилади,
    README → «Zaxiradan tiklash»)."""
    url = os.environ.get("RESTORE_TEST_DATABASE_URL", "").strip()
    if url:
        return url
    p = urlsplit(settings.DATABASE_URL)
    return urlunsplit(p._replace(path=p.path.rstrip("/") + "_restore_test"))


PG_CLEAN_SQL = """DO $$DECLARE r record; BEGIN
FOR r IN SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','S') AND pg_get_userbyid(c.relowner) = current_user
           -- IDENTITY / serial ketma-ketligi jadval bilan birga ketadi (alohida DROP xato beradi)
           AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                                                AND d.objid = c.oid AND d.deptype IN ('a', 'i')))
         ORDER BY CASE c.relkind WHEN 'v' THEN 0 WHEN 'm' THEN 1 WHEN 'S' THEN 3 ELSE 2 END
LOOP
  EXECUTE format('DROP %s IF EXISTS public.%I CASCADE',
                 CASE r.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
                 r.relname);
END LOOP; END$$;"""  # deploy/pgenv.sh даги билан бир хил


def _expected_tables() -> set:
    from django.apps import apps
    return {m._meta.db_table for m in apps.get_models(include_auto_created=True)
            if m._meta.managed and not m._meta.proxy}


def _live_migrations() -> set:
    from django.db.migrations.recorder import MigrationRecorder
    return set(MigrationRecorder.Migration.objects.values_list("app", "name"))


def _verify(tables: dict, migrations: set) -> dict:
    """``tables`` — {жадвал: қаторлар сони} (тикланган базадан). Нусха эскироқ бўлса (кейинроқ миграция қўшилган)
    — янги жадваллар йўқлиги хато эмас; нусха ҳозирги миграцияларда бўлса — ҳамма жадвал бўлиши шарт."""
    from django.contrib.auth import get_user_model
    if "django_migrations" not in tables or not migrations:
        raise RuntimeError("тикланган базада миграциялар жадвали йўқ — бу сайтнинг нусхаси эмас")
    missing = sorted(_expected_tables() - set(tables))
    behind = len(_live_migrations() - migrations)
    if missing and not behind:
        raise RuntimeError(f"жадваллар йўқ: {', '.join(missing[:10])}")
    users = tables.get(get_user_model()._meta.db_table, 0)
    if not users and get_user_model().objects.exists():
        raise RuntimeError("тикланган базада битта ҳам фойдаланувчи йўқ")
    return {"tables": len(tables), "rows": sum(tables.values()), "users": users, "behind": behind,
            "missing": missing[:10]}


def _test_sqlite(path: Path) -> dict:
    d = root() / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=d) as tmp:
        copy = Path(tmp) / "restore_test.sqlite3"
        shutil.copyfile(path, copy)  # «тиклаш» = файлни жойига қўйиш; асл нусхага тегилмайди
        db = sqlite3.connect(copy)
        try:
            ok = db.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise RuntimeError(f"SQLite файли бузуқ: {ok}")
            names = [r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            tables = {n: db.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
            migrations = (set(db.execute("SELECT app, name FROM django_migrations"))
                          if "django_migrations" in tables else set())
        except sqlite3.DatabaseError as e:
            raise RuntimeError(f"SQLite файли ўқилмади: {e}") from e
        finally:
            db.close()
    return _verify(tables, migrations)


def _test_pg(path: Path) -> dict:
    import psycopg
    url = test_url()
    env = pg_env(url)
    try:
        conn = psycopg.connect(url, autocommit=True)
    except psycopg.OperationalError as e:
        if "does not exist" not in str(e):
            raise
        name = env["PGDATABASE"]
        try:  # фойдаланувчида CREATEDB ҳуқуқи бўлса (Docker'да — бор) ўзи очади
            with psycopg.connect(settings.DATABASE_URL, autocommit=True) as c:
                c.execute(f'CREATE DATABASE "{name}"')
        except psycopg.Error as e2:
            raise RuntimeError(f"синов базаси «{name}» йўқ ва очиб бўлмади. Серверда бир марта: "
                               f"sudo -u postgres psql -c \"CREATE DATABASE {name} OWNER {env['PGUSER']};\"") from e2
        conn = psycopg.connect(url, autocommit=True)
    try:
        conn.execute(PG_CLEAN_SQL)
        r = subprocess.run(["pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction",
                            "-d", env["PGDATABASE"], str(path)], env=env, timeout=6 * 3600, capture_output=True)
        if r.returncode:
            raise RuntimeError("pg_restore: " + r.stderr.decode("utf-8", "replace").strip()[-400:])
        names = [x[0] for x in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
        tables = {n: conn.execute(f'SELECT COUNT(*) FROM public."{n}"').fetchone()[0] for n in names}
        migrations = ({tuple(x) for x in conn.execute("SELECT app, name FROM django_migrations")}
                      if "django_migrations" in tables else set())
        return _verify(tables, migrations)
    finally:
        try:
            conn.execute(PG_CLEAN_SQL)  # синов базаси жой эгалламасин
        except Exception:  # noqa: BLE001 — тозалаш хатоси синов натижасини яширмасин
            log.warning("тиклаш синови: синов базаси тозаланмади", exc_info=True)
        finally:
            conn.close()


def restore_test(name: Optional[str] = None) -> dict:
    """Нусхани (берилмаса — охиргисини) алоҳида бўш базага тиклаб текширади; натижа панелда кўринади."""
    started = time.time()
    if name is None:
        items = dumps()
        name = items[0]["name"] if items else ""
    path = path_of(name) if name else None
    res = {"file": name}
    try:
        if path is None:
            raise RuntimeError("синаш учун нусха йўқ")
        res.update(_test_pg(path) if path.suffix == ".dump" else _test_sqlite(path))
        res["ok"] = True
        log.info("тиклаш синови ўтди: %s (%s жадвал, %s қатор)", name, res["tables"], res["rows"])
    except Exception as e:  # noqa: BLE001 — сабаби панелда ва хатда кўринади
        log.exception("тиклаш синови ўтмади: %s", name)
        res.update(ok=False, error=str(e)[:500])
    res.update(time=time.time(), seconds=round(time.time() - started))
    _put(TEST_KEY, res)
    return res


def start_test(user) -> bool:
    """Панелдан: тиклаш синовини фонда бошлаш."""
    t = test_state()
    if t.get("running") and time.time() - t.get("started", 0) < 6 * 3600:
        return False
    _put(TEST_KEY, {**t, "running": True, "started": time.time(), "by": user.email})

    def run():
        try:
            restore_test()
        finally:
            close_old_connections()
    threading.Thread(target=run, daemon=True, name="panel-restore-test").start()
    return True

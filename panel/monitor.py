"""Жонли кузатув (панел → «Тизим»): сўровлар вақти, ҳозир бажарилаётган сўровлар, сервер ресурслари, база,
Redis, LibreOffice, фон ишчилари, nginx журнали ва муаммолар рўйхати.

Сўровлар (``RequestMonitorMiddleware``) кешга ёзилади — gunicorn'нинг бир нечта жараёни бўлса ҳам (Redis умумий)
ҳаммаси бир жойда кўринади:

* ``mon:m:<дақиқа>:<n|ms|db|e4|e5|slow>`` — дақиқа бўйича ҳисоблагичлар (2 соат сақланади);
* ``mon:recent:<pid>`` — шу жараённинг охирги 40 та сўрови;
* ``mon:flight:<pid>`` — шу жараёнда **айни пайтда** бажарилаётган сўровлар (қайси саҳифа, қайси функция, қачондан).

Кузатув хатоси сайтни ҳеч қачон бузмайди — ҳаммаси ``try`` ичида."""
import itertools
import logging
import os
import re
import socket
import threading
import time
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connection

log = logging.getLogger(__name__)

KEEP = 2 * 3600
RECENT = 40
SLOW_MS = 1500
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_NUM = re.compile(r"(?<=/)\d+(?=/|$)")
_ids = itertools.count(1)
_lock = threading.Lock()
_flight = {}  # id -> [start, method, path, view, user]
_recent = []
SKIP = ("/static/", "/favicon", "/media/")


def _self_paths():
    from django.urls import reverse
    try:
        return (reverse("panel:tizim_live"),)
    except Exception:  # noqa: BLE001
        return ()


_POLL = None


def is_poll(request) -> bool:
    """«Тизим» саҳифасининг ҳар 3 сониялик ўз сўрови: фойдаланувчи фаоллиги эмас — журнал, сессия вақти ва
    сўров ҳисобига қўшилмайди (акс ҳолда саҳифа очиқ турганда «сайтда вақт» ўсиб, сўров доим кўринади)."""
    global _POLL
    if _POLL is None:
        _POLL = _self_paths()
    return request.path in _POLL


def _norm(path: str) -> str:
    return _NUM.sub("<n>", _UUID.sub("<id>", path))[:160]


def _bump(minute: int, **vals):
    for k, v in vals.items():
        if not v:
            continue
        key = f"mon:m:{minute}:{k}"
        try:
            cache.add(key, 0, KEEP)
            cache.incr(key, int(v))
        except ValueError:  # калит шу орада ўчди
            cache.set(key, int(v), KEEP)


def _publish():
    pid = os.getpid()
    with _lock:
        flight = [[i] + v for i, v in _flight.items()]
        recent = list(_recent)
    cache.set_many({f"mon:flight:{pid}": flight, f"mon:recent:{pid}": recent}, 600)
    procs = cache.get("mon:pids") or {}
    if procs.get(str(pid), 0) < time.time() - 30:
        procs = {k: t for k, t in procs.items() if t > time.time() - 900}
        procs[str(pid)] = time.time()
        cache.set("mon:pids", procs, 3600)


class RequestMonitorMiddleware:
    """Ҳар сўровнинг вақти, базага кетган вақт ва сўровлар сони, жавоб коди. Айни пайтдагилари ҳам кўринади."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.skip_exact = None

    def __call__(self, request):
        if self.skip_exact is None:
            self.skip_exact = _self_paths()
        path = request.path
        if path.startswith(SKIP) or path in self.skip_exact:
            return self.get_response(request)
        rid = next(_ids)
        start = time.time()
        request._mon_id = rid
        with _lock:
            _flight[rid] = [start, request.method, _norm(path), "", ""]
        db = {"n": 0, "ms": 0.0}

        def wrap(execute, sql, params, many, context):
            t = time.perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                db["n"] += 1
                db["ms"] += (time.perf_counter() - t) * 1000

        try:
            _publish()
        except Exception:  # noqa: BLE001
            pass
        status = 500
        try:
            with connection.execute_wrapper(wrap):
                response = self.get_response(request)
            status = response.status_code
            return response
        finally:
            ms = (time.time() - start) * 1000
            view = getattr(getattr(request, "resolver_match", None), "_func_path", "") or ""
            uid = ""
            try:
                user = getattr(request, "user", None)
                if user is not None and user.is_authenticated:
                    uid = user.email
            except Exception:  # noqa: BLE001
                pass
            try:
                with _lock:
                    _flight.pop(rid, None)
                    _recent.append([round(start, 1), request.method, _norm(path), view, status, round(ms), db["n"],
                                    round(db["ms"]), uid])
                    del _recent[:-RECENT]
                _bump(int(start // 60), n=1, ms=ms, db=db["ms"], e4=400 <= status < 500, e5=status >= 500,
                      slow=ms >= SLOW_MS)
                _publish()
            except Exception:  # noqa: BLE001 — кузатув сайтни бузмасин
                log.debug("monitor failed", exc_info=True)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # айни пайтдаги сўровга функция номи (URL топилгандан кейин маълум бўлади)
        try:
            name = f"{view_func.__module__}.{getattr(view_func, '__qualname__', view_func.__name__)}"
            user = getattr(request, "user", None)
            email = user.email if user is not None and user.is_authenticated else ""
            with _lock:
                v = _flight.get(getattr(request, "_mon_id", None))
                if v is not None:
                    v[3], v[4] = name, email
            _publish()
        except Exception:  # noqa: BLE001
            pass
        return None


# ===================== йиғиш =====================
def requests_state(minutes: int = 60) -> dict:
    now = time.time()
    m0 = int(now // 60)
    keys = [f"mon:m:{m}:{k}" for m in range(m0 - minutes + 1, m0 + 1) for k in ("n", "ms", "db", "e4", "e5", "slow")]
    got = cache.get_many(keys)
    series = []
    for m in range(m0 - minutes + 1, m0 + 1):
        g = lambda k: got.get(f"mon:m:{m}:{k}", 0)  # noqa: E731
        series.append({"t": m * 60, "n": g("n"), "ms": g("ms"), "db": g("db"), "e4": g("e4"), "e5": g("e5"), "slow": g("slow")})
    pids = list((cache.get("mon:pids") or {}).keys())
    if str(os.getpid()) not in pids:
        pids.append(str(os.getpid()))
    data = cache.get_many([f"mon:{w}:{p}" for p in pids for w in ("flight", "recent")])
    flight, recent = [], []
    for p in pids:
        for f in data.get(f"mon:flight:{p}") or []:
            if now - f[1] < 900:  # ўлган жараённинг эски ёзуви — кўрсатилмайди
                flight.append({"pid": p, "since": f[1], "ms": round((now - f[1]) * 1000), "method": f[2], "path": f[3],
                               "view": f[4], "user": f[5]})
        for r in data.get(f"mon:recent:{p}") or []:
            recent.append({"pid": p, "t": r[0], "method": r[1], "path": r[2], "view": r[3], "status": r[4], "ms": r[5],
                           "q": r[6], "db": r[7], "user": r[8]})
    recent.sort(key=lambda r: r["t"], reverse=True)
    last5 = series[-5:]
    n5 = sum(s["n"] for s in last5)
    last15 = series[-15:]
    return {
        "series": series, "flight": sorted(flight, key=lambda f: f["since"]), "recent": recent[:60],
        "per_min": round(n5 / 5, 1), "avg_ms": round(sum(s["ms"] for s in last5) / n5) if n5 else 0,
        "db_ms": round(sum(s["db"] for s in last5) / n5) if n5 else 0,
        "e5_15": sum(s["e5"] for s in last15), "e4_15": sum(s["e4"] for s in last15),
        "slow_15": sum(s["slow"] for s in last15), "n_hour": sum(s["n"] for s in series),
        "slowest": sorted(recent, key=lambda r: r["ms"], reverse=True)[:8],
        "processes": len(pids), "slow_ms": SLOW_MS,
    }


# ---------- сервер ----------
_procs = {}


def _psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def _pinfo(p, psutil) -> dict:
    """Битта жараён: PID, хотира, процессор, қачондан, оқимлар."""
    cached = _procs.get(p.pid)
    if cached is None or cached.create_time() != p.create_time():
        _procs[p.pid] = cached = p
        cached.cpu_percent(None)
    try:
        with cached.oneshot():
            mem = cached.memory_info().rss
            return {"pid": p.pid, "rss": mem, "cpu": round(cached.cpu_percent(None), 1), "since": cached.create_time(),
                    "threads": cached.num_threads(), "name": cached.name()}
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return {"pid": p.pid, "rss": 0, "cpu": 0, "since": 0, "threads": 0, "name": "?"}


GROUPS = (  # (калит, жараён номида ёки буйруғида нима бор)
    ("web", ("gunicorn", "runserver")),
    ("worker", ("run_worker",)),
    ("lo", ("soffice",)),
    ("pg", ("postgres",)),
    ("redis", ("redis-server",)),
    ("nginx", ("nginx",)),
)


def server_state() -> dict:
    psutil = _psutil()
    out = {"host": socket.gethostname(), "pid": os.getpid(), "psutil": bool(psutil)}
    try:
        load = os.getloadavg()
    except (AttributeError, OSError):
        load = None
    out["load"] = load
    if not psutil:
        return out
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    out.update({
        "cpu": psutil.cpu_percent(None), "cores": psutil.cpu_count(), "boot": psutil.boot_time(),
        "ram": {"total": vm.total, "used": vm.total - vm.available, "avail": vm.available, "pct": vm.percent},
        "swap": {"total": sw.total, "used": sw.used, "pct": sw.percent},
    })
    disks = []
    seen = set()
    for label, path in (("Сайт ва база", settings.BASE_DIR), ("media/ (Excel файллар)", settings.MEDIA_ROOT),
                        ("Захира", getattr(settings, "NARXLASH_BACKUP_DIR", None))):
        if not path:
            continue
        try:
            du = psutil.disk_usage(str(path))
        except OSError:
            continue
        key = (du.total, du.used)
        disks.append({"label": label, "path": str(path), "total": du.total, "used": du.used, "free": du.free,
                      "pct": du.percent, "same": key in seen})
        seen.add(key)
    out["disks"] = disks
    groups = {k: [] for k, _ in GROUPS}
    me = os.getpid()
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or []).lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for key, marks in GROUPS:
            if any(m in name or m in cmd for m in marks):
                if key == "web" and "run_worker" in cmd:
                    continue
                groups[key].append(_pinfo(p, psutil))
                break
    for key in groups:
        groups[key].sort(key=lambda x: x["since"])
    out["procs"] = groups
    out["me"] = me
    alive = {p.pid for p in psutil.process_iter()}
    for pid in [p for p in _procs if p not in alive]:
        _procs.pop(pid, None)
    return out


# ---------- база ----------
def db_state() -> dict:
    vendor = connection.vendor
    out = {"vendor": vendor}
    try:
        with connection.cursor() as c:
            if vendor == "postgresql":
                c.execute("select pg_database_size(current_database()), current_database(), version()")
                size, name, ver = c.fetchone()
                c.execute("show max_connections")
                maxc = int(c.fetchone()[0])
                c.execute("""select state, count(*) from pg_stat_activity where datname = current_database()
                             group by state""")
                states = {s or "background": n for s, n in c.fetchall()}
                c.execute("""select pid, state, extract(epoch from now() - query_start), left(query, 300), wait_event_type,
                                    application_name
                             from pg_stat_activity
                             where datname = current_database() and pid <> pg_backend_pid() and state <> 'idle'
                             order by query_start limit 15""")
                active = [{"pid": r[0], "state": r[1], "sec": round(r[2] or 0, 1), "sql": r[3], "wait": r[4] or "",
                           "app": r[5] or ""} for r in c.fetchall()]
                c.execute("""select relname, pg_total_relation_size(relid), n_live_tup from pg_stat_user_tables
                             order by pg_total_relation_size(relid) desc limit 10""")
                tables = [{"name": r[0], "size": r[1], "rows": r[2]} for r in c.fetchall()]
                c.execute("""select blks_hit, blks_read, xact_commit, xact_rollback, deadlocks
                             from pg_stat_database where datname = current_database()""")
                hit, read, com, rb, dl = c.fetchone()
                out.update({"size": size, "name": name, "version": ver.split(",")[0], "max_conn": maxc, "conn": states,
                            "conn_total": sum(states.values()), "active": active, "tables": tables,
                            "cache_hit": round(100 * hit / (hit + read), 1) if hit + read else None,
                            "commits": com, "rollbacks": rb, "deadlocks": dl})
            else:
                db_file = Path(settings.DATABASES["default"]["NAME"])
                out.update({"size": db_file.stat().st_size if db_file.exists() else 0, "name": db_file.name,
                            "version": "SQLite (ишлаб чиқиш)"})
                c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")
                names = [r[0] for r in c.fetchall()]
                tables = []
                for n in names:
                    c.execute(f'select count(*) from "{n}"')  # noqa: S608 — номлар базанинг ўзидан
                    tables.append({"name": n, "rows": c.fetchone()[0], "size": None})
                out["tables"] = sorted(tables, key=lambda t: t["rows"], reverse=True)[:10]
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:300]
    return out


# ---------- Redis ----------
def cache_state() -> dict:
    backend = settings.CACHES["default"]["BACKEND"].rsplit(".", 1)[-1]
    out = {"backend": backend}
    if backend != "RedisCache":
        out["note"] = "Ишлаб чиқиш: кеш шу жараён хотирасида (Redis йўқ)"
        return out
    try:
        client = cache._cache.get_client(None, write=False)
        info = client.info()
        hits, miss = info.get("keyspace_hits", 0), info.get("keyspace_misses", 0)
        out.update({"mem": info.get("used_memory"), "peak": info.get("used_memory_peak"), "clients": info.get("connected_clients"),
                    "uptime": info.get("uptime_in_seconds"), "version": info.get("redis_version"),
                    "keys": sum(v.get("keys", 0) for k, v in info.items() if k.startswith("db") and isinstance(v, dict)),
                    "hit": round(100 * hits / (hits + miss), 1) if hits + miss else None,
                    "maxmem": info.get("maxmemory") or None})
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:300]
    return out


# ---------- LibreOffice ----------
def lo_state() -> dict:
    from core.convert import converter_name, find_soffice
    from jobs.models import FileTiming
    from django.utils import timezone
    from datetime import timedelta
    out = {"timeout": 180}
    from jobs import lo_queue
    out.update(slots=lo_queue.slots(), busy=lo_queue.busy(), waiting=lo_queue.waiting())
    try:
        out["exe"] = find_soffice(None)
    except Exception as e:  # noqa: BLE001
        out["exe"] = ""
        out["exe_error"] = str(e)[:200]
    try:
        out["method"] = converter_name()
    except Exception:  # noqa: BLE001
        out["method"] = ""
    since = timezone.now() - timedelta(days=1)
    rows = list(FileTiming.objects.filter(convert_s__isnull=False).order_by("-created")[:12])
    out["recent"] = [{"t": r.created.isoformat(), "name": r.name[:80], "ext": r.ext,
                      "size": r.size, "sec": round(r.convert_s, 1), "method": r.method, "error": (r.error or "")[:120]}
                     for r in rows]
    day = [r for r in FileTiming.objects.filter(convert_s__isnull=False, created__gte=since).only("convert_s", "error")]
    ok = [r.convert_s for r in day if not r.error]
    out["day"] = {"n": len(day), "errors": sum(1 for r in day if r.error), "avg": round(sum(ok) / len(ok), 1) if ok else None,
                  "max": round(max(ok), 1) if ok else None}
    return out


# ---------- nginx ----------
NGINX_LOG = getattr(settings, "NARXLASH_NGINX_LOG", "/var/log/nginx/narxlash.mon.log")
_NGX = re.compile(r"^(?P<t>\S+) (?P<status>\d{3}) (?P<rt>[\d.]+) (?P<up>\S+) (?P<m>\S+) \"(?P<uri>[^\"]*)\" (?P<bytes>\d+)")


def nginx_state(minutes: int = 15) -> dict:
    """``deploy/nginx/narxlash.conf`` даги ``narxlash_mon`` журналининг охири: кодлар, вақт, секин манзиллар."""
    from datetime import datetime, timedelta, timezone as tz
    path = Path(NGINX_LOG)
    out = {"log": str(path), "readable": False}
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - 400_000))
            lines = f.read().decode("utf-8", "replace").splitlines()[1:]
    except OSError as e:
        out["error"] = e.strerror or str(e)
        return out
    out["readable"] = True
    since = datetime.now(tz.utc) - timedelta(minutes=minutes)
    codes, times, slow, n = {}, [], [], 0
    for line in lines:
        m = _NGX.match(line)
        if not m:
            continue
        try:
            t = datetime.fromisoformat(m["t"])
        except ValueError:
            continue
        if t < since:
            continue
        n += 1
        codes[m["status"]] = codes.get(m["status"], 0) + 1
        rt = float(m["rt"])
        times.append(rt)
        slow.append((rt, m["m"], _norm(m["uri"]), m["status"]))
    times.sort()
    out.update({"minutes": minutes, "n": n, "codes": dict(sorted(codes.items())),
                "avg_ms": round(1000 * sum(times) / len(times)) if times else 0,
                "p95_ms": round(1000 * times[int(len(times) * .95) - 1]) if len(times) >= 20 else None,
                "slow": [{"ms": round(s[0] * 1000), "method": s[1], "uri": s[2], "status": s[3]}
                         for s in sorted(slow, reverse=True)[:6]]})
    return out


# ---------- папкалар ҳажми (секин — 15 дақиқада бир, фонда) ----------
def media_sizes() -> dict:
    got = cache.get("mon:media_sizes")
    if got is None and cache.add("mon:media_sizes_busy", 1, 600):
        threading.Thread(target=_measure_media, daemon=True, name="mon-media").start()
    return got or {}


def _measure_media():
    try:
        root = Path(settings.MEDIA_ROOT)
        out = {}
        for sub in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
            total = files = 0
            for dirpath, _d, names in os.walk(sub):
                for n in names:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, n))
                        files += 1
                    except OSError:
                        pass
            out[sub.name] = {"size": total, "files": files}
        cache.set("mon:media_sizes", {"dirs": out, "at": time.time()}, 900)
    except Exception:  # noqa: BLE001
        log.debug("media sizes failed", exc_info=True)
    finally:
        cache.delete("mon:media_sizes_busy")

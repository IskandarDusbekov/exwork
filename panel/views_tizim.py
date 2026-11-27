"""Панел → «Тизим»: сервернинг жонли ҳолати (бошқарув), жараёнлар схемаси ва кодни фақат ўқиш учун кўриш.

* ``tizim_page`` — бутун экранли саҳифа (``panel/tizim.html`` + ``static/js/tizim.js``). Тугунлар ҳақида матн ва
  жараёнлар — ``panel/tizim_lessons.py``.
* ``tizim_live`` — ҳозирги ҳолат: сўровлар (вақти билан), ресурслар, база, Redis, LibreOffice, nginx, фон ишчилари,
  ҳозир бажарилаётган ишлар ва уларнинг коди, муаммолар. Йиғувчилар — ``panel/monitor.py``.
* ``tizim_code`` — лойиҳа файлини **фақат ўқиш** учун: рухсат этилган папка ва кенгайтмалар; ``.env``, яширин
  файллар ва лойиҳадан ташқари йўллар ҳеч қачон берилмайди. Функция берилса — қаторлари, вазифаси (docstring) ва
  қаерда чақирилиши.

Фақат суперфойдаланувчи (код — тижорат сири)."""
import ast
import multiprocessing
import os
import re
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Sum
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from .tizim_lessons import LESSONS, NODES
from .views import superuser_required

ROOT = Path(settings.BASE_DIR).resolve()
CODE_DIRS = ("core", "jobs", "panel", "accounts", "resbase", "narxbaza", "texnazor", "support", "news", "web",
             "deploy", "docs")
CODE_EXT = {".py", ".html", ".js", ".css", ".sh", ".conf", ".service", ".md", ".yml", ".txt"}
_DENY = ("secret", ".env", "password", "id_rsa", ".pem", ".key")
MAX_BYTES = 600_000
_SKIP_DIRS = {"__pycache__", "node_modules", "migrations", "static", "fixtures"}


# ===================== код (фақат ўқиш) =====================
def _safe_path(rel: str):
    """Рухсат этилган файл ёки ``None``. Йўл лойиҳа ичида, яширин эмас, рухсат этилган папка ва кенгайтмада."""
    rel = (rel or "").replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/") or any(p.startswith(".") for p in rel.split("/")):
        return None
    if rel.split("/")[0] not in CODE_DIRS or any(d in rel.lower() for d in _DENY):
        return None
    try:
        p = (ROOT / rel).resolve()
        p.relative_to(ROOT)
    except (ValueError, OSError):
        return None
    if p.suffix.lower() not in CODE_EXT or not p.is_file() or p.stat().st_size > MAX_BYTES:
        return None
    return p


def _py_node(src: str, name: str):
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    body, node = tree.body, None
    for part in name.split("."):
        node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.name == part), None)
        if node is None:  # модул даражасидаги ўзгарувчи (``MIDDLEWARE = [...]``)
            node = next((n for n in body if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == part for t in n.targets)), None)
            if node is None:
                return None
        body = getattr(node, "body", [])
    return node


def _py_range(src: str, name: str):
    node = _py_node(src, name)
    if node is None:
        return None
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    return start, node.end_lineno


def _find_range(lines, text: str):
    """``text`` бор биринчи қатор ва ундан кейинги блок (бўш қаторгача, кўпи билан 25 қатор)."""
    for i, line in enumerate(lines, 1):
        if text in line:
            end = i
            while end < len(lines) and end - i < 25 and lines[end].strip():
                end += 1
            return i, end
    return None


def resolve(rel: str, fn: str = "", find: str = "", line: int = 0):
    """(файл матни, (бошланиш, тугаш)) — топилмаса (None, None). Файл рухсат этилмаса — Http404."""
    p = _safe_path(rel)
    if p is None:
        raise Http404
    src = p.read_text(encoding="utf-8", errors="replace")
    rng = None
    if fn and p.suffix == ".py":
        rng = _py_range(src, fn)
    elif find:
        rng = _find_range(src.splitlines(), find)
    elif line:
        rng = (line, line)
    return src, (rng or (None, None))


_files_cache = {"at": 0, "files": []}


def code_files():
    """Кўриш мумкин бўлган файллар рўйхати."""
    if time.time() - _files_cache["at"] < 60:
        return _files_cache["files"]
    out = []
    for top in CODE_DIRS:
        base = ROOT / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
            for name in sorted(filenames):
                rel = Path(dirpath, name).relative_to(ROOT).as_posix()
                if Path(name).suffix.lower() in CODE_EXT and _safe_path(rel):
                    out.append(rel)
    _files_cache.update(at=time.time(), files=out)
    return out


GENERIC = {"run", "make", "index", "start", "request", "queue", "_run", "_free", "_claim", "claim_next", "run_now",
           "worker_mode", "detail", "save", "get", "update", "delete", "settle", "write", "read", "main", "Command"}


def callers(rel: str, fn: str, limit: int = 25) -> list:
    """``fn`` қаерда чақирилади ёки уланади (URL, импорт). Умумий номлар (``run``, ``make``) — модул номи билан."""
    name = fn.split(".")[-1]
    mod = Path(rel).stem
    if name in GENERIC or len(name) <= 4:
        pat = re.compile(rf"\b{re.escape(mod)}\.{re.escape(name)}\b")
    else:
        pat = re.compile(rf"(?<![\w.]def )\b{re.escape(name)}\b(?!\s*=)")
    out = []
    for f in code_files():
        if not f.endswith((".py", ".html", ".js")) or "/tests" in f or f.startswith("docs/"):
            continue
        try:
            lines = (ROOT / f).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if not pat.search(line) or s.startswith(("#", "def ", "class ", "async def ")) or s.startswith(('"', "'")):
                continue
            out.append({"f": f, "line": i, "text": s[:160]})
            if len(out) >= limit:
                return out
    return out


@superuser_required
def tizim_code(request):
    if request.GET.get("list"):
        return JsonResponse({"files": code_files()})
    rel = request.GET.get("f", "").replace("\\", "/").strip("/")
    fn = request.GET.get("fn", "")
    line = int(request.GET["line"]) if request.GET.get("line", "").isdigit() else 0
    src, (start, end) = resolve(rel, fn, request.GET.get("find", ""), line)
    doc = ""
    if fn and rel.endswith(".py"):
        node = _py_node(src, fn)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node) or ""
    return JsonResponse({"path": rel, "src": src, "start": start, "end": end, "lines": src.count("\n") + 1,
                         "doc": doc[:1500], "callers": callers(rel, fn) if fn and start else []})


# ===================== саҳифа =====================
@superuser_required
def tizim_page(request):
    return render(request, "panel/tizim.html", {"lessons": LESSONS, "nodes": NODES})


# ===================== жонли ҳолат =====================
def _gunicorn():
    workers = int(os.environ.get("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 5)))
    threads = int(os.environ.get("GUNICORN_THREADS", 2))
    return {"workers": workers, "threads": threads, "slots": workers * threads, "cpu": multiprocessing.cpu_count(),
            "timeout": 300, "debug": settings.DEBUG}


# нархлаш босқичи (``Batch.step`` охири) → айни пайтда ишлаётган код
PHASE_CODE = {
    "read": ("core/pipeline.py", "run"),
    "price": ("core/pipeline.py", "_price_smart"),
    "write": ("core/writer.py", "write_priced_rows"),
    "save": ("core/pipeline.py", "_run_xlsx"),
    "auto": ("jobs/services.py", "run_job"),
}
STAGE_CODE = {"queued": ("jobs/folders.py", "_run"), "converting": ("jobs/folders.py", "convert_files"),
              "pricing": ("jobs/folders.py", "price_part"), "ready": ("jobs/folders.py", "analyze_part")}


def _age(dt):
    return round((timezone.now() - dt).total_seconds()) if dt else None


# ---------- битта иш: қайси босқичда, қайси файл, қайси код ----------
PRICING_STAGES = (  # (калит, номи, нима бўлади, код)
    ("queue", "Навбат", "«Бошлаш» босилди — иш базага ёзилди, бўш ишчи оқими уни олади", ("jobs/pricing.py", "claim_next")),
    ("prep", "Тайёрлаш", "Смета учун папка очилади, локал ва ресурс файл нусхаланади", ("jobs/services.py", "confirm_batch")),
    ("read", "Excel ўқиш", "Локал смета ва ресурс файл (.xlsx) хотирага ўқилади, устунлар топилади", ("core/pipeline.py", "run")),
    ("price", "Нархлаш", "Ҳар бир қатор ресурс файлдан / базадан қидирилади ва нархи қўйилади", ("core/pipeline.py", "_price_smart")),
    ("write", "Excel'га ёзиш", "Топилган нархлар ва формулалар натижа файлига ёзилади", ("core/writer.py", "write_priced_rows")),
    ("save", "Сақлаш", "Натижа .xlsx media/ га сақланади", ("core/pipeline.py", "_run_xlsx")),
    ("auto", "Таклифлар", "Топилмаган қаторларга ўхшаш нархлар таклиф қилинади", ("jobs/services.py", "run_job")),
)
FOLDER_STAGES = (
    ("queued", "Навбат", "Папка юкланди, файллар диска ёзилди — ишчи оқимини кутяпти", ("jobs/folders.py", "claim_next")),
    ("converting", "Файлларни очиш", ".xls → .xlsx (LibreOffice), листлар ўқилади", ("jobs/folders.py", "convert_files")),
    ("ready", "Таҳлил", "Листлар тури (локал / ресурс) аниқланади ва жуфтланади", ("jobs/folders.py", "analyze_part")),
    ("pricing", "Нархлаш", "Ҳар бир ички папканинг жуфтликлари нархланади", ("jobs/folders.py", "price_part")),
    ("done", "Тайёр", "Ҳамма ички папка тугади", ("jobs/folders.py", "settle")),
)
_HEAD = re.compile(r"^(\d+)/(\d+) смета нархланяпти: (.*)$")


def _stages(table, cur):
    return [{"key": k, "t": t, "what": w, "code": {"f": c[0], "fn": c[1]}} for k, t, w, c in table], cur


def simple_detail(code, worker):
    return {"stages": [{"key": "queue", "t": "Навбат", "what": "Ишчи оқимини кутяпти", "code": None},
                       {"key": "run", "t": "Бажарилмоқда", "what": "", "code": {"f": code[0], "fn": code[1]}}],
            "cur": 1 if worker else 0}


def pricing_detail(b, tail) -> dict:
    """Нархлаш: босқичлар, ҳозирги смета (қайси файл, қайси лист), қолган сметалар."""
    from jobs.services import pair_ready
    step = b.step or ""
    head = step.split(" — ", 1)[0]
    m = _HEAD.match(head)
    keys = [x[0] for x in PRICING_STAGES]
    cur = 0 if not b.worker else keys.index(tail["phase"]) if tail.get("phase") in keys else 1
    stages, cur = _stages(PRICING_STAGES, cur)
    pairs = [p for p in b.pairs if pair_ready(p)]
    k = int(m.group(1)) if m else 0
    smetas = []
    for i, p in enumerate(pairs, 1):
        loc = b.sheet(p["local"]) or {}
        res = b.sheet(p["res"]) if p.get("res") else None
        f = lambda sh: (b.files[sh["file"]]["name"] if sh and sh.get("file") is not None and sh["file"] < len(b.files) else "")  # noqa: E731
        smetas.append({"label": p.get("label", ""), "local": f(loc), "local_sheet": loc.get("sheet", ""),
                       "res": f(res) if res else "ресурс базаси", "res_sheet": (res or {}).get("sheet", ""),
                       "state": "done" if i < k else "now" if i == k else "wait"})
    return {"stages": stages, "cur": cur, "smetas": smetas, "files": [f.get("name", "") for f in b.files],
            "step_age": _age(b.step_since), "heartbeat": _age(b.heartbeat), "url": reverse("batch", args=[b.pk])}


def folder_detail(p, part) -> dict:
    keys = [x[0] for x in FOLDER_STAGES]
    stage = part.stage if part else "queued"
    stages, cur = _stages(FOLDER_STAGES, keys.index(stage) if stage in keys and p.worker else 0)
    counts = {}
    for st in p.parts.values_list("stage", flat=True):
        counts[st] = counts.get(st, 0) + 1
    from jobs.models import Batch
    labels = dict(Batch.Stage.choices)
    return {"stages": stages, "cur": cur, "files": [f.get("path", "") for f in (p.files or [])[:200]],
            "nfiles": len(p.files or []), "part": part.folder or "(асосий папка)" if part else "",
            "parts": [{"t": labels.get(k, k), "n": n} for k, n in sorted(counts.items(), key=lambda x: -x[1])],
            "step_age": _age(part.step_since) if part else None, "heartbeat": _age(p.heartbeat),
            "url": reverse("project", args=[p.pk])}


def tasks_state() -> dict:
    """Фондаги ишлар: қайси ишчи оқими нимани бажаряпти, қайси қадамда, қайси код; навбатда кутаётганлар."""
    from jobs.models import Batch, Job, Project, ProjectOfyorta
    from jobs.recalc import _pending as recalc_pending
    from jobs.services import parse_step_tail
    from texnazor.models import MyOfyorta, SiteObject

    running, waiting = [], []

    def add(kind, who, name, worker, since, code, step="", rows=None, key="", detail=None):
        item = {"kind": kind, "name": (name or "")[:120], "user": who, "worker": worker, "age": _age(since),
                "step": (step or "")[:200], "code": {"f": code[0], "fn": code[1]}, "rows": rows, "key": key,
                "detail": detail or simple_detail(code, worker)}
        (running if worker else waiting).append(item)

    for b in Batch.objects.filter(pricing__isnull=False, project__isnull=True).select_related("owner"):
        step = b.step or ""
        tail = parse_step_tail(step)
        code = PHASE_CODE.get(tail.get("phase"), ("jobs/pricing.py", "run"))
        rows = [tail["done"], tail["total"]] if tail.get("total") else None
        add("Нархлаш", b.owner.email, b.name, b.worker, b.step_since, code, step or "навбатда", rows,
            key=f"batch:{b.pk}", detail=pricing_detail(b, tail))
    for p in Project.objects.filter(status=Project.Status.PROCESSING).select_related("owner"):
        part = (p.parts.exclude(stage__in=["done", "review", "empty", "failed", "stopped"])
                .order_by("stage_since").first())
        stage = part.stage if part else "queued"
        add("Папка", p.owner.email, p.name, p.worker, p.run_started or p.created,
            STAGE_CODE.get(stage, ("jobs/folders.py", "_run")), (part.step if part and part.step else stage),
            key=f"project:{p.pk}", detail=folder_detail(p, part))
    for j in Job.objects.filter(recalc_pending()).select_related("owner"):
        add("Қайта ҳисоб", j.owner.email, j.local_name, j.recalc_worker, j.recalc_requested, ("jobs/recalc.py", "run"))
    from narxbaza.models import Intake
    for it in Intake.objects.filter(status=Intake.Status.READING).select_related("owner"):
        pr = it.progress or {}
        rows = [pr["done"], pr["total"]] if pr.get("total") else None
        add("Нарх базасига ўқиш", it.owner.email if it.owner else "", it.title, it.worker, it.created,
            ("narxbaza/intake.py", "read_upload"), pr.get("stage") or "навбатда", rows)
    for o in SiteObject.objects.filter(status=SiteObject.Status.PARSING).select_related("owner"):
        code = (("texnazor/services_acts.py", "prepare") if o.mode == SiteObject.Mode.ACTS
                else ("texnazor/services.py", "import_ofyorta"))
        add("Офёрта ўқиш", o.owner.email, o.name, o.worker, o.created, code)
    for d in MyOfyorta.objects.filter(status=MyOfyorta.Status.BUILDING).select_related("owner"):
        add("Менинг офёртам", d.owner.email, d.name, d.worker, d.created, ("texnazor/build.py", "make"))
    for d in ProjectOfyorta.objects.filter(status=ProjectOfyorta.Status.BUILDING).select_related("owner"):
        add("Папка офёртаси", d.owner.email, d.name, d.worker, d.created, ("jobs/ofyorta.py", "make"))
    return {"running": running, "waiting": sorted(waiting, key=lambda x: -(x["age"] or 0))}


def workers_state() -> dict:
    from .data import workers
    w = workers()
    return {"mode": w["mode"], "alive": w["alive"],
            "list": [{"name": x.name, "alive": x.alive, "threads": x.threads, "busy": x.busy, "done": x.done,
                      "started": x.started.isoformat(), "seen": _age(x.last_seen)} for x in w["list"][:8]]}


def alerts(st) -> list:
    """Муаммолар: [даража, тугун, матн]. Даража: bad — ҳозироқ эътибор; warn — кузатинг."""
    out = []
    srv, req, w, t, db = st["server"], st["requests"], st["workers"], st["tasks"], st["db"]
    if w["mode"] == "worker" and not w["alive"]:
        out.append(["bad", "worker", "Фон ишчиси ишламаяпти — нархлаш, папкалар ва офёрталар навбатда қолади. "
                                     "Серверда: systemctl status narxlash-worker"])
    old = [x for x in t["waiting"] if (x["age"] or 0) > 120]
    if old:
        out.append(["warn", "worker", f"{len(old)} та иш 2 дақиқадан кўп навбатда турибди (энг узоғи {old[0]['age'] // 60} дақ.)"])
    for d in srv.get("disks", []):
        if not d["same"] and d["pct"] >= 80:
            out.append(["bad" if d["pct"] >= 90 else "warn", "media",
                        f"Диск «{d['label']}» {d['pct']} % тўлган — бўш жой {d['free'] // 2**30} ГБ"])
    ram = srv.get("ram")
    if ram and ram["pct"] >= 90:
        out.append(["bad", "server", f"Хотира (RAM) {ram['pct']} % банд — сервер секинлашади"])
    if req["e5_15"]:
        out.append(["bad", "web", f"Охирги 15 дақиқада {req['e5_15']} та сўров хато (500) билан тугади — панел «Хатолар»"])
    if req["slow_15"]:
        out.append(["warn", "web", f"Охирги 15 дақиқада {req['slow_15']} та секин сўров ({req['slow_ms'] / 1000:g}+ с)"])
    lo = st.get("lo", {})
    if lo.get("waiting"):
        out.append(["warn", "lo", f"LibreOffice навбатида {lo['waiting']} та .xls файл кутяпти — бир вақтда "
                                  f"{lo.get('slots')} тадан очилади (фойдаланувчиларга «Сервер банд» деб кўрсатилган)"])
    for p in srv.get("procs", {}).get("lo", []):
        age = time.time() - p["since"] if p["since"] else 0
        if age > 120:
            out.append(["warn", "lo", f"LibreOffice (PID {p['pid']}) {int(age)} сониядан бери ишлаяпти — 180 с да тўхтатилади"])
    if db.get("max_conn") and db.get("conn_total", 0) > 0.8 * db["max_conn"]:
        out.append(["bad", "pg", f"Базага уланишлар {db['conn_total']} / {db['max_conn']}"])
    long_q = [q for q in db.get("active", []) if q["sec"] > 30]
    if long_q:
        out.append(["warn", "pg", f"Базада {len(long_q)} та сўров 30 сониядан узун ишлаяпти"])
    if db.get("error"):
        out.append(["bad", "pg", "База ҳолатини ўқиб бўлмади: " + db["error"]])
    ngx = st["nginx"]
    if ngx.get("readable"):
        c = ngx.get("codes", {})
        if c.get("502", 0) + c.get("504", 0):
            out.append(["bad", "nginx", f"Nginx сайтдан жавоб ололмади: 502 — {c.get('502', 0)}, 504 — {c.get('504', 0)} (15 дақ.)"])
        if c.get("429", 0) > 20:
            out.append(["warn", "nginx", f"{c['429']} та сўров тезлик чеклови (429) билан рад этилди — бот ёки ҳужум бўлиши мумкин"])
    elif not settings.DEBUG:
        out.append(["warn", "nginx", "Nginx журнали ўқилмади (" + ngx.get("error", "") + ") — deploy/README.md, 7-бўлим"])
    if not srv.get("psutil"):
        out.append(["warn", "server", "psutil ўрнатилмаган — RAM ва жараёнлар кўринмайди (pip install -r requirements.txt)"])
    for _code, text in st.get("backup", []):
        out.append(["warn", "backup", text])
    if st.get("errors_hour"):
        out.append(["warn", "web", f"Охирги соатда журналда {st['errors_hour']} та хато ёзуви бор (панел «Хатолар»)"])
    return out


def live_state() -> dict:
    from django.core.cache import cache
    got = cache.get("mon:live")
    if got:
        return got
    from django.contrib.auth import get_user_model
    from jobs.models import Batch, Job

    from . import monitor
    from .models import AccessStat, LoginSession, ServerLog
    from .sessions import online_since

    now = timezone.now()
    today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    hour = now.replace(minute=0, second=0, microsecond=0)
    kinds = {r["kind"]: r["n"] for r in AccessStat.objects.filter(hour=hour).values("kind").annotate(n=Sum("count"))}
    try:
        from .backup import problems
        backup = problems()
    except Exception:  # noqa: BLE001
        backup = []
    jobs_today = Job.objects.filter(created__gte=today)
    st = {
        "now": now.isoformat(),
        "gunicorn": _gunicorn(),
        "requests": monitor.requests_state(),
        "kinds": kinds,
        "server": monitor.server_state(),
        "db": monitor.db_state(),
        "cache": monitor.cache_state(),
        "lo": monitor.lo_state(),
        "nginx": monitor.nginx_state(),
        "media": monitor.media_sizes(),
        "workers": workers_state(),
        "tasks": tasks_state(),
        "online": LoginSession.objects.filter(ended__isnull=True, last_seen__gte=online_since()).values("user").distinct().count(),
        "today": {"priced": jobs_today.filter(status=Job.Status.DONE).count(),
                  "failed": jobs_today.filter(status=Job.Status.FAILED).count(),
                  "rows": jobs_today.filter(status=Job.Status.DONE).aggregate(n=Sum("rows"))["n"] or 0,
                  "uploads": Batch.objects.filter(created__gte=today).count()},
        "counts": {"users": get_user_model().objects.count(), "batches": Batch.objects.count(), "jobs": Job.objects.count()},
        "backup": [list(b) for b in backup],
        "errors_hour": ServerLog.objects.filter(level__gte=40, created__gte=now - timedelta(hours=1)).count(),
    }
    st["alerts"] = alerts(st)
    cache.set("mon:live", st, 2)
    return st


@superuser_required
def tizim_live(request):
    return JsonResponse(live_state())

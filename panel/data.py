"""Панел учун йиғма маълумотлар: ҳисоблагичлар, диск ҳажми, асл файллар, фондаги жараёнлар."""
import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max, Q
from django.utils import timezone

from jobs.folders import STALE_AFTER, is_working
from jobs.models import Batch, Job, Project

KINDS = {"folder": Project, "object": Batch, "smeta": Job}
KIND_LABELS = {"folder": "Папка", "object": "Объект", "smeta": "Смета"}
ACTIVE_STAGES = (Batch.Stage.QUEUED, Batch.Stage.CONVERTING, Batch.Stage.PRICING)


def top_level(kind):
    """Смета­чи юклаган ишлар (папка ичидаги қисмлар ва объект ичидаги сметалар — алоҳида эмас)."""
    if kind == "folder":
        return Project.objects.all()
    if kind == "object":
        return Batch.objects.filter(project__isnull=True)
    return Job.objects.filter(batch__isnull=True)


def get_work(kind, work_id):
    return top_level(kind).select_related("owner").get(id=work_id)


# ===================== диск =====================
def dir_size(path) -> int:
    total = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        total += dir_size(e.path)
                    elif e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def disk_by_user() -> dict:
    """{owner_id: байт} — ҳар бир фойдаланувчининг барча иш папкалари (5 дақиқа кешланади)."""
    def calc():
        out = {}
        for model in (Project, Batch, Job):
            for obj in model.objects.only("id", "owner_id"):
                out[obj.owner_id] = out.get(obj.owner_id, 0) + dir_size(obj.dir)
        return out
    return cache.get_or_set("panel:disk_by_user", calc, 300)


def disk_total() -> int:
    return cache.get_or_set("panel:disk_total", lambda: dir_size(settings.MEDIA_ROOT), 300)


# ===================== фойдаланувчилар бўйича ҳисоблагичлар =====================
def counts_by_user() -> dict:
    """{owner_id: {"folder": n, "object": n, "smeta": n, "jobs": n, "priced": n, "failed": n, "last": dt}}"""
    out = {}

    def put(owner_id, **kw):
        row = out.setdefault(owner_id, {"folder": 0, "object": 0, "smeta": 0, "jobs": 0, "priced": 0, "failed": 0,
                                        "running": 0, "last": None})
        last = kw.pop("last", None)
        row.update(kw)
        if last and (row["last"] is None or last > row["last"]):
            row["last"] = last

    for kind in KINDS:
        for r in top_level(kind).values("owner_id").annotate(n=Count("id"), last=Max("created")):
            put(r["owner_id"], **{kind: r["n"]}, last=r["last"])
    for r in Job.objects.values("owner_id").annotate(
            n=Count("id"), priced=Count("id", filter=Q(status=Job.Status.DONE)),
            failed=Count("id", filter=Q(status=Job.Status.FAILED))):
        put(r["owner_id"], jobs=r["n"], priced=r["priced"], failed=r["failed"])
    for r in Project.objects.filter(status=Project.Status.PROCESSING).values("owner_id").annotate(n=Count("id")):
        put(r["owner_id"], running=r["n"])
    return out


def per_day(qs, field="created", days=14):
    """Охирги ``days`` кун: ҳар куни нечта (графика учун)."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    counts = {start + timedelta(days=i): 0 for i in range(days)}
    for dt in qs.filter(**{f"{field}__date__gte": start}).values_list(field, flat=True):
        d = timezone.localtime(dt).date()
        if d in counts:
            counts[d] += 1
    peak = max(counts.values()) or 1
    return [{"date": d, "count": n, "h": round(100 * n / peak)} for d, n in counts.items()]


def uploads_per_day(days=14):
    """Юклашлар (папка, объект, алоҳида смета) — журналдан эмас, ишларнинг ўз санасидан."""
    rows = [per_day(top_level(k), days=days) for k in KINDS]
    counts = [sum(r[i]["count"] for r in rows) for i in range(days)]
    peak = max(counts) or 1
    return [{"date": rows[0][i]["date"], "count": n, "h": round(100 * n / peak)} for i, n in enumerate(counts)]


# ===================== асл файллар =====================
def _inside_media(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(Path(settings.MEDIA_ROOT).resolve())
    except OSError:
        return False


def _first(folder: Path, stem: str):
    found = sorted(folder.glob(f"{stem}.*")) if folder.is_dir() else []
    return found[0] if found else None


def originals(kind, obj) -> list:
    """Смета­чи юклаган асл файллар: [{"name", "path", "size", "abs" (Path ёки None), "skip"}].
    ``abs`` — дискда бор ва MEDIA ичида бўлса; юклаб олиш шу рўйхат индекси бўйича."""
    out = []
    if kind == "folder":
        for e in obj.files:
            p = obj.abs(f"src/{e['path']}") if not e.get("skip") else None
            out.append({"name": Path(e["path"]).name, "path": e["path"], "size": e.get("size"), "abs": p,
                        "skip": e.get("skip", "")})
    elif kind == "object":
        for i, f in enumerate(obj.files):
            p = _first(obj.abs("upload"), str(i))
            out.append({"name": f["name"], "path": f["name"], "size": None, "abs": p, "skip": ""})
    else:
        roles = [("local", obj.local_name)]
        if obj.res_name and obj.res_name != obj.local_name or _first(obj.abs("upload"), "res"):
            roles.append(("res", obj.res_name))
        for stem, name in roles:
            p = _first(obj.abs("upload"), stem)
            out.append({"name": name, "path": f"{'Локал' if stem == 'local' else 'Ресурс'}: {name}", "size": None,
                        "abs": p, "skip": ""})
    for f in out:
        p = f["abs"]
        if p is not None and (not p.is_file() or not _inside_media(p)):
            f["abs"] = None
        if f["abs"] is not None and f["size"] is None:
            f["size"] = f["abs"].stat().st_size
    return out


# ===================== фондаги жараёнлар =====================
STAGE_LABELS = {"converting": "Файллар очиляпти", "pricing": "Нархланяпти", "queued": "Навбатда"}


def avg_part_seconds() -> dict:
    """Битта қисмни очиш/нархлаш ўртача неча сония (бутун тизим бўйича, 5 дақиқа кешланади) — қолган вақт учун."""
    def calc():
        sums = {"converting": [0.0, 0], "pricing": [0.0, 0]}
        for d in Batch.objects.exclude(durations={}).values_list("durations", flat=True):
            for k, v in (d or {}).items():
                if k in sums and v:
                    sums[k][0] += v
                    sums[k][1] += 1
        return {k: (t / n if n else None) for k, (t, n) in sums.items()}
    return cache.get_or_set("panel:avg_part_seconds", calc, 300)


def processes():
    """Ҳозир фонда ишланаётган папкалар: ким, неча қисм қайси босқичда, ҳозир қайси файл,
    қанча вақт ўтди ва тахминан қанча қолди."""
    now = timezone.now()
    avg = avg_part_seconds()
    out = []
    for pr in Project.objects.filter(status=Project.Status.PROCESSING).select_related("owner").prefetch_related("parts"):
        parts = [p for p in pr.parts.all() if p.stage != Batch.Stage.EMPTY]
        stages = [p.stage for p in parts]
        counts = {k: stages.count(k) for k in ("queued", "converting", "pricing")}
        finished = len(parts) - sum(counts.values())
        current = [p for p in parts if p.stage in (Batch.Stage.CONVERTING, Batch.Stage.PRICING)]
        started = pr.run_started or pr.created
        # қолган вақт: шу папкада тугаган қисмлар ўртачаси, бўлмаса — тизим ўртачаси
        eta, known = 0.0, True
        for stage, n in (("converting", counts["queued"] + counts["converting"]), ("pricing", counts["pricing"])):
            if not n:
                continue
            own = [p.durations[stage] for p in parts if (p.durations or {}).get(stage)]
            per = sum(own) / len(own) if own else avg.get(stage)
            if per is None:
                known = False
                continue
            eta += per * n
        for c in current:  # бошланган қисмнинг ўтган вақти айрилади
            if c.stage_since:
                eta -= min((now - c.stage_since).total_seconds(), eta)
        alive = is_working(pr)
        out.append({
            "project": pr, "owner": pr.owner, "parts": len(parts), "finished": finished, "counts": counts,
            "percent": round(100 * finished / len(parts)) if parts else 100,
            "current": [{"part": c, "label": STAGE_LABELS.get(c.stage, c.get_stage_display()),
                         "since": (now - c.stage_since).total_seconds() if c.stage_since else None,
                         "step_for": (now - c.step_since).total_seconds() if c.step and c.step_since else None}
                        for c in current],
            "elapsed": (now - started).total_seconds(),
            "eta": max(eta, 0) if known and finished < len(parts) else None,
            "alive": alive, "stalled": not alive,
            "beat_age": (now - pr.heartbeat).total_seconds() if pr.heartbeat else None,
            "stop_requested": pr.stop_requested,
        })
    return out


def stalled_after_minutes() -> int:
    return int(STALE_AFTER.total_seconds() // 60)


def workers() -> dict:
    """Fondagi ishchilar: worker rejimida birortasi tirik bo'lmasa — navbat yurmaydi (panelda qizil ogohlantirish)."""
    from jobs.folders import worker_mode
    from jobs.models import Worker
    alive_after = timezone.now() - timedelta(seconds=30)
    rows = list(Worker.objects.all())
    for w in rows:
        w.alive = w.last_seen >= alive_after
    return {"mode": "worker" if worker_mode() else "thread", "list": rows,
            "alive": sum(1 for w in rows if w.alive),
            "queued": Project.objects.filter(status=Project.Status.PROCESSING, worker="").count()}


def live() -> dict:
    """Умумий саҳифанинг «ҳозир» блоки (ҳар 3 сонияда янгиланади)."""
    from .models import Event, LoginSession
    from .sessions import online_since
    now = timezone.now()
    today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    procs = processes()
    users, seen = [], set()
    for s in (LoginSession.objects.filter(ended__isnull=True, last_seen__gte=online_since())
              .select_related("user").order_by("-last_seen")):
        if s.user_id not in seen:  # фойдаланувчининг энг сўнгги сессияси
            seen.add(s.user_id)
            users.append(s)
    jobs_today = Job.objects.filter(created__gte=today)
    return {
        "now": now, "procs": procs, "online": users, "workers": workers(),
        "parts_queued": sum(p["counts"]["queued"] for p in procs),
        "parts_opening": sum(p["counts"]["converting"] for p in procs),
        "parts_pricing": sum(p["counts"]["pricing"] for p in procs),
        "today_uploads": sum(top_level(k).filter(created__gte=today).count() for k in KINDS),
        "today_priced": jobs_today.filter(status=Job.Status.DONE).count(),
        "today_failed": jobs_today.filter(status=Job.Status.FAILED).count(),
        "today_logins": Event.objects.filter(kind=Event.Kind.LOGIN, created__gte=today).count(),
        "today_failed_logins": Event.objects.filter(kind=Event.Kind.LOGIN_FAILED, created__gte=today).count(),
        "stale_minutes": stalled_after_minutes(),
    }


# ===================== тезлик: файл очиш ва нархлаш =====================
def _per_mb(seconds, sizes):
    mb = sum(sizes) / (1024 * 1024)
    return sum(seconds) / mb if mb else None


def speed(days: int) -> dict:
    """Файлларни очиш (.xls → .xlsx, ўқиш, таҳлил) ва нархлаш тезлиги — охирги ``days`` кун."""
    from jobs.models import FileTiming
    since = timezone.now() - timedelta(days=days)
    files = list(FileTiming.objects.filter(created__gte=since).select_related("owner"))
    conv = [f for f in files if f.convert_s is not None]
    ok = [f for f in files if not f.error]

    by_ext = []
    for ext in sorted({f.ext for f in files}):
        fs = [f for f in ok if f.ext == ext]
        if fs:
            by_ext.append({"ext": ext, "n": len(fs), "avg": _avg([f.total_s for f in fs]),
                           "per_mb": _per_mb([f.total_s for f in fs], [f.size or 0 for f in fs]),
                           "size": _avg([f.size or 0 for f in fs])})
    by_method = []
    for m in sorted({f.method for f in conv if f.method}):
        fs = [f for f in conv if f.method == m and not f.error]
        if fs:
            by_method.append({"method": m, "n": len(fs), "avg": _avg([f.convert_s for f in fs]),
                              "per_mb": _per_mb([f.convert_s for f in fs], [f.size or 0 for f in fs])})

    jobs = list(Job.objects.filter(created__gte=since, status=Job.Status.DONE, duration__isnull=False)
                .select_related("owner").defer("result").order_by("-duration"))
    rows_jobs = [j for j in jobs if j.rows]
    per_1000 = (1000 * sum(j.duration for j in rows_jobs) / sum(j.rows for j in rows_jobs)) if rows_jobs else None
    # ҳажм бўйича гуруҳлар: катта сметалар секинми
    buckets = []
    for lo, hi, label in ((0, 500, "500 гача"), (500, 2000, "500–2 000"), (2000, 5000, "2 000–5 000"),
                          (5000, 10**9, "5 000 дан кўп")):
        js = [j for j in rows_jobs if lo <= j.rows < hi]
        if js:
            buckets.append({"label": label, "n": len(js), "avg": _avg([j.duration for j in js]),
                            "per_1000": 1000 * sum(j.duration for j in js) / sum(j.rows for j in js)})

    return {
        "files": len(files), "converted": len(conv), "errors": [f for f in files if f.error][:10],
        "n_errors": sum(1 for f in files if f.error),
        "size_total": sum(f.size or 0 for f in files),
        "time_total": sum(f.total_s for f in files),
        "convert_avg": _avg([f.convert_s for f in conv if not f.error]),
        "convert_max": max((f.convert_s for f in conv), default=None),
        "convert_per_mb": _per_mb([f.convert_s for f in conv if not f.error], [f.size or 0 for f in conv if not f.error]),
        "read_avg": _avg([f.read_s for f in ok if f.read_s is not None]),
        "analyze_avg": _avg([f.analyze_s for f in ok if f.analyze_s is not None]),
        "open_avg": _avg([f.total_s for f in ok]),
        "by_ext": by_ext, "by_method": by_method,
        "slow_files": sorted(files, key=lambda f: f.total_s, reverse=True)[:10],
        "recent": files[:60],
        "per_day": _series([f.created for f in files], days),
        "jobs": len(jobs), "price_avg": _avg([j.duration for j in jobs]), "per_1000": per_1000, "per_100": per_1000 / 10 if per_1000 is not None else None,
        "rows_total": sum(j.rows for j in rows_jobs), "buckets": buckets, "slow_jobs": jobs[:10],
    }


# ===================== статистика =====================
def _series(dts, days):
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    counts = {start + timedelta(days=i): 0 for i in range(days)}
    for dt in dts:
        d = timezone.localtime(dt).date()
        if d in counts:
            counts[d] += 1
    peak = max(counts.values()) or 1
    return [{"date": d, "count": n, "h": round(100 * n / peak)} for d, n in counts.items()]


def _breakdown(qs, field):
    rows = list(qs.values_list(field).annotate(n=Count("id")).order_by("-n")[:6])
    total = sum(n for _, n in rows) or 1
    return [{"key": k or "—", "n": n, "pct": round(100 * n / total)} for k, n in rows]


def _avg(xs):
    return sum(xs) / len(xs) if xs else None


def stats(days: int) -> dict:
    """Статистика саҳифаси: охирги ``days`` кун."""
    from django.contrib.auth import get_user_model

    from .models import Event, LoginSession
    User = get_user_model()
    since = timezone.now() - timedelta(days=days)
    uploads = [dt for k in KINDS for dt in top_level(k).filter(created__gte=since).values_list("created", flat=True)]
    jobs = Job.objects.filter(created__gte=since)
    done = jobs.filter(status=Job.Status.DONE)
    logins = list(Event.objects.filter(kind=Event.Kind.LOGIN, created__gte=since).values_list("created", flat=True))

    by_user, match = {}, {}
    for owner_id, total, st in done.values_list("owner_id", "result__totals__s_nds", "result__stats"):
        r = by_user.setdefault(owner_id, {"n": 0, "sum": 0.0})
        r["n"] += 1
        r["sum"] += total if isinstance(total, (int, float)) else 0
        for k, v in (st or {}).items():
            if isinstance(v, (int, float)):
                match[k] = match.get(k, 0) + v
    users = {u.pk: u for u in User.objects.filter(pk__in=by_user)}
    top = sorted(({"user": users[k], **v} for k, v in by_user.items() if k in users),
                 key=lambda r: r["n"], reverse=True)[:8]
    shown = {k: v for k, v in match.items() if v and k not in ("NOT_PRICED", "CLEARED")}
    match_total = sum(shown.values()) or 1

    durs = sorted(d for d in done.values_list("duration", flat=True) if d)
    part_durs = list(Batch.objects.filter(project__isnull=False, created__gte=since).exclude(durations={})
                     .values_list("project_id", "durations"))
    slow = {}
    for pid, d in part_durs:
        slow[pid] = slow.get(pid, 0) + sum(v for v in d.values() if v)
    slow_ids = sorted(slow, key=slow.get, reverse=True)[:6]
    slow_projects = Project.objects.select_related("owner").in_bulk(slow_ids)

    hours = [0] * 24
    for dt in uploads + logins:
        hours[timezone.localtime(dt).hour] += 1
    hpeak = max(hours) or 1
    sess = LoginSession.objects.filter(created__gte=since)
    return {
        "uploads": _series(uploads, days), "priced": _series(done.values_list("created", flat=True), days),
        "logins": _series(logins, days),
        "regs": _series(User.objects.filter(date_joined__gte=since).values_list("date_joined", flat=True), days),
        "n_uploads": len(uploads), "n_jobs": jobs.count(), "n_done": done.count(),
        "n_failed": jobs.filter(status=Job.Status.FAILED).count(), "n_logins": len(logins),
        "n_regs": User.objects.filter(date_joined__gte=since).count(),
        "total_sum": sum(r["sum"] for r in by_user.values()),
        "match": sorted(({"key": k, "n": v, "pct": round(100 * v / match_total, 1)} for k, v in shown.items()),
                        key=lambda r: r["n"], reverse=True),
        "not_priced": match.get("NOT_PRICED", 0),
        "dur_avg": _avg(durs), "dur_med": durs[len(durs) // 2] if durs else None, "dur_max": durs[-1] if durs else None,
        "open_avg": _avg([d["converting"] for _, d in part_durs if d.get("converting")]),
        "price_avg": _avg([d["pricing"] for _, d in part_durs if d.get("pricing")]),
        "slow": [{"project": slow_projects[i], "secs": slow[i]} for i in slow_ids if i in slow_projects],
        "hours": [{"h": h, "count": n, "pct": round(100 * n / hpeak)} for h, n in enumerate(hours)],
        "devices": _breakdown(sess, "device"), "browsers": _breakdown(sess, "browser"), "oses": _breakdown(sess, "os"),
        "top": top,
    }

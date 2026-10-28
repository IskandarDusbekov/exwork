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
def processes():
    """Ҳозир ишланаётган папкалар ва уларнинг қисмлари (ким, қайси босқич, қачон охирги белги)."""
    now = timezone.now()
    out = []
    for pr in Project.objects.filter(status=Project.Status.PROCESSING).select_related("owner").prefetch_related("parts"):
        parts = list(pr.parts.all())
        stages = [p.stage for p in parts]
        finished = sum(1 for s in stages if s not in ACTIVE_STAGES)
        current = next((p for p in parts if p.stage in (Batch.Stage.CONVERTING, Batch.Stage.PRICING)), None)
        alive = is_working(pr)
        out.append({
            "project": pr, "owner": pr.owner, "parts": len(parts), "finished": finished,
            "percent": round(100 * finished / len(parts)) if parts else 100,
            "queued": stages.count(Batch.Stage.QUEUED), "current": current,
            "alive": alive, "stalled": not alive,
            "beat_age": (now - pr.heartbeat) if pr.heartbeat else None,
            "stop_requested": pr.stop_requested,
        })
    return out


def stalled_after_minutes() -> int:
    return int(STALE_AFTER.total_seconds() // 60)

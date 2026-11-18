"""Қатор ҳисоби (``RowUsage``): ҳар смета нечта қаторли ва нечтасига ишлов берилди, фойдаланувчи бўйича
ҳафта / ой / жами. Келажакда тўлов шунга қараб ҳисобланади — шунинг учун ёзувлар смета ўчирилса ҳам қолади."""
from datetime import datetime, time as dtime, timedelta

from django.db.models import Count, F, Sum
from django.utils import timezone

# нархлаш ҳолати эмас — ҳисоб устунлари (``result.stats``да бор, лекин алоҳида қатор эмас)
_NOT_ROW = {"ZERO_RULE", "NON_NUMERIC", "NOT_PRICED"}


def processed_rows(result: dict) -> int:
    """Ишлов берилган қаторлар: ресурс деб танилиб, нархлашдан ўтгани (ҳолати бор) — бўш ва сарлавҳасиз
    қаторлар эмас. Эски (compat) режимда ҳолат қаторга ёзилмаган бўлса — ``stats``дан."""
    rows = result.get("rows") or []
    n = sum(1 for r in rows if r.get("status"))
    if not n:
        n = sum(v for k, v in (result.get("stats") or {}).items() if k not in _NOT_ROW and isinstance(v, int))
    return min(n, len(rows)) if rows else n


def _where(job):
    if job.batch_id:
        project_id = getattr(job.batch, "project_id", None)
        return ("folder", project_id) if project_id else ("object", job.batch_id)
    return "smeta", job.id


def record(job):
    """Нархлангандан кейин: ёзув бўлмаса — яратилади (сана — ҳозир), бўлса — сонлари янгиланади."""
    from .models import RowUsage
    total, done = job.rows or 0, job.rows_done or 0
    title = (job.batch_label or job.local_name or "")[:255]
    if RowUsage.objects.filter(job_id=job.id).update(rows_total=total, rows_done=done, title=title,
                                                     runs=F("runs") + 1, updated=timezone.now()):
        return
    kind, work_id = _where(job)
    RowUsage.objects.get_or_create(job_id=job.id, defaults=dict(
        owner_id=job.owner_id, created=timezone.now(), kind=kind, work_id=work_id, title=title,
        rows_total=total, rows_done=done))


def period_starts(now=None) -> dict:
    """Маҳаллий вақт бўйича: бугун, шу ҳафта (душанбадан), шу ой (1-кундан) бошланиши."""
    now = timezone.localtime(now or timezone.now())
    day = timezone.make_aware(datetime.combine(now.date(), dtime.min), now.tzinfo)
    return {"day": day, "week": day - timedelta(days=now.weekday()), "month": day.replace(day=1)}


def _agg(qs) -> dict:
    a = qs.aggregate(done=Sum("rows_done"), total=Sum("rows_total"), n=Count("id"))
    return {"done": a["done"] or 0, "total": a["total"] or 0, "n": a["n"] or 0}


def summary(owner=None) -> dict:
    """{"day"/"week"/"month"/"all": {"done", "total", "n" (смета сони)}} — битта фойдаланувчи ёки ҳамма."""
    from .models import RowUsage
    qs = RowUsage.objects.all() if owner is None else RowUsage.objects.filter(owner=owner)
    out = {k: _agg(qs.filter(created__gte=v)) for k, v in period_starts().items()}
    out["all"] = _agg(qs)
    return out


def by_user(since=None) -> dict:
    """{owner_id: {"done", "total", "n"}} — ``since``дан бери (None — ҳаммаси)."""
    from .models import RowUsage
    qs = RowUsage.objects.all() if since is None else RowUsage.objects.filter(created__gte=since)
    return {r["owner_id"]: {"done": r["done"] or 0, "total": r["total"] or 0, "n": r["n"]}
            for r in qs.values("owner_id").annotate(done=Sum("rows_done"), total=Sum("rows_total"), n=Count("id"))}


def by_month(owner=None, months: int = 12) -> list:
    """Охирги ойлар: [{"month": сана, "done", "total", "n"}] — янгиси биринчи."""
    from django.db.models.functions import TruncMonth
    from .models import RowUsage
    qs = RowUsage.objects.all() if owner is None else RowUsage.objects.filter(owner=owner)
    start = period_starts()["month"]
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    rows = (qs.filter(created__gte=start).annotate(m=TruncMonth("created")).values("m")
            .annotate(done=Sum("rows_done"), total=Sum("rows_total"), n=Count("id")).order_by("-m"))
    return [{"month": r["m"], "done": r["done"] or 0, "total": r["total"] or 0, "n": r["n"]} for r in rows]

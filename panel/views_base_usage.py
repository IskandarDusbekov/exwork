"""Панел → «Ресурс базаси» → «Базадан нархлаш»: RES'сиз (ресурс базасидан) нархланган сметалар — ҳар бири базада
неча фоиз нархини топгани ва умумий ўртача фоиз."""
from django.db.models.fields.json import KT
from django.shortcuts import render

from jobs.models import Job
from resbase.regions import REGION_LABELS

from .views import staff_required
from .views_base import _bctx

LIMIT = 300  # охирги шунча смета


def _pct(n, total):
    return round(100 * n / total, 1) if total else None


@staff_required
def base_usage(request):
    # натижанинг ўзи (минглаб қатор) ўқилмайди — фақат stats ичидаги иккита сон (SQLite JSON)
    qs = (Job.objects.filter(status=Job.Status.DONE, res_path="", mode="smart")
          .exclude(base_period={}).select_related("owner", "batch", "batch__project")
          .annotate(n_base=KT("result__stats__BASE"), n_left=KT("result__stats__NOT_PRICED"))
          .only("id", "created", "local_name", "local_sheet", "base_period", "owner__email", "owner__first_name",
                "owner__last_name", "batch__name", "batch__project__name", "sum_total")
          .order_by("-created")[:LIMIT])
    rows = []
    for j in qs:
        base, left = int(j.n_base or 0), int(j.n_left or 0)
        if not base and not left:
            continue
        p = j.base_period or {}
        rows.append({"job": j, "base": base, "left": left, "total": base + left, "pct": _pct(base, base + left),
                     "period": f'{REGION_LABELS.get(p.get("region"), "—")} · {p.get("year") or "?"}-{p.get("quarter") or "?"}'})
    n_base, n_all = sum(r["base"] for r in rows), sum(r["total"] for r in rows)
    pcts = [r["pct"] for r in rows if r["pct"] is not None]
    summary = {"jobs": len(rows), "base": n_base, "total": n_all,
               "weighted": _pct(n_base, n_all),  # ҳамма қатор бўйича
               "average": round(sum(pcts) / len(pcts), 1) if pcts else None}  # сметалар ўртачаси
    return render(request, "panel/base_usage.html", _bctx(request, rows=rows, summary=summary, limit=LIMIT))

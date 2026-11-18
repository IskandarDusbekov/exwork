"""«Қатор ҳисоби»: ким қанча қатор ишлади — давр бўйича (бугун, ҳафта, ой, ўтган ой, аниқ ой, ҳаммаси).
Келажакда тўлов шунга қараб: ёзувлар ``jobs.RowUsage``да, смета ўчирилса ҳам қолади (jobs/usage.py)."""
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from django.shortcuts import render
from django.utils import timezone

from jobs import usage
from jobs.models import RowUsage

from .views import _ctx, staff_required

MONTHS = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
          "Ноябрь", "Декабрь"]


def month_label(d) -> str:
    return f"{MONTHS[d.month]} {d.year}"


def _period(key: str):
    """(бошланиши, тугаши, номи) — тугаши None бўлса ҳозиргача."""
    st = usage.period_starts()
    if key == "day":
        return st["day"], None, "Бугун"
    if key == "week":
        return st["week"], None, "Шу ҳафта (душанбадан)"
    if key == "prev":
        end = st["month"]
        start = (end - timedelta(days=1)).replace(day=1)
        return start, end, f"Ўтган ой — {month_label(start)}"
    if key == "all":
        return None, None, "Бутун вақт"
    if len(key) == 7 and key[4] == "-":  # 2026-10
        try:
            start = timezone.make_aware(datetime(int(key[:4]), int(key[5:]), 1))
        except ValueError:
            return _period("month")
        end = (start + timedelta(days=32)).replace(day=1)
        return start, end, month_label(start)
    return st["month"], None, f"Шу ой — {month_label(st['month'])}"


@staff_required
def usage_page(request):
    key = request.GET.get("p", "month")
    start, end, label = _period(key)
    qs = RowUsage.objects.all()
    if start:
        qs = qs.filter(created__gte=start)
    if end:
        qs = qs.filter(created__lt=end)
    per = list(qs.values("owner_id").annotate(done=Sum("rows_done"), total=Sum("rows_total"), n=Count("id"),
                                              works=Count("work_id", distinct=True)).order_by("-done"))
    users = get_user_model().objects.in_bulk([r["owner_id"] for r in per])
    rows = [{**r, "user": users.get(r["owner_id"])} for r in per if users.get(r["owner_id"])]
    total = {"done": sum(r["done"] or 0 for r in rows), "total": sum(r["total"] or 0 for r in rows),
             "n": sum(r["n"] for r in rows)}
    top = rows[0]["done"] if rows else 0
    for r in rows:
        r["share"] = round(100 * (r["done"] or 0) / top) if top else 0
    months = usage.by_month(months=12)
    for m in months:
        m["key"], m["label"] = m["month"].strftime("%Y-%m"), month_label(m["month"])
    now = usage.summary()
    now_cards = [(k, l, now[k]) for k, l in (("day", "Бугун"), ("week", "Шу ҳафта"), ("month", "Шу ой"), ("all", "Бутун вақт"))]
    return render(request, "panel/usage.html", _ctx(
        request, "usage", key=key, label=label, rows=rows, total=total, months=months, now_cards=now_cards,
        periods=[("day", "Бугун"), ("week", "Ҳафта"), ("month", "Шу ой"), ("prev", "Ўтган ой"), ("all", "Ҳаммаси")]))

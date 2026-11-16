"""Панел → «Фойдаланувчи йўли»: смета юклашдан натижагача одамлар қаерга етди, қаерда тўхтади, қайтиб келдими."""
from django.contrib.auth import get_user_model
from django.shortcuts import render

from . import track
from .models import Track
from .views import _ctx, staff_required

PERIODS = [(7, "7 кун"), (30, "30 кун"), (90, "90 кун")]


@staff_required
def track_page(request):
    try:
        days = int(request.GET.get("days") or 30)
    except ValueError:
        days = 30
    days = days if days in dict(PERIODS) else 30
    staff = request.GET.get("staff") == "1"
    rep = track.report(days, staff)
    ids = {u for s in rep["stopped"] for u in s["users"]} | {e[0] for e in rep["errors"]}
    names = {u.pk: u for u in get_user_model().objects.filter(pk__in=ids)}
    for s in rep["stopped"]:
        s["people"] = [names[u] for u in s["users"] if u in names]
    rep["errors"] = [{"user": names.get(e[0]), "when": e[3], "detail": e[4]} for e in rep["errors"]]
    recent = (Track.objects.select_related("user").exclude(**({} if staff else {"user__is_staff": True}))[:40])
    return render(request, "panel/track.html", _ctx(
        request, "track", rep=rep, days=days, staff=staff, periods=PERIODS,
        recent=[{"t": t, "label": track.STEPS.get(t.step, (t.step,))[0]} for t in recent]))

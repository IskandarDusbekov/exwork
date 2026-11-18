from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .services import mark_read


@login_required
@require_POST
def read(request):
    """Ойна ёпилди ёки қўнғироқча очилди: ``ids`` (бир нечта) — ўқилган деб белгиланади."""
    ids = [int(x) for x in request.POST.getlist("ids") if x.isdigit()][:50]
    return JsonResponse({"ok": True, "n": mark_read(request.user, ids)})

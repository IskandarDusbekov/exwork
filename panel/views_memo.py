"""Панел → «Эслаб қолинган листлар»: смета­чилар тизимга қўлда тушунтирган листлар (``jobs.SheetMemo``) — ким,
қайси файлда, қандай белгилаган, неча марта ўзи қўлланди. Кўриш — ``is_staff``, ўчириш — ``is_superuser``."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from openpyxl.utils import get_column_letter

from jobs.models import Batch, SheetMemo

from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

ROLES = {"local": ("Локал", "bg-emerald-100 text-emerald-800"), "resource": ("Ресурс", "bg-sky-100 text-sky-800"),
         "unknown": ("Керак эмас", "bg-slate-100 text-slate-600"), "": ("тури ўзгармаган", "bg-slate-100 text-slate-600")}
COLS = [("name", "Ном"), ("unit", "Бирлик"), ("qty_unit", "Миқдор (бирликка)"), ("qty_total", "Миқдор (жами)"),
        ("price", "Нарх")]


def memo_view(m: SheetMemo) -> dict:
    """Эслатма одам ўқийдиган кўринишда: устунлар ҳарф билан, биринчи қатор — қандай сақлангани."""
    c = m.columns or {}
    cols = [{"key": k, "label": label, "letter": get_column_letter(int(c[k]))} for k, label in COLS if c.get(k)]
    if c.get("start_off"):
        start = f"«1 2 3…» рақамлаш қаторидан {c['start_off']} та пастда"
    elif c.get("start"):
        start = f"{c['start']}-қатор"
    else:
        start = ""
    file_name, _sep, sheet = (m.sample or "").rpartition(" · ")
    return {"m": m, "role": ROLES.get(m.role, ROLES[""]), "cols": cols, "start": start,
            "file": file_name or m.sample, "sheet": sheet}


def _filters(get) -> dict:
    role = get.get("role", "")
    return {"q": get.get("q", "").strip()[:100], "role": role if role in ("local", "resource", "unknown") else "",
            "user": get.get("user", "") if get.get("user", "").isdigit() else ""}


@staff_required
def memos(request):
    f = _filters(request.GET)
    qs = SheetMemo.objects.select_related("owner")
    if f["q"]:
        qs = qs.filter(Q(sample__icontains=f["q"]) | Q(owner__email__icontains=f["q"]) |
                       Q(owner__first_name__icontains=f["q"]) | Q(owner__last_name__icontains=f["q"]))
    if f["role"]:
        qs = qs.filter(role=f["role"])
    if f["user"]:
        qs = qs.filter(owner_id=int(f["user"]))
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    rows = [memo_view(m) for m in page.object_list]
    # намунани очиш: эслатма кўрсатилган объект ҳали бор бўлса — ўша файл ва лист панелдаги Excel кўринишида
    batches = {b.pk: b for b in Batch.objects.filter(pk__in={m.batch_id for m in page.object_list if m.batch_id})}
    for r in rows:
        b = batches.get(r["m"].batch_id)
        s = b.sheet(r["m"].sheet_key) if b else None
        if s:
            r["open"] = reverse("panel:work_sheet", args=["object", b.pk, s["file"]]) + "?s=" + s["sheet"]
            r["work"] = reverse("panel:work", args=["object", b.pk])
    total = SheetMemo.objects.aggregate(n=Count("id"), uses=Sum("uses"), users=Count("owner", distinct=True))
    by_role = dict(SheetMemo.objects.values_list("role").annotate(n=Count("id")))
    return render(request, "panel/memos.html", _ctx(
        request, "memos", rows=rows, page=page, f=f, total=total, by_role=by_role,
        users=SheetMemo.objects.values("owner_id", "owner__email").annotate(n=Count("id")).order_by("-n")[:30]))


@superuser_required
@require_POST
def memo_delete(request, pk):
    m = get_object_or_404(SheetMemo, pk=pk)
    audit(request, Event.Kind.ADMIN_SETTINGS, target=m.owner, detail=f"эслатма ўчирилди: {m.sample}"[:500])
    m.delete()
    messages.success(request, "Эслатма ўчирилди: шундай лист кейин келса, тизим уни яна ўзи ўқийди.")
    nxt = request.POST.get("next", "")
    return redirect(nxt if nxt.startswith(reverse("panel:memos")) else reverse("panel:memos"))

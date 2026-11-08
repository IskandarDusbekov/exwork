"""Панел → «Ёрдам хизмати»: барча суҳбатлар (ўқилмаганлар сони билан), суҳбатни очиш, жавоб ёзиш, «ҳал қилинди».
Кўриш — ``is_staff``, жавоб ва ёпиш — ``is_superuser``. Ҳар бир амал журналга ёзилади."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from support import services
from support.models import Conversation
from support.views import attach_url

from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

FILTERS = (("open", "Очиқ"), ("unread", "Жавоб кутаётган"), ("closed", "Ҳал қилинган"), ("all", "Ҳаммаси"))


@staff_required
def support_list(request):
    f = request.GET.get("f", "open")
    f = f if f in dict(FILTERS) else "open"
    q = request.GET.get("q", "").strip()[:100]
    qs = (Conversation.objects.select_related("user")
          .annotate(unread=Count("messages", filter=Q(messages__from_staff=False, messages__read_at__isnull=True)),
                    total=Count("messages"), last_at=Max("messages__created")))
    if f == "open":
        qs = qs.filter(status=Conversation.Status.OPEN)
    elif f == "closed":
        qs = qs.filter(status=Conversation.Status.CLOSED)
    elif f == "unread":
        qs = qs.filter(unread__gt=0)
    if q:
        qs = qs.filter(Q(user__email__icontains=q) | Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q))
    page = Paginator(qs.order_by("-unread", "-updated"), 30).get_page(request.GET.get("page"))
    for c in page:
        last = c.messages.order_by("-created", "-pk").first()
        c.last = last
    counts = {
        "open": Conversation.objects.filter(status=Conversation.Status.OPEN).count(),
        "unread": Conversation.objects.filter(messages__from_staff=False, messages__read_at__isnull=True).distinct().count(),
        "closed": Conversation.objects.filter(status=Conversation.Status.CLOSED).count(),
        "all": Conversation.objects.count(),
    }
    return render(request, "panel/support.html", _ctx(request, "support", page=page, f=f, q=q, filters=FILTERS,
                                                       counts=counts))


@never_cache
@staff_required
def support_chat(request, pk):
    conv = get_object_or_404(Conversation.objects.select_related("user", "closed_by"), pk=pk)
    if services.mark_read(conv, by_staff=True):
        audit(request, Event.Kind.ADMIN_SUPPORT, target=conv.user, detail="ёрдам хизмати: суҳбат ўқилди")
    msgs = list(conv.messages.select_related("author").order_by("created", "pk"))
    for m in msgs:
        m.url = attach_url(m) if m.attachment else ""
    return render(request, "panel/support_chat.html", _ctx(
        request, "support", conv=conv, msgs=msgs, max_text=services.MAX_TEXT,
        max_mb=services.MAX_FILE // 2**20, last_id=msgs[-1].pk if msgs else 0))


@never_cache
@require_GET
@staff_required
def support_poll(request, pk):
    """Очиқ турган суҳбатга фойдаланувчидан янги хабар келдими (JS 15 сонияда сўрайди)."""
    conv = get_object_or_404(Conversation, pk=pk)
    try:
        after = max(0, int(request.GET.get("after") or 0))
    except ValueError:
        after = 0
    new = list(conv.messages.filter(pk__gt=after).order_by("created", "pk"))
    if new:
        services.mark_read(conv, by_staff=True)
    return JsonResponse({"messages": [services.as_json(m, attach_url) for m in new]})


@require_POST
@superuser_required
def support_reply(request, pk):
    conv = get_object_or_404(Conversation.objects.select_related("user"), pk=pk)
    try:
        m = services.post(conv, request.user, request.POST.get("text", ""), request.FILES.get("file"), from_staff=True)
    except services.SupportError as e:
        messages.error(request, str(e))
        return redirect("panel:support_chat", pk=conv.pk)
    services.mark_read(conv, by_staff=True)
    mailed = services.notify_reply(request, conv)
    audit(request, Event.Kind.ADMIN_SUPPORT, target=conv.user,
          detail=f"ёрдам хизмати: жавоб ёзилди ({len(m.text)} белги"
                 f"{', файл «' + m.attachment_name + '»' if m.attachment else ''}{', хат юборилди' if mailed else ''})")
    if request.POST.get("close") == "1":
        _close(request, conv)
    messages.success(request, "Жавоб юборилди." + (" Фойдаланувчига хат ҳам кетди." if mailed else ""))
    return redirect("panel:support_chat", pk=conv.pk)


def _close(request, conv):
    conv.status, conv.closed_at, conv.closed_by = Conversation.Status.CLOSED, timezone.now(), request.user
    conv.save(update_fields=["status", "closed_at", "closed_by"])
    audit(request, Event.Kind.ADMIN_SUPPORT, target=conv.user, detail="ёрдам хизмати: «ҳал қилинди» деб ёпилди")


@require_POST
@superuser_required
def support_status(request, pk):
    conv = get_object_or_404(Conversation.objects.select_related("user"), pk=pk)
    action = request.POST.get("action")
    if action == "close":
        _close(request, conv)
        messages.success(request, "Суҳбат «ҳал қилинди» деб ёпилди. Фойдаланувчи яна ёзса, ўзи қайта очилади.")
    elif action == "open":
        conv.status, conv.closed_at, conv.closed_by = Conversation.Status.OPEN, None, None
        conv.save(update_fields=["status", "closed_at", "closed_by"])
        audit(request, Event.Kind.ADMIN_SUPPORT, target=conv.user, detail="ёрдам хизмати: суҳбат қайта очилди")
        messages.success(request, "Суҳбат қайта очилди.")
    return redirect("panel:support_chat", pk=conv.pk)

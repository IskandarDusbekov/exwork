"""Фойдаланувчи томони: «Ёрдам» ойнаси (JSON) ва тўлиқ саҳифа. Ҳар ким фақат ўз суҳбатини кўради —
суҳбат ҳеч қачон URL'дан олинмайди, доим ``request.user`` дан."""
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import services
from .models import Message


def attach_url(m: Message) -> str:
    return reverse("support:file", args=[m.pk])


def _no_inspect(request):
    if getattr(request, "viewing_owner", None):  # панелдан «текширув режими»да — бировнинг номидан ёзилмасин
        raise Http404


@require_GET
def page(request):
    """Алоҳида саҳифа йўқ: хатдаги ҳавола «Иш столи»ни очади, суҳбат ойнаси бурчакда ўзи очилади (#support)."""
    return redirect(reverse("dashboard") + "#support")


@never_cache
@require_GET
def messages_json(request):
    """Тарих (ёки ``after`` дан кейингилари). Очилганда администратор жавоблари ўқилди деб белгиланади."""
    _no_inspect(request)
    conv = services.chat_of(request.user, create=False)
    if conv is None:
        return JsonResponse({"messages": [], "status": "open", "unread": 0})
    try:
        after = max(0, int(request.GET.get("after") or 0))
    except ValueError:
        after = 0
    # охирги 200 таси (эскидан янгига)
    qs = list(conv.messages.filter(pk__gt=after).order_by("-created", "-pk")[:200])[::-1]
    if request.GET.get("seen") == "1":
        services.mark_read(conv, by_staff=False)
    return JsonResponse({"messages": [services.as_json(m, attach_url) for m in qs],
                         "status": conv.status, "unread": services.user_unread(request.user)})


@never_cache
@require_GET
def unread_json(request):
    """Тугмадаги белги учун (ойна ёпиқ турганда дақиқада бир сўралади)."""
    return JsonResponse({"unread": services.user_unread(request.user)})


@require_POST
def send(request):
    _no_inspect(request)
    wait = services.rate_wait(request.user)
    if wait:
        return JsonResponse({"error": f"Жуда тез ёзяпсиз. {wait} сониядан кейин яна юборинг."}, status=429)
    conv = services.chat_of(request.user)
    try:
        m = services.post(conv, request.user, request.POST.get("text", ""), request.FILES.get("file"),
                          from_staff=False)
    except services.SupportError as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"message": services.as_json(m, attach_url)})


@never_cache
@require_GET
def attachment(request, pk):
    """Бириктирилган файл: фақат суҳбат эгасига ёки ходимга."""
    m = get_object_or_404(Message.objects.select_related("conversation"), pk=pk)
    if not m.attachment:
        raise Http404
    u = request.user
    if m.conversation.user_id != u.pk and not (u.is_active and u.is_staff):
        raise Http404
    if u.is_staff and m.conversation.user_id != u.pk:
        from panel.audit import log as audit
        from panel.models import Event
        audit(request, Event.Kind.ADMIN_DOWNLOAD, target=m.conversation.user,
              detail=f"ёрдам хизмати: файл «{m.attachment_name}»")
    try:
        fh = m.attachment.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404
    # Расм (ичи текширилган) — суҳбатда кўринади; қолгани доим юклаб олинади (браузерда очилмайди)
    inline = m.is_image and request.GET.get("dl") != "1"
    resp = FileResponse(fh, as_attachment=not inline, filename=m.attachment_name or "file")
    resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp

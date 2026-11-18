"""Панел → «Эълонлар»: янгилик, жиддий хабар ёки табрик ёзиш. Чоп этилгач фойдаланувчи кирганда бир марта
ойнада чиқади ва сарлавҳадаги қўнғироқчада туради (news/). Кўриш — ``is_staff``; ёзиш — суперфойдаланувчи."""
from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from news.models import Announcement

from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

STYLE_HINTS = {
    "news": "Янги имконият, ўзгариш, сайт янгиланди — кўк, хотиржам.",
    "serious": "Муҳим огоҳлантириш, техник ишлар, қоида ўзгарди — тўқ, жиддий.",
    "celebrate": "Байрам табриги, ютуқ, раҳмат — рангли, конфетти билан.",
}


@staff_required
def news_page(request):
    items = list(Announcement.objects.annotate(n_read=Count("reads")).order_by("-created"))
    edit = None
    pk = request.GET.get("edit", "")
    if pk.isdigit():
        edit = next((a for a in items if a.pk == int(pk)), None)
    from django.contrib.auth import get_user_model
    draft = edit or Announcement(style="news", title="Сарлавҳа шу ерда кўринади",
                                 body="Матн ёзинг. Бўш қатор — янги хатбоши.\n\n- «- » билан бошланган қаторлар\n- рўйхат бўлади",
                                 show_modal=True, published_at=timezone.now())
    return render(request, "panel/news.html", _ctx(
        request, "news", items=items, edit=edit, draft=draft, styles=Announcement.Style.choices,
        style_hints=STYLE_HINTS, users_n=get_user_model().objects.filter(is_active=True).count()))


def _fill(a: Announcement, post):
    a.title = post.get("title", "").strip()[:160] or "Эълон"
    a.body = post.get("body", "").strip()[:5000]
    a.style = post.get("style") if post.get("style") in Announcement.Style.values else "news"
    url = post.get("link_url", "").strip()[:300]
    a.link_url = url if url.startswith(("/", "https://", "http://")) else ""
    a.link_text = post.get("link_text", "").strip()[:60]
    a.show_modal = post.get("show_modal") == "1"
    ends = post.get("ends_at", "").strip()
    dt = parse_datetime(ends) if ends else None
    a.ends_at = timezone.make_aware(dt) if dt and timezone.is_naive(dt) else dt


@require_POST
@superuser_required
def news_save(request):
    pk = request.POST.get("id", "")
    a = get_object_or_404(Announcement, pk=int(pk)) if pk.isdigit() else Announcement(author=request.user)
    _fill(a, request.POST)
    action = request.POST.get("action", "draft")
    if action == "publish":
        a.publish()
    elif action == "unpublish":
        a.published = False
    a.save()
    word = {"publish": "чоп этилди", "unpublish": "яширилди"}.get(action, "сақланди")
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Эълон: «{a.title[:120]}» {word}")
    messages.success(request, f"«{a.title}» {word}." + (" Фойдаланувчилар кирганда бир марта кўради." if action == "publish" and a.show_modal else ""))
    return redirect(reverse("panel:news") + f"?edit={a.pk}")


@require_POST
@superuser_required
def news_delete(request, pk):
    a = get_object_or_404(Announcement, pk=pk)
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Эълон: «{a.title[:120]}» ўчирилди")
    a.delete()
    messages.success(request, "Эълон ўчирилди.")
    return redirect("panel:news")

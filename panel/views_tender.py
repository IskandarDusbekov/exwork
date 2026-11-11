"""Панел → «Объект таҳлили»: ёқиш/ўчириш, таҳлил нимага асосланиши (чегаралар, ҳақиқий харажат) ва
админнинг тажриба омиллари. Кўриш — ``is_staff``, ўзгартириш — фақат ``is_superuser`` (журналга ёзилади)."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from jobs import tender
from jobs.models import Job, TenderFactor, TenderSettings

from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required


def _groups(values: dict) -> dict:
    out = {}
    for key, label, help_, unit, default, lo, hi, group in tender.PARAMS:
        out.setdefault(group, []).append({"key": key, "label": label, "help": help_, "unit": unit, "default": default,
                                          "value": values.get(key, default), "bool": key == "norm_in_cost",
                                          "changed": float(values.get(key, default)) != float(default)})
    return out


@staff_required
def tender_page(request):
    s = TenderSettings.objects.filter(pk=1).first()
    values = (s.params if s else {}) or {}
    recent = (Job.objects.filter(status=Job.Status.DONE).select_related("owner")
              .only("id", "local_name", "batch_label", "created", "owner__email", "sum_total").order_by("-created")[:8])
    return render(request, "panel/tender.html", _ctx(
        request, "tender", s=s, enabled=bool(s and s.enabled), groups=_groups(values),
        factors=TenderFactor.objects.all(), scopes=TenderFactor.Scope.choices, recent=recent))


@require_POST
@superuser_required
def tender_save(request):
    try:
        params = tender.parse_params(request.POST)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("panel:tender")
    s, _ = TenderSettings.objects.get_or_create(pk=1)
    was = s.enabled
    s.enabled = bool(request.POST.get("enabled"))
    s.params, s.updated_by = params, request.user
    s.save()
    tender.forget_config()
    state = "" if was == s.enabled else (" · ёқилди" if s.enabled else " · ўчирилди")
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Объект таҳлили сақланди{state}")
    messages.success(request, "Сақланди — ярим дақиқа ичида ҳамма таҳлилларда ишлайди.")
    return redirect("panel:tender")


def _factor_from(post, f: TenderFactor) -> TenderFactor:
    title = " ".join(str(post.get("title", "")).split())[:200]
    if not title:
        raise ValueError("Омил номини ёзинг")
    scope = post.get("scope", "all")
    if scope not in TenderFactor.Scope.values:
        raise ValueError("Омил қаерга таъсир қилишини танланг")
    try:
        effect = float(str(post.get("effect", "0")).replace(",", ".").replace(" ", "") or 0)
    except ValueError:
        raise ValueError("Таъсир — сон бўлиши керак (масалан −8 ёки 5)")
    if not -90 <= effect <= 300:
        raise ValueError("Таъсир −90% дан 300% гача")
    f.title, f.scope, f.effect = title, scope, round(effect, 2)
    f.active = bool(post.get("active"))
    f.visible = bool(post.get("visible"))
    f.user_text = " ".join(str(post.get("user_text", "")).split())[:300]
    f.note = str(post.get("note", ""))[:5000]
    try:
        f.order = max(0, min(999, int(post.get("order") or 0)))
    except ValueError:
        f.order = 0
    return f


@require_POST
@superuser_required
def tender_factor(request, pk=None):
    f = get_object_or_404(TenderFactor, pk=pk) if pk else TenderFactor()
    if request.POST.get("delete") and pk:
        audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Таҳлил омили ўчирилди: {f.title}")
        f.delete()
        tender.forget_config()
        messages.success(request, "Омил ўчирилди.")
        return redirect("panel:tender")
    try:
        _factor_from(request.POST, f).save()
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("panel:tender")
    tender.forget_config()
    audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Таҳлил омили {'ўзгартирилди' if pk else 'қўшилди'}: {f.title}")
    messages.success(request, "Омил сақланди.")
    return redirect("panel:tender")

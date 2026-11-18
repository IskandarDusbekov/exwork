"""Панел → «Бош саҳифа матнлари»: очиқ бош саҳифа (landing) матнлари — коддан ўзгартирмасдан. Кўриш — ``is_staff``;
ўзгартириш — суперфойдаланувчи. Матнлар: ``jobs.site`` (SiteSetting "landing"). Мақола ва видеолар — ``views_blog``."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from jobs import site

from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required


@staff_required
def site_page(request):
    cur, over = site.content(), site.stored()
    blocks = []
    for b in site.BLOCKS:
        d = cur[b.key]
        rows = [r[1:] if b.icons else r for r in d.get("rows", [])]
        blocks.append({
            "b": b, "changed": b.key in over,
            "fields": [{"key": k, "label": label, "default": default, "value": d[k], "long": len(default) > 70}
                       for k, label, default in b.fields],
            "cols": list(enumerate(b.cols)),
            "rows": [list(enumerate(r)) for r in rows] + ([[(i, "") for i in range(len(b.cols))]] if b.cols else []),
        })
    return render(request, "panel/site.html", _ctx(
        request, "site", blocks=blocks, changed=len(over), max_rows=site.MAX_ROWS))


@require_POST
@superuser_required
def landing_save(request):
    if request.POST.get("reset") == "1":
        from . import conf
        conf.put("landing", {}, request.user)
        audit(request, Event.Kind.ADMIN_SETTINGS, detail="Бош саҳифа матнлари асл ҳолига қайтарилди")
        messages.success(request, "Бош саҳифа матнлари асл ҳолига қайтарилди")
        return redirect("panel:site")
    changed = site.save(request.POST, request.user)
    if changed:
        audit(request, Event.Kind.ADMIN_SETTINGS, detail=("Бош саҳифа: " + ", ".join(changed))[:200])
    messages.success(request, f"Сақланди: {', '.join(changed)}" if changed else "Ўзгариш йўқ")
    return redirect("panel:site")


# ===================== иловадаги чап меню =====================
@staff_required
def menu_page(request):
    off = site.side_hidden()
    groups = {}
    for k, label, g in site.SIDE_ITEMS:
        groups.setdefault(g, []).append({"key": k, "label": label, "on": k not in off})
    return render(request, "panel/menu.html", _ctx(request, "menu", groups=list(groups.items()), n_off=len(off)))


@require_POST
@superuser_required
def menu_save(request):
    from . import conf
    shown = set(request.POST.getlist("show"))
    off = [k for k, _l, _g in site.SIDE_ITEMS if k not in shown]
    if sorted(off) != sorted(site.side_hidden()):
        conf.put("side_hidden", off, request.user)
        names = {k: label for k, label, _g in site.SIDE_ITEMS}
        audit(request, Event.Kind.ADMIN_SETTINGS,
              detail=("Чап меню, яширилган: " + (", ".join(names[k] for k in off) or "йўқ"))[:200])
        messages.success(request, "Сақланди. Меню ярим дақиқа ичида ҳамма учун янгиланади.")
    else:
        messages.success(request, "Ўзгариш йўқ")
    return redirect("panel:menu")

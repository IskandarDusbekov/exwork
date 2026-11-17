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

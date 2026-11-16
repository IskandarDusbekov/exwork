"""Панел → «Ёрдам матнлари ва видео» (6-босқич): «?» изоҳлари ва /help/ саҳифасидаги матнларни коддан
ўзгартирмасдан тўғрилаш, видео дарслар рўйхати. Кўриш — ``is_staff``; ўзгартириш — суперфойдаланувчи.
Сақланади: ``SiteSetting`` "help_texts" ({калит: [номи, нимани билдиради, нима қилиш керак]} — фақат ўзгарганлари)
ва "videos" ([{title, id, page, note}]). Бўш майдон — коддаги матн."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from jobs.guide import HELP, HELP_GROUPS, VIDEO_PAGES, help_texts, videos, youtube_id

from . import conf
from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

PARTS = ("номи", "нимани билдиради", "нима қилиш керак")
MAX_VIDEOS = 60


@staff_required
def help_texts_page(request):
    texts = help_texts()
    groups = [(title, [{"key": k, "base": HELP[k], "cur": texts[k], "changed": texts[k] != HELP[k],
                        "fields": list(zip(range(3), PARTS, HELP[k], texts[k]))} for k in keys])
              for title, keys in HELP_GROUPS]
    listed = {k for _, keys in HELP_GROUPS for k in keys}
    other = [k for k in HELP if k not in listed]  # гуруҳга кирмаганлари ҳам (фақат «?» да чиқади)
    if other:
        groups.append(("Бошқа изоҳлар", [{"key": k, "base": HELP[k], "cur": texts[k], "changed": texts[k] != HELP[k],
                                          "fields": list(zip(range(3), PARTS, HELP[k], texts[k]))} for k in other]))
    rows = videos() + [{"title": "", "id": "", "page": "", "note": ""}]  # охиридаги бўш — янгисини қўшиш учун
    return render(request, "panel/help_texts.html", _ctx(
        request, "help_texts", groups=groups, videos=rows, pages=VIDEO_PAGES,
        changed=sum(1 for _, rs in groups for r in rs if r["changed"])))


@require_POST
@superuser_required
def help_texts_save(request):
    over, changed = {}, []
    for k, base in HELP.items():
        vals = [request.POST.get(f"t_{k}_{i}", "").strip() for i in range(3)]
        if not any(f"t_{k}_{i}" in request.POST for i in range(3)):
            continue
        vals = [v if v and v != base[i] else "" for i, v in enumerate(vals)]
        if any(vals):
            over[k] = vals
    old = conf.raw("help_texts", {}) or {}
    for k in set(old) | set(over):
        if old.get(k) != over.get(k):
            changed.append(HELP[k][0] if k in HELP else k)
    conf.put("help_texts", over, request.user)
    if changed:
        audit(request, Event.Kind.ADMIN_SETTINGS, detail="Ёрдам матнлари: " + ", ".join(changed)[:180])
    messages.success(request, f"Сақланди: {len(changed)} та изоҳ ўзгарди" if changed else "Ўзгариш йўқ")
    return redirect("panel:help_texts")


@require_POST
@superuser_required
def videos_save(request):
    titles, urls = request.POST.getlist("v_title"), request.POST.getlist("v_url")
    pages, notes = request.POST.getlist("v_page"), request.POST.getlist("v_note")
    valid_pages = {k for k, _ in VIDEO_PAGES}
    out, bad = [], []
    for i, url in enumerate(urls):
        url, title = url.strip(), (titles[i] if i < len(titles) else "").strip()
        if not url and not title:
            continue  # бўш қатор
        vid = youtube_id(url)
        if not vid:
            bad.append(title or url)
            continue
        page = pages[i] if i < len(pages) and pages[i] in valid_pages else ""
        out.append({"title": (title or "Видео дарс")[:120], "id": vid, "page": page,
                    "note": (notes[i] if i < len(notes) else "").strip()[:300]})
    if bad:
        messages.error(request, "YouTube ҳаволаси эмас — сақланмади: " + ", ".join(bad)[:200])
        return redirect("panel:help_texts")
    out = out[:MAX_VIDEOS]
    if out != (conf.raw("videos", []) or []):
        conf.put("videos", out, request.user)
        audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Видео дарслар: {len(out)} та")
        messages.success(request, f"Видео рўйхати сақланди ({len(out)} та)")
    else:
        messages.success(request, "Ўзгариш йўқ")
    return redirect("panel:help_texts")

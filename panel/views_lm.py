"""Панел → «Ускуна ва кабель базаси»: ўзи ўрганганлари, админ ўргатиши (бирма-бир ва Excel), тузатиш.
Кўриш — ``is_staff``, ўзгартириш — фақат ``is_superuser`` (журналга ёзилади)."""
import json
import threading
import uuid
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import close_old_connections
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from jobs.services import UploadError, _check_upload, _save
from resbase import lm
from resbase.models import LM_CATEGORIES, LMEntry

from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

CATS = dict(LM_CATEGORIES)
UPLOAD_KINDS = [("M", "Ҳаммаси — ускуна"), ("L", "Ҳаммаси — кабель"), ("K", "Ҳаммаси — ускуна эмас (материал)"),
                ("res", "RES файл — бўлимлари бўйича («ОБОРУДОВАНИЕ» → ускуна, «КАБЕЛЬ» → кабель)")]
HARVEST_KEY = "panel:lm:harvest"
MAX_PREVIEW = 1400  # ҳар номга 2 та форма майдони — DATA_UPLOAD_MAX_NUMBER_FIELDS (3000) дан ошмасин


def _dir() -> Path:
    d = Path(settings.MEDIA_ROOT) / "lm_uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _back(request, default="panel:lm"):
    nxt = request.POST.get("next", "")
    return redirect(nxt if nxt.startswith(reverse("panel:lm")) else reverse(default))


def _filters(get) -> dict:
    cat = get.get("cat", "")
    src = get.get("src", "")
    return {"q": get.get("q", "").strip()[:100], "cat": cat if cat in ("M", "L", "K", "?") else "",
            "src": src if src in ("auto", "locked", "conflict") else ""}


@staff_required
def lm_page(request):
    f = _filters(request.GET)
    qs = LMEntry.objects.all()
    if f["q"]:
        qs = qs.filter(Q(name__icontains=f["q"]) | Q(key__icontains=lm.lm_key(f["q"])) | Q(code=f["q"]))
    if f["cat"] == "?":
        qs = qs.filter(category="")
    elif f["cat"]:
        qs = qs.filter(category=f["cat"])
    if f["src"] == "auto":
        qs = qs.filter(locked=False)
    elif f["src"] == "locked":
        qs = qs.filter(locked=True)
    page = Paginator(qs.order_by("-locked", "name"), 50).get_page(request.GET.get("page"))
    rows = list(page)
    if f["src"] == "conflict":  # овозлари қарама-қарши — Python'да (JSON майдон)
        rows = [e for e in qs.order_by("name") if len({k[-1] for k in (e.votes or {})}) > 1][:200]
        page = None
    for e in rows:
        e.reason = (lm._reason(e) if e.category else
                    lm._reason(e) + " — битта смета­чи танлови, тасдиқланг" if lm.needs_confirm(e) else
                    "овозлар тенг — аниқ эмас")
        e.mixed = len({k[-1] for k in (e.votes or {})}) > 1
    mixed = sum(1 for v in LMEntry.objects.values_list("votes", flat=True) if len({k[-1] for k in (v or {})}) > 1)
    base = {k: v for k, v in f.items() if v}
    return render(request, "panel/lm.html", _ctx(
        request, "lm", f=f, rows=rows, page=page, stats=lm.stats(), mixed=mixed, cats=LM_CATEGORIES, upload_kinds=UPLOAD_KINDS,
        harvest=cache.get(HARVEST_KEY), qs_base=urlencode(base),
        link=lambda **kw: "?" + urlencode({**base, **kw})))


@require_POST
@superuser_required
def lm_teach(request):
    name, cat = request.POST.get("name", "").strip(), request.POST.get("category", "")
    e = lm.teach(name, cat, request.POST.get("unit", "").strip(), request.POST.get("code", "").strip())
    if e:
        audit(request, Event.Kind.ADMIN_BASE, detail=f"ускуна базаси: «{e.name}» → {CATS[cat]}")
        messages.success(request, f"Ўргатилди: «{e.name}» — {CATS[cat].lower()}.")
    else:
        messages.success(request, "Номи ва турини киритинг.")
    return _back(request)


@require_POST
@superuser_required
def lm_set(request, pk):
    e = get_object_or_404(LMEntry, pk=pk)
    cat = request.POST.get("category", "")
    if cat == "auto":
        lm.unlock(e)
        detail = "ўзи ўрганганига қайтарилди"
    elif cat in CATS:
        lm.teach(e.name, cat, e.unit, e.code)
        detail = CATS[cat]
    else:
        raise Http404
    audit(request, Event.Kind.ADMIN_BASE, detail=f"ускуна базаси: «{e.name}» → {detail}")
    return _back(request)


@require_POST
@superuser_required
def lm_delete(request, pk):
    e = get_object_or_404(LMEntry, pk=pk)
    audit(request, Event.Kind.ADMIN_BASE, detail=f"ускуна базаси: «{e.name}» ўчирилди")
    e.delete()
    messages.success(request, f"Ўчирилди: «{e.name}». Кейинги сметаларда яна учраса — қайта ўрганади.")
    return _back(request)


@require_POST
@superuser_required
def lm_upload(request):
    f, kind = request.FILES.get("file"), request.POST.get("kind", "")
    if not f or kind not in dict(UPLOAD_KINDS):
        messages.success(request, "Файл ва турини танланг.")
        return redirect("panel:lm")
    try:
        ext = _check_upload(f, f.name)
    except UploadError as e:
        messages.success(request, str(e))
        return redirect("panel:lm")
    token = uuid.uuid4().hex
    path = _dir() / f"{token}{ext}"
    _save(f, path)
    try:
        items = lm.parse_excel(path, kind)
    except Exception as e:  # noqa: BLE001 — бузуқ файл
        messages.success(request, f"Файлни ўқиб бўлмади: {e}")
        return redirect("panel:lm")
    finally:
        path.unlink(missing_ok=True)
    if not items:
        messages.success(request, "Файлда ресурс номи топилмади." + (" RES файлда нархли ресурс жадвали бўлиши керак." if kind == "res" else ""))
        return redirect("panel:lm")
    if len(items) > MAX_PREVIEW:
        messages.success(request, f"Файлда {len(items)} та ном бор — бир марта биринчи {MAX_PREVIEW} таси олинади. "
                                  "Қолганини файлни бўлиб юкланг.")
        items = items[:MAX_PREVIEW]
    have = {e.key: e for e in LMEntry.objects.filter(key__in=[lm.lm_key(i["name"]) for i in items])}
    for i in items:
        e = have.get(lm.lm_key(i["name"]))
        i["now"] = e.category if e else ""
        i["now_locked"] = bool(e and e.locked)
    (_dir() / f"{token}.json").write_text(json.dumps({"file": f.name, "kind": kind, "items": items}, ensure_ascii=False),
                                          encoding="utf-8")
    return redirect("panel:lm_preview", token=token)


def _pending(token):
    if not token.isalnum():
        raise Http404
    p = _dir() / f"{token}.json"
    if not p.is_file():
        raise Http404("Юклаш топилмади ёки эскирган — файлни қайта юкланг")
    return p, json.loads(p.read_text(encoding="utf-8"))


@superuser_required
def lm_preview(request, token):
    p, data = _pending(token)
    if request.method == "POST":
        if request.POST.get("action") == "cancel":
            p.unlink(missing_ok=True)
            messages.success(request, "Бекор қилинди — ҳеч нарса ўзгармади.")
            return redirect("panel:lm")
        take = []
        for i, it in enumerate(data["items"]):
            cat = request.POST.get(f"c{i}", "")
            if request.POST.get(f"t{i}") and cat in CATS:
                take.append({**it, "category": cat})
        out = lm.teach_many(take, LMEntry.Origin.EXCEL, note=data["file"][:200])
        p.unlink(missing_ok=True)
        audit(request, Event.Kind.ADMIN_BASE, detail=f"ускуна базаси: «{data['file']}» — {len(take)} та ўргатилди")
        messages.success(request, f"«{data['file']}»: {out.get('new', 0)} та янги, {out.get('changed', 0)} та ўзгарди, "
                                  f"{out.get('same', 0)} та аввал ҳам шундай эди.")
        return redirect("panel:lm")
    counts = {c: sum(1 for i in data["items"] if i["category"] == c) for c in CATS}
    return render(request, "panel/lm_preview.html", _ctx(
        request, "lm", data=data, token=token, cats=LM_CATEGORIES, counts=counts,
        kind_label=dict(UPLOAD_KINDS)[data["kind"]]))


def _harvest_thread():
    close_old_connections()
    try:
        def progress(n, total):
            cache.set(HARVEST_KEY, {"running": True, "n": n, "total": total}, 3600)
        out = lm.harvest_all(progress)
        cache.set(HARVEST_KEY, {"running": False, **out}, 3600)
    except Exception as e:  # noqa: BLE001
        cache.set(HARVEST_KEY, {"running": False, "error": str(e)}, 3600)
    finally:
        close_old_connections()


@require_POST
@superuser_required
def lm_harvest(request):
    st = cache.get(HARVEST_KEY) or {}
    if not st.get("running"):
        cache.set(HARVEST_KEY, {"running": True, "n": 0, "total": 0}, 3600)
        threading.Thread(target=_harvest_thread, daemon=True, name="lm-harvest").start()
        audit(request, Event.Kind.ADMIN_BASE, detail="ускуна базаси: ҳамма сметалардан қайта йиғиш")
    messages.success(request, "Ҳамма нархланган сметалардан қайта йиғиляпти — бир оздан кейин саҳифани янгиланг.")
    return redirect("panel:lm")

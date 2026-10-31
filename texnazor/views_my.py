"""«Менинг офёрталарим»: narxlash loyihasidagi narxlangan lokallardan ofyorta tuzish (fonda), yuklab olish va
texnazorda shu ofyortadan obyekt ochish."""
import shutil

from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from jobs.models import Project
from panel.audit import log as _log
from panel.models import Event

from . import build, services, tasks
from .models import MyOfyorta

MAX_SHOWN_PROBLEMS = 300


def audit(request, doc, text: str):
    _log(request, Event.Kind.TEXNAZOR, work=doc, work_kind="tn_my", detail=f"Менинг офёртам «{doc.name[:200]}»: {text}")


def _doc(request, doc_id) -> MyOfyorta:
    return get_object_or_404(MyOfyorta, pk=doc_id, owner=request.user)


def my_list(request):
    docs = list(MyOfyorta.objects.filter(owner=request.user).select_related("project"))
    return render(request, "texnazor/my_list.html", {"nav": "texnazor", "docs": docs,
                                                     "projects": build.projects_for(request.user)})


def _pct(text) -> float:
    try:
        v = float(str(text).replace(",", ".").replace("%", "").strip() or 0)
    except ValueError:
        raise ValueError(f"«{text}» — фоиз сон бўлиши керак")
    if not 0 <= v <= 100:
        raise ValueError(f"«{text}» — фоиз 0 дан 100 гача бўлиши керак")
    return round(v / 100, 6)


def _save(request, doc: MyOfyorta, setup: dict) -> MyOfyorta:
    """Sozlash formasi -> ofyorta (fonda tuziladi)."""
    post = request.POST
    parts = []
    for p in setup["parts"]:
        title = " ".join(post.get(f"title_{p['job']}", p["title"]).split())[:300] or p["title"]
        parts.append({"job": p["job"], "title": title, "include": post.get(f"include_{p['job']}") == "1"})
    if not any(p["include"] for p in parts):
        raise ValueError("Камида битта локал танланг")
    from jobs.services import UploadError, check_quota
    try:
        check_quota(request.user)
    except UploadError as e:
        raise ValueError(str(e))
    keys = {e["key"] for e in setup["equipment"]}
    doc.name = (post.get("name") or "").strip()[:300] or doc.name or "Офёрта"
    doc.object_type = (post.get("object_type") or "").strip()[:200]
    doc.parts = parts
    doc.customer = [k for k in post.getlist("customer") if k in keys]
    doc.rates = {k: _pct(post.get(f"rate_{k}", "")) for k in build.RATE_LABELS}
    doc.status, doc.error, doc.progress = MyOfyorta.Status.BUILDING, "", {"step": "Навбатда"}
    new = doc._state.adding
    doc.save()
    tasks.start(doc.pk)
    audit(request, doc, "тузилмоқда" if new else "қайта тузилмоқда")
    return doc


def _setup_page(request, project, doc=None, error=None):
    data = build.setup(project, doc)
    if not data["parts"]:
        raise Http404("Лойиҳада нархланган смета йўқ")
    return render(request, "texnazor/my_setup.html", {"nav": "texnazor", "project": project, "doc": doc, "s": data,
                                                      "error": error, "name": doc.name if doc else project.name,
                                                      "object_type": doc.object_type if doc else ""})


def my_new(request):
    pid = request.GET.get("project") or request.POST.get("project")
    if not pid:
        return redirect("texnazor:my_list")
    project = get_object_or_404(Project, pk=pid, owner=request.user)
    if request.method == "POST":
        try:
            doc = _save(request, MyOfyorta(owner=request.user, project=project), build.setup(project))
            return redirect("texnazor:my_detail", doc_id=doc.pk)
        except ValueError as e:
            return _setup_page(request, project, error=str(e))
    return _setup_page(request, project)


def my_edit(request, doc_id):
    doc = _doc(request, doc_id)
    if doc.project is None:
        messages.error(request, "Нархлаш лойиҳаси ўчирилган — офёртани қайта тузиб бўлмайди")
        return redirect("texnazor:my_detail", doc_id=doc.pk)
    if request.method == "POST":
        try:
            _save(request, doc, build.setup(doc.project, doc))
            return redirect("texnazor:my_detail", doc_id=doc.pk)
        except ValueError as e:
            return _setup_page(request, doc.project, doc, error=str(e))
    return _setup_page(request, doc.project, doc)


def my_detail(request, doc_id):
    doc = _doc(request, doc_id)
    blocks = [s["title"] for s in doc.structure if s.get("block")]
    parts, notes = {}, []
    for p in doc.problems or []:
        if p.get("kind") == "note":  # ma'lumot uchun: lokaldagi qo'lda yozilgan miqdor (summa lokaldagidek)
            notes.append(p)
        else:
            parts.setdefault(p.get("part") or "", []).append(p)
    return render(request, "texnazor/my_detail.html", {
        "nav": "texnazor", "doc": doc, "blocks": blocks, "sections": len(doc.structure) - len(blocks),
        "problems": list(parts.items()), "problems_total": sum(len(v) for v in parts.values()), "notes": notes,
        "rates": [(label, (doc.rates or {}).get(k, 0.0)) for k, label in build.RATE_LABELS.items()],
        "objects": list(doc.site_objects.filter(owner=request.user)),
    })


def my_state(request, doc_id):
    doc = _doc(request, doc_id)
    return JsonResponse({"status": doc.status, "error": doc.error, "progress": doc.progress})


def my_file(request, doc_id):
    doc = _doc(request, doc_id)
    if doc.status != MyOfyorta.Status.READY or not doc.file.exists():
        raise Http404("Офёрта ҳали тайёр эмас")
    audit(request, doc, "Excel юклаб олинди")
    resp = FileResponse(open(doc.file, "rb"), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = content_disposition_header(True, f"Офёрта {doc.name[:150]}.xlsx")
    return resp


@require_POST
def my_delete(request, doc_id):
    doc = _doc(request, doc_id)
    audit(request, doc, "ўчирилди")
    shutil.rmtree(doc.dir, ignore_errors=True)
    doc.delete()  # undan ochilgan obyektlar qoladi (made_from = NULL)
    messages.success(request, "Офёрта ўчирилди")
    return redirect("texnazor:my_list")

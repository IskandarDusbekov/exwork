"""Папка офёртаси: нархланган сметаларни смета­чи ўзи тартиблайди (папка ичида сметалар, папкалар ичида
папкалар), сариқ сарлавҳаларни ёзади — «Офёрта тузиш» фонда битта Excel'ни йиғади (``jobs.ofyorta``)."""
import json

from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from panel.audit import log as audit
from web.i18n import ui
from web import brand as brandfile
from billing import gate
from panel.models import Event

from . import ofyorta
from .models import Batch, Project, ProjectOfyorta
from .views import _audit_download, _get_work, crumbs_for
from django.utils.translation import gettext as _


def _work(request, project_id=None, batch_id=None):
    """Папка ёки алоҳида юкланган объект: (иш, URL олд қўшимчаси, қайтиш манзили, номи)."""
    if project_id:
        w = _get_work(request, Project, id=project_id)
        return w, "project", reverse("project", args=[w.id]), "folder"
    w = _get_work(request, Batch, id=batch_id)
    return w, "batch", reverse("batch", args=[w.id]), "object"


def _urls(kind, work):
    return {k: reverse(f"{kind}_ofyorta{'_' + k if k != 'page' else ''}", args=[work.id]) for k in ("page", "save", "state", "file")}


def _state(doc: ProjectOfyorta) -> dict:
    """Тузилиш ҳолати. ``job`` — ҳозир қўшилаётган смета, ``since`` — «Офёрта тузиш» босилган пайт
    (прогресс ``.update()`` билан ёзилади, ``updated`` ўзгармайди)."""
    p = doc.progress or {}
    done, total = p.get("done", 0), p.get("total", 0)
    return {"status": doc.status, "label": doc.get_status_display(), "error": ui(doc.error),
            "step": ui(p.get("step", "")), "done": done, "total": total, "job": p.get("job", ""),
            "pct": round(100 * done / total) if total else 0,
            "since": doc.updated.isoformat() if doc.updated else ""}


def ofyorta_page(request, project_id=None, batch_id=None):
    project, kind, back, _k = _work(request, project_id, batch_id)
    own = project.owner_id == request.user.pk
    doc = ofyorta.for_project(project, save=own)
    jobs = {str(j.pk): j for j in ofyorta.priced_jobs(project)}
    included = [p for p in doc.parts if p["include"]]
    crumbs = (crumbs_for(project=project) if kind == "project" else crumbs_for(batch=project)) + [{"label": _("Офёрта"), "url": "", "icon": "sheet", "color": "text-amber-500"}]
    return render(request, "jobs/ofyorta.html", {
        "crumbs": crumbs, "project": project, "doc": doc, "urls": _urls(kind, project), "back": back,
        "is_folder": kind == "project", "nodes": ofyorta.tree(doc.parts, jobs), "own": own,
        "count": len(doc.parts), "count_in": len(included),
        "sum_in": sum(jobs[p["job"]].sum_total or 0 for p in included if p["job"] in jobs),
        "state": _state(doc), "has_file": doc.status == ProjectOfyorta.Status.READY and bool(doc.file),
    })


def _parse(raw: str, allowed: set) -> list:
    """Саҳифадан келган тартиб: ``[{"job", "title", "include"}]`` — фақат шу папканинг нархланган сметалари."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        raise ValueError(_("Тартиб нотўғри юборилди — саҳифани янгиланг"))
    if not isinstance(data, list):
        raise ValueError(_("Тартиб нотўғри юборилди — саҳифани янгиланг"))
    out, seen = [], set()
    for p in data[:2000]:
        if not isinstance(p, dict):
            continue
        key = str(p.get("job", ""))
        if key in allowed and key not in seen:
            seen.add(key)
            out.append({"job": key, "title": " ".join(str(p.get("title") or "").split())[:300],
                        "include": bool(p.get("include"))})
    return out


@require_POST
def ofyorta_save(request, project_id=None, batch_id=None):
    project, kind, _b, _k = _work(request, project_id, batch_id)  # POST — фақат эгасига
    doc = ofyorta.for_project(project)
    url = _urls(kind, project)["page"]
    if doc.status == ProjectOfyorta.Status.BUILDING:
        messages.info(request, _("Офёрта ҳозир тузиляпти — тугагач, тартибни ўзгартиришингиз мумкин."))
        return redirect(url)
    try:
        parts = _parse(request.POST.get("parts"), {p["job"] for p in doc.parts})
    except ValueError as e:
        messages.error(request, str(e))
        return redirect(url)
    doc.parts = ofyorta.sync_parts(project, parts)
    doc.name = " ".join(request.POST.get("name", "").split())[:300] or project.name[:300]
    build = request.POST.get("action") == "build"
    if build:
        if not any(p["include"] for p in doc.parts):
            doc.save(update_fields=["parts", "name", "updated"])
            messages.error(request, _("Камида битта сметани белгиланг."))
            return redirect(url)
        doc.status, doc.error = ProjectOfyorta.Status.BUILDING, ""
        doc.progress = {"step": "Навбатда", "done": 0, "total": sum(p["include"] for p in doc.parts)}  # i18n: ok
        doc.worker, doc.heartbeat = "", None
    doc.save()
    if build:
        from texnazor import tasks
        audit(request, Event.Kind.TEXNAZOR, detail=f"{'Папка' if kind == 'project' else 'Объект'} офёртаси тузилмоқда: {project.name} "  # i18n: ok
                                                    f"({doc.progress['total']} та смета)")  # i18n: ok
        tasks.start(doc.pk)
    else:
        messages.success(request, _("Тартиб сақланди."))
    return redirect(url)


def ofyorta_state(request, project_id=None, batch_id=None):
    project, kind, _b, _k = _work(request, project_id, batch_id)
    doc = ProjectOfyorta.objects.filter(**{kind: project}).first()
    if doc is None:
        raise Http404(_("Офёрта йўқ"))
    return JsonResponse(_state(doc))


def ofyorta_file(request, project_id=None, batch_id=None):
    project, kind, back, audit_kind = _work(request, project_id, batch_id)
    doc = ProjectOfyorta.objects.filter(**{kind: project}).first()
    if doc is None or doc.status != ProjectOfyorta.Status.READY or not doc.file:
        raise Http404(_("Офёрта ҳали тузилмаган"))
    path = project.abs(doc.file)
    if not path.is_file():
        raise Http404(_("Офёрта файли топилмади — қайта тузинг"))
    stop = gate.check(request, gate.items_for(project), back)  # пулли режим: ичидаги сметалар пули
    if stop:
        return stop
    name = f"Офёрта — {doc.name}.xlsx".replace("/", "-").replace("\\", "-")  # i18n: ok
    _audit_download(request, project, audit_kind, name)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=brandfile.name(name))

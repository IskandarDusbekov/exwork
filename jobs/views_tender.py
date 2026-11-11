"""Объект таҳлили саҳифаси: битта смета, объект ёки бутун папка (``jobs.tender``). Панелда ўчирилган бўлса —
фақат администраторга (синов учун) кўринади."""
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from . import tender
from .models import Batch, Job, Project
from .ofyorta import priced_jobs
from .views import _get_work, crumbs_for


def _allowed(request):
    on = tender.enabled()
    if not on and not request.user.is_staff:
        raise Http404
    return on


def _page(request, jobs, title, crumbs, back):
    on = _allowed(request)
    report = tender.analyse(jobs)
    back = reverse(back[0], args=[back[1]])
    if crumbs and not crumbs[-1]["url"]:
        crumbs[-1]["url"] = back
    return render(request, "jobs/tender.html", {
        "crumbs": crumbs + [{"label": "Объект таҳлили", "url": "", "icon": "chart", "color": "text-indigo-500"}],
        "title": title, "r": report, "enabled": on, "back": back,
    })


def tender_job(request, job_id):
    job = _get_work(request, Job, id=job_id)
    if job.status != Job.Status.DONE:
        raise Http404
    return _page(request, [job], job.batch_label or job.local_name, crumbs_for(job=job), ("job", job.id))


def tender_batch(request, batch_id):
    batch = _get_work(request, Batch, id=batch_id)
    jobs = list(batch.jobs.filter(status=Job.Status.DONE).order_by("batch_order", "created"))
    return _page(request, jobs, batch.name, crumbs_for(batch=batch), ("batch", batch.id))


def tender_project(request, project_id):
    project = _get_work(request, Project, id=project_id)
    return _page(request, priced_jobs(project), project.name, crumbs_for(project=project), ("project", project.id))

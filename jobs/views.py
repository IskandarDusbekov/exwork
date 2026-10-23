from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from core.svodka import SVODKA_ROWS

from .models import Job
from .services import UploadError, create_job, run_job

STAT_CARDS = [
    ("EXACT", "Аниқ топилди", "emerald"),
    ("FUZZY", "Ўхшаш (fuzzy)", "amber"),
    ("AMBIGUOUS", "ШАБҲАЛИ", "orange"),
    ("NOT_FOUND", "ТОПИЛМАДИ", "rose"),
    ("ZERO_RULE", "Нол қоидаси", "slate"),
    ("NON_NUMERIC", "G да рақам эмас", "rose"),
]


@require_http_methods(["GET", "POST"])
def index(request):
    error = None
    if request.method == "POST":
        local = request.FILES.get("local")
        res = request.FILES.get("res")
        if not local:
            error = "Локал файлни танланг."
        else:
            try:
                job = create_job(local, res)
                return redirect("job", job_id=job.id)
            except UploadError as e:
                error = str(e)
    return render(request, "jobs/index.html", {"error": error, "recent": Job.objects.all()[:8]})


def job_detail(request, job_id):
    job = get_object_or_404(Job, id=job_id)
    ctx = {"job": job, "fuzzy_range": range(80, 101)}
    if job.status == Job.Status.DONE:
        res = job.result
        totals = res["totals"]
        ctx.update(
            stats=[(label, res["stats"].get(key, 0), color) for key, label, color in STAT_CARDS],
            svodka=[
                {"row": res["svodka_start"] + i, "text": text, "pct": pct, "value": totals.get(key)}
                for i, (key, text, pct) in enumerate(SVODKA_ROWS)
            ],
            s_nds=totals.get("s_nds"),
            has_errors=any(isinstance(v, str) for v in totals.values()),
            rows=res["rows"],
            problems=res["problems"],
        )
    return render(request, "jobs/job.html", ctx)


@require_POST
def job_run(request, job_id):
    job = get_object_or_404(Job, id=job_id)
    run_job(job, request.POST.get("local_sheet"), request.POST.get("res_sheet"),
            request.POST.get("fuzzy") or job.fuzzy)
    return redirect("job", job_id=job.id)


def job_download(request, job_id):
    job = get_object_or_404(Job, id=job_id)
    if job.status != Job.Status.DONE or not job.output_path:
        raise Http404("Натижа ҳали тайёр эмас")
    path = job.abs(job.output_path)
    if not path.is_file():
        raise Http404("Файл топилмади")
    return FileResponse(open(path, "rb"), as_attachment=True, filename=job.download_name)

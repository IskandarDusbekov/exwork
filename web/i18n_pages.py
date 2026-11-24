"""Таржимани текшириш учун саҳифалар рўйхати: ҳар бир турдаги саҳифадан биттадан (базадаги маълумот билан).
``manage.py i18n pages --lang ru`` ва тестлар ишлатади."""
from django.urls import reverse


def page_urls(user) -> list[str]:
    """Фойдаланувчи кўрадиган саҳифалар (панелсиз). Базада бор ишлардан биттадан олинади."""
    from jobs.models import Batch, Job, Project
    from texnazor.models import SiteObject

    urls = [reverse(n) for n in ("dashboard", "calc", "help", "guide", "user_settings", "landing",
                                 "about_us", "terms", "privacy", "blog", "texnazor:index", "texnazor:create")]
    pr = Project.objects.filter(owner=user).order_by("-created").first()
    if pr:
        urls += [reverse("project", args=[pr.id]), reverse("project_ofyorta", args=[pr.id]),
                 reverse("project_tender", args=[pr.id])]
    b = Batch.objects.filter(owner=user, project__isnull=True).order_by("-created").first()
    if b:
        urls.append(reverse("batch", args=[b.id]))
    j = Job.objects.filter(owner=user, status=Job.Status.DONE).order_by("-created").first()
    if j:
        urls += [reverse("job", args=[j.id]), reverse("job_tender", args=[j.id])]
    o = SiteObject.objects.filter(owner=user).order_by("-created").first()
    if o:
        urls += [reverse("texnazor:object", args=[o.pk]), reverse("texnazor:mark", args=[o.pk])]
    return urls


PUBLIC = ["login", "register", "reset_request", "landing", "about_us", "terms", "privacy", "blog"]

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _l


class Announcement(models.Model):
    """Админ ёзадиган янгилик / эълон / табрик. Чоп этилгач фойдаланувчи кирганда бир марта ойнада (модал)
    чиқади, кейин сарлавҳадаги қўнғироқчада (билдиришномалар) туради."""

    class Style(models.TextChoices):
        NEWS = "news", _l("Янгилик")          # кўк — янги имконият, ўзгариш
        SERIOUS = "serious", _l("Жиддий")     # тўқ — муҳим огоҳлантириш, техник иш, қоида
        CELEBRATE = "celebrate", _l("Табрик")  # байрамона — байрам, табрик, ютуқ

    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)  # оддий матн; бўш қатор — янги хатбоши, «- » билан бошланса — рўйхат
    style = models.CharField(max_length=10, choices=Style.choices, default=Style.NEWS)
    link_url = models.CharField(max_length=300, blank=True)  # ихтиёрий тугма: «Батафсил» каби
    link_text = models.CharField(max_length=60, blank=True)
    published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    show_modal = models.BooleanField(default=True)  # кирганда ойнада чиқсинми (йўқ — фақат қўнғироқчада)
    ends_at = models.DateTimeField(null=True, blank=True)  # шу пайтдан кейин кўрсатилмайди (ихтиёрий)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")

    class Meta:
        ordering = ["-published_at", "-id"]

    def __str__(self):
        return self.title

    def publish(self):
        self.published = True
        if not self.published_at:
            self.published_at = timezone.now()

    @property
    def blocks(self):
        """Матн бўлаклари: [("p", "…") | ("ul", ["…", "…"])] — шаблонда хавфсиз чизиш учун (HTML йўқ)."""
        out = []
        for chunk in (self.body or "").replace("\r\n", "\n").split("\n\n"):
            lines = [x.strip() for x in chunk.split("\n") if x.strip()]
            if not lines:
                continue
            if all(x.startswith(("- ", "• ", "* ")) for x in lines):
                out.append(("ul", [x[2:].strip() for x in lines]))
            else:
                out.append(("p", "\n".join(lines)))
        return out


class AnnouncementRead(models.Model):
    """Фойдаланувчи эълонни кўрди (ойна ёпилди ёки қўнғироқчада очилди) — ойнада қайта чиқмайди."""

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["announcement", "user"], name="news_read_once")]

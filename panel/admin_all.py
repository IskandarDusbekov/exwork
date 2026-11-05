"""Django admin («Маълумотлар базаси»): ўз admin.py си йўқ барча жадваллар автоматик рўйхатга олинади.

``PanelConfig.ready()`` дан чақирилади — admin autodiscover'дан кейин, шунинг учун қўлда ёзилган admin
(масалан accounts.User) устун туради. Катта жадваллар (RawRow ~150 минг) учун: саҳифада 50 та, тўлиқ санаш
йўқ, ForeignKey — рақам майдони (минглаб қаторли рўйхат очилмайди)."""
from django.apps import apps
from django.contrib import admin
from django.db import models

APPS = ("accounts", "jobs", "panel", "resbase", "texnazor")
# журнал ва ўлчовлар — фақат кўриш (ўзгартириб ёки қўшиб бўлмайди)
VIEW_ONLY = {"panel.Event", "panel.AccessStat", "panel.ServerLog", "panel.LoginSession",
             "jobs.FileTiming", "jobs.UploadTiming", "accounts.TwoFactor"}
NO_DELETE = {"panel.Event"}  # хавфсизлик журнали ўчирилмайди
HIDDEN = {"accounts.TwoFactor": ("secret",)}  # иккинчи босқич калити ҳеч қаерда кўрсатилмайди
LONG = (models.TextField, models.JSONField, models.BinaryField)


class AutoAdmin(admin.ModelAdmin):
    list_per_page = 50
    show_full_result_count = False


def _no(self, request, obj=None):
    return False


def admin_for(model):
    label = model._meta.label
    hidden = HIDDEN.get(label, ())
    fields = [f for f in model._meta.concrete_fields if f.name not in hidden]
    fks = [f.name for f in fields if isinstance(f, (models.ForeignKey, models.OneToOneField))]
    attrs = {
        "list_display": [f.name for f in fields if not isinstance(f, LONG)][:7] or ["__str__"],
        "search_fields": [f.name for f in fields if isinstance(f, models.CharField) and not f.choices][:4],
        "list_filter": [f.name for f in fields if f.choices or isinstance(f, models.BooleanField)][:4],
        "raw_id_fields": fks,
        "list_select_related": fks,
        "exclude": hidden,
    }
    if label in VIEW_ONLY:
        attrs.update(has_add_permission=_no, has_change_permission=_no)
    if label in NO_DELETE:
        attrs["has_delete_permission"] = _no
    return type(f"{model.__name__}AutoAdmin", (AutoAdmin,), attrs)


def register_all(site=admin.site):
    for model in apps.get_models():
        if model._meta.app_label in APPS and not site.is_registered(model):
            site.register(model, admin_for(model))

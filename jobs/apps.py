# i18n: ok-file — admin / тизим текшируви
from django.apps import AppConfig


class JobsConfig(AppConfig):
    name = "jobs"
    verbose_name = "Нархлаш"

    def ready(self):
        from . import checks  # noqa: F401 — production ogohlantirishlari (check --deploy)
        from core import convert

        from . import lo_queue
        convert.set_limiter(lo_queue.slot)  # LibreOffice: сервер бўйича бир вақтда кўпи билан 2 та

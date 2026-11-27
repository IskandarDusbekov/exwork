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
        from django.conf import settings
        convert.set_cache(getattr(settings, "NARXLASH_CONVERT_CACHE", "") or None,  # такрор .xls — кешдан
                          getattr(settings, "NARXLASH_CONVERT_CACHE_MB", 2048) * 1024 * 1024)
        try:  # доимий LibreOffice (deploy/office): бўш — ўчиқ, ҳар файлга алоҳида soffice
            convert.set_office(getattr(settings, "NARXLASH_OFFICE", ""))
        except ValueError as e:
            import logging
            logging.getLogger(__name__).error("%s — доимий LibreOffice ўчиқ қолди", e)
            convert.set_office("")

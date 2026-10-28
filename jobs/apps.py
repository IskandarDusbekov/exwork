from django.apps import AppConfig


class JobsConfig(AppConfig):
    name = "jobs"
    verbose_name = "Нархлаш"

    def ready(self):
        from . import checks  # noqa: F401 — production ogohlantirishlari (check --deploy)

"""Ofyortani fonda o'qish (18 000+ qator — sayt so'rovi kutib qolmasin). ``jobs.recalc`` bilan bir xil tartib:
worker rejimida ``run_worker`` navbatdan oladi (lease), thread rejimida — sayt jarayonidagi oqim."""
import datetime as dt
import logging
import os
import threading

from django.conf import settings
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from .models import SiteObject
from .services import import_ofyorta

log = logging.getLogger(__name__)
STALE_AFTER = dt.timedelta(minutes=5)


def _free():
    return Q(worker="") | Q(heartbeat__isnull=True) | Q(heartbeat__lt=timezone.now() - STALE_AFTER)


def _claim(obj_id, worker: str) -> bool:
    return bool(SiteObject.objects.filter(pk=obj_id, status=SiteObject.Status.PARSING).filter(_free())
                .update(worker=worker, heartbeat=timezone.now()))


def claim_next(worker: str):
    for obj_id in (SiteObject.objects.filter(status=SiteObject.Status.PARSING).filter(_free())
                   .order_by("created").values_list("id", flat=True)[:10]):
        if _claim(obj_id, worker):
            return obj_id
    return None


def run(obj_id, worker: str):
    close_old_connections()
    try:
        obj = SiteObject.objects.get(pk=obj_id)
    except SiteObject.DoesNotExist:
        return
    beat = lambda: SiteObject.objects.filter(pk=obj_id, worker=worker).update(heartbeat=timezone.now())  # noqa: E731
    try:
        import_ofyorta(obj, progress=beat)
    except Exception:  # noqa: BLE001
        log.exception("офёртани ўқиш: %s", obj_id)
        SiteObject.objects.filter(pk=obj_id).update(status=SiteObject.Status.FAILED, error="кутилмаган хато")
    finally:
        SiteObject.objects.filter(pk=obj_id, worker=worker).update(worker="", heartbeat=None)


def worker_mode() -> bool:
    return getattr(settings, "NARXLASH_TASKS", "thread") == "worker"


def start(obj_id):
    if worker_mode():
        return
    threading.Thread(target=run_now, args=(obj_id,), daemon=True, name=f"ofyorta-{obj_id}").start()


def run_now(obj_id):
    me = f"thread:{os.getpid()}"
    try:
        if _claim(obj_id, me):
            run(obj_id, me)
    finally:
        close_old_connections()

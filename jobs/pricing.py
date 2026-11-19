"""Bitta obyektni narxlash («Бошлаш») — fonda. Sayt so'rovi faqat tanlovni saqlab, darhol javob qaytaradi;
narxlashni ``run_worker`` navbatdan oladi (lease: ``worker`` + ``heartbeat``), thread rejimida (DEBUG) — sayt
jarayonidagi oqim. Sahifa ``batch_progress`` ni so'rab turadi va tugagach natijaga o'tadi.

Tartib ``texnazor.tasks`` va ``jobs.recalc`` bilan bir xil. Papka qismlari bu yerda emas — ``queue_part``."""
import datetime as dt
import logging
import os
import threading

from django.conf import settings
from django.db import close_old_connections
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import Batch

log = logging.getLogger(__name__)
STALE_AFTER = dt.timedelta(minutes=5)  # ishchi shuncha jim bo'lsa (o'chib qolgan) — boshqasi qaytadan oladi


def _free():
    return Q(worker="") | Q(heartbeat__isnull=True) | Q(heartbeat__lt=timezone.now() - STALE_AFTER)


def _queue():
    return Batch.objects.filter(pricing__isnull=False, project__isnull=True)


def queue(batch: Batch, decisions: dict) -> bool:
    """Tanlovni navbatga qo'yadi. Allaqachon narxlanayotgan bo'lsa — qayta qo'yilmaydi (``False``)."""
    if _queue().filter(pk=batch.pk).exists():
        return False
    now = timezone.now()
    Batch.objects.filter(pk=batch.pk).update(
        pricing={str(n): d for n, d in decisions.items()}, worker="", heartbeat=None, price_error="",
        step="Навбатда…", step_since=now)
    return True


def ahead(batch: Batch) -> int:
    """Navbatda bu obyektdan oldin nechta kutmoqda (hali hech kim olmagan)."""
    if not batch.step_since:
        return 0
    return _queue().filter(worker="", step_since__lt=batch.step_since).count()


def result_url(batch: Batch) -> str:
    """Tugagan narxlash natijasi: bitta smeta — uning sahifasi, bir nechta — obyekt sahifasi."""
    ids = list(batch.jobs.order_by("batch_order").values_list("id", flat=True)[:2])
    return reverse("job", args=[ids[0]]) if len(ids) == 1 else reverse("batch", args=[batch.id])


def _claim(batch_id, worker: str) -> bool:
    return bool(_queue().filter(pk=batch_id).filter(_free()).update(worker=worker, heartbeat=timezone.now()))


def claim_next(worker: str):
    for batch_id in _queue().filter(_free()).order_by("step_since").values_list("id", flat=True)[:10]:
        if _claim(batch_id, worker):
            return batch_id
    return None


def run(batch_id, worker: str):
    from .services import confirm_batch
    close_old_connections()
    batch = _queue().filter(pk=batch_id, worker=worker).first()
    if batch is None:
        return
    error = ""
    try:
        confirm_batch(batch, {int(k): v for k, v in batch.pricing.items()})
    except Exception as e:  # noqa: BLE001 — sabab sahifada ko'rsatiladi, smetachi qayta urinadi
        log.exception("нархлаш: %s", batch_id)
        error = f"Нархлашда хато: {e}"[:500]
    finally:
        done = {"pricing": None, "worker": "", "heartbeat": None, "price_error": error}
        if error:
            done["step"] = ""
        Batch.objects.filter(pk=batch_id, worker=worker).update(**done)


def worker_mode() -> bool:
    return getattr(settings, "NARXLASH_TASKS", "thread") == "worker"


def start(batch_id):
    if worker_mode():
        return
    threading.Thread(target=run_now, args=(batch_id,), daemon=True, name=f"pricing-{batch_id}").start()


def run_now(batch_id):
    me = f"thread:{os.getpid()}:{threading.get_ident()}"
    try:
        if _claim(batch_id, me):
            run(batch_id, me)
    finally:
        close_old_connections()

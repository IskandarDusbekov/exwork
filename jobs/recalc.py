"""Smetani qayta hisoblash navbati: smetachi o'zgartirgan sozlama (L/M ustun, narx, foiz, …) darhol saqlanadi,
butun smetani qayta narxlash esa fonda — sayt so'rovi kutib qolmaydi (katta smeta bir necha daqiqa oladi).

* ``request`` — sozlama saqlanadi, ``recalc_seq`` oshadi (navbatga qo'yiladi). Hisob tugaguncha yana o'zgarish
  bo'lsa — yig'iladi: oxirida bitta hisob hammasini oladi (5 ta parallel hisob emas).
* Worker (``run_worker``) ``claim_next`` bilan egallaydi (lease, ``folders`` dagi loyihalar kabi), ``run`` bilan
  hisoblaydi. Hisob davomida yangi o'zgarish kelsa (``recalc_seq`` oshgan) — smeta navbatda qoladi va yana olinadi.
* ``thread`` rejimida (ishlab chiqish) — sayt jarayonidagi oqim.
"""
import datetime as dt
import logging
import os
import threading

from django.conf import settings
from django.db import close_old_connections
from django.db.models import F, Q
from django.utils import timezone

from .models import Job
from .services import RESULT_FIELDS, SETTING_FIELDS, apply_settings, recompute

log = logging.getLogger(__name__)

STALE_AFTER = dt.timedelta(minutes=5)  # worker shuncha vaqt belgi bermasa — boshqasi oladi
KEEPALIVE_SECONDS = 30


def request(job: Job, local_sheet=None, res_sheet=None, fuzzy=None, mode=None, percents=None,
            categories=None, prices=None, cleared=None, marked=None) -> Job:
    """Sozlamani saqlab, qayta hisoblashni navbatga qo'yadi. Hisob natijasi — keyinroq (``job.recalc_pending``)."""
    apply_settings(job, local_sheet, res_sheet, job.fuzzy if fuzzy is None else fuzzy, mode, percents,
                   categories, prices, cleared, marked)
    job.save(update_fields=SETTING_FIELDS)
    Job.objects.filter(pk=job.pk).update(recalc_seq=F("recalc_seq") + 1, recalc_requested=timezone.now())
    job.refresh_from_db(fields=["recalc_seq", "recalc_done", "recalc_requested"])
    start(job.pk)
    return job


def _pending():
    return Q(recalc_seq__gt=F("recalc_done"))


def _free():
    return Q(recalc_worker="") | Q(recalc_heartbeat__isnull=True) | Q(recalc_heartbeat__lt=timezone.now() - STALE_AFTER)


def _claim(job_id, worker: str) -> bool:
    return bool(Job.objects.filter(pk=job_id).filter(_pending()).filter(_free()).update(
        recalc_worker=worker, recalc_heartbeat=timezone.now()))


def claim_next(worker: str):
    """Navbatdagi smetani egallaydi (atomar UPDATE). Smeta id'si yoki None. Eng avval so'ralgani birinchi."""
    for job_id in (Job.objects.filter(_pending()).filter(_free()).order_by("recalc_requested")
                   .values_list("id", flat=True)[:20]):
        if _claim(job_id, worker):
            return job_id
    return None


def run(job_id, worker: str):
    """Egallangan smetani hisoblaydi. Faqat natija maydonlari yoziladi — hisob davomida smetachi o'zgartirgan
    sozlama ustidan yozilmaydi; o'sha o'zgarish uchun smeta navbatda qoladi."""
    close_old_connections()
    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return
    seq = job.recalc_seq
    stop = threading.Event()

    def keepalive():
        while not stop.wait(KEEPALIVE_SECONDS):
            try:
                Job.objects.filter(pk=job_id, recalc_worker=worker).update(recalc_heartbeat=timezone.now())
            except Exception:  # noqa: BLE001 — belgi qo'yilmasa, keyingisida
                pass
            finally:
                close_old_connections()

    threading.Thread(target=keepalive, daemon=True, name=f"recalc-keepalive-{job_id}").start()
    try:
        recompute(job)
    except Exception:  # noqa: BLE001 — recompute o'zi xatoni job.error ga yozadi; bu — kutilmagan holat
        log.exception("qayta hisoblash: %s", job_id)
        job.status, job.error = Job.Status.FAILED, "қайта ҳисоблашда кутилмаган хато"
    finally:
        stop.set()
    mine = Job.objects.filter(pk=job_id, recalc_worker=worker)
    if not mine.exists():
        return  # lease boshqasiga o'tgan (uzoq javob bermadik) — natijani o'sha yozadi
    job.save(update_fields=RESULT_FIELDS)
    mine.update(recalc_done=seq, recalc_worker="", recalc_heartbeat=None)
    if not Job.objects.filter(pk=job_id).filter(_pending()).exists():
        # smetachi sahifada kutyapti: sahifa yangilanishi bilan jadval ochiladi — ko'rinish hozir tayyorlanadi
        from .preview import warm
        warm(job)


def worker_mode() -> bool:
    return getattr(settings, "NARXLASH_TASKS", "thread") == "worker"


_running = set()
_lock = threading.Lock()


def start(job_id):
    """Worker rejimida hech narsa qilmaydi (bo'sh worker o'zi oladi); thread rejimida — sayt jarayonida oqim."""
    if worker_mode():
        return
    with _lock:
        if job_id in _running:
            return  # shu smeta allaqachon hisoblanyapti — u tugagach yangi o'zgarishni ham oladi
        _running.add(job_id)
    threading.Thread(target=_thread_run, args=(job_id,), daemon=True, name=f"recalc-{job_id}").start()


def run_now(job_id):
    """Navbatdagi hisobni shu jarayonda bajaradi (thread rejimi va testlar)."""
    me = f"thread:{os.getpid()}"
    while _claim(job_id, me):
        run(job_id, me)


def _thread_run(job_id):
    try:
        run_now(job_id)
    finally:
        with _lock:
            _running.discard(job_id)
        close_old_connections()

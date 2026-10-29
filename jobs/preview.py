"""Smeta sahifasidagi Excel ko'rinishi (natija / lokal / RES): ma'lumot, kesh va oldindan tayyorlash.

Katta smetada (16 000 qator) ko'rinishni birinchi marta yasash ~10 soniya — Excel faylni ochishning o'zi.
Shuning uchun worker uni narxlash va qayta hisobdan keyin **fonda** tayyorlab qo'yadi (``warm``): smetachi
bosganda kesh tayyor, jadval darhol ochiladi. Sahifa avval boshini (``head``), keyin butun jadvalni oladi.
"""
import datetime as dt
import logging

from django.db import close_old_connections
from django.utils import timezone

from . import xlview
from .models import Job

log = logging.getLogger(__name__)

HEAD_ROWS = 400  # birinchi bo'lak — darhol ko'rinadi, qolgani keyin
WARM_DAYS = 30   # worker bo'sh paytda shu muddatdagi smetalarni tayyorlaydi (eskilari — so'ralganda)


class NotAvailable(Exception):
    """Fayl yo'q yoki natija hali tayyor emas (sahifada 404)."""


def sheet_data(job: Job, kind: str, sheet=None) -> dict:
    """``kind``: out | local | res. Kesh fayl o'zgarmaguncha saqlanadi (xlview.cached)."""
    res = job.result or {}
    red, yellow, fallback, default = frozenset(), frozenset(), None, None
    if kind == "out":
        if job.status != Job.Status.DONE or not job.output_path:
            raise NotAvailable("Натижа ҳали тайёр эмас")
        path, name = job.abs(job.output_path), job.download_name
        # G даги нархланмаганлар Excel'да шартли формат билан қизил — шу ерда ҳам
        red = frozenset(f"G{r['row']}" for r in res.get("rows", [])
                        if (isinstance(r.get("g"), str) and r["g"])
                        or (r.get("cleared") and r.get("status") == "NOT_FOUND" and r.get("g") is None))
        yellow = frozenset(f"G{r['row']}" for r in res.get("rows", []) if r.get("marked") and r.get("g") is None)
        fallback = job.abs(job.local_path)  # openpyxl сақлаганда асл формулалар натижаси йўқолади
    elif kind == "local":
        path, name, default = job.abs(job.local_path), job.local_name, job.local_sheet
    elif kind == "res" and not job.res_path:
        raise NotAvailable("Бу смета RES'сиз — ресурс базасидан нархланган")
    elif kind == "res":
        path, name, default = job.abs(job.res_path), job.res_name, job.res_sheet
    else:
        raise NotAvailable("Нотўғри файл")
    if not path.is_file():
        raise NotAvailable("Файл топилмади")
    key = f"{kind}|{sheet}|{default}|{','.join(sorted(red))}|{','.join(sorted(yellow))}"
    data = xlview.cached(job.dir / "preview", key, path, lambda: xlview.render(
        path, sheet, default, fallback=fallback, red=red, yellow=yellow))
    return {**data, "file": name}


def head(data: dict, n: int = HEAD_ROWS) -> dict:
    """Jadvalning birinchi ``n`` qatori (darhol ko'rsatish uchun); qolgani bo'lsa ``partial``."""
    if data.get("rows", 0) <= n:
        return data
    html = data["html"]
    start = html.find("<tbody>")
    pos = start
    for _ in range(n):
        pos = html.find("</tr>", pos + 1)
        if pos < 0:
            return data
    return {**data, "html": html[:pos + 5] + "</tbody></table>", "partial": True, "shown": n}


def warm(job: Job):
    """Natija ko'rinishini oldindan tayyorlaydi (keshga). Xato bo'lsa — jim: sahifa o'zi yasaydi."""
    try:
        sheet_data(job, "out")
    except NotAvailable:
        pass
    except Exception:  # noqa: BLE001 — ko'rinish buzilsa ham worker ishlayversin
        log.exception("кўринишни тайёрлаб бўлмади: %s", job.pk)
    Job.objects.filter(pk=job.pk).update(preview_ready=True)


def warm_next() -> bool:
    """Worker bo'sh paytda: tayyorlanmagan eng yangi smetani oladi (atomar) va tayyorlaydi. Ish bo'ldimi."""
    since = timezone.now() - dt.timedelta(days=WARM_DAYS)
    for job_id in (Job.objects.filter(status=Job.Status.DONE, preview_ready=False, created__gte=since)
                   .order_by("-created").values_list("id", flat=True)[:5]):
        if Job.objects.filter(pk=job_id, preview_ready=False).update(preview_ready=True):
            close_old_connections()
            warm(Job.objects.get(pk=job_id))
            return True
    return False

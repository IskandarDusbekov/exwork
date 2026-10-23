"""Yuklash -> konvertatsiya -> narxlash. Django view'lari faqat shu funksiyalarni chaqiradi."""
import datetime as dt
import shutil
from pathlib import Path
from typing import Optional

from django.conf import settings
from openpyxl import load_workbook

from core import convert
from core.detect import pair_sheets
from core.pipeline import Options, run

from .models import Job

ALLOWED_EXT = (".xls", ".xlsx", ".xlsm")


class UploadError(Exception):
    pass


def _check_upload(f, label: str) -> str:
    ext = Path(f.name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise UploadError(f"{label}: фақат .xls, .xlsx, .xlsm файллар қабул қилинади")
    if f.size > settings.NARXLASH_MAX_UPLOAD:
        raise UploadError(f"{label}: файл жуда катта ({f.size // (1024 * 1024)} МБ)")
    return ext


def _save(f, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as out:
        for chunk in f.chunks():
            out.write(chunk)


def sheet_names(path: Path):
    wb = load_workbook(path, read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def create_job(local_file, res_file=None) -> Job:
    """Fayllarni saqlaydi, .xlsx ga o'giradi, listlarni o'qib standart juftlikni tanlaydi."""
    local_ext = _check_upload(local_file, "Локал файл")
    res_ext = _check_upload(res_file, "Ресурс файл") if res_file else local_ext

    job = Job(local_name=Path(local_file.name).name, res_name=Path((res_file or local_file).name).name)
    try:
        _save(local_file, job.abs(f"upload/local{local_ext}"))
        local_xlsx = convert.ensure_xlsx(job.abs(f"upload/local{local_ext}"), job.abs("conv"))
        if res_file:
            _save(res_file, job.abs(f"upload/res{res_ext}"))
            res_xlsx = convert.ensure_xlsx(job.abs(f"upload/res{res_ext}"), job.abs("conv"))
        else:
            res_xlsx = local_xlsx
        job.local_sheets = sheet_names(local_xlsx)
        job.res_sheets = sheet_names(res_xlsx)
    except convert.ConversionError as e:
        shutil.rmtree(job.dir, ignore_errors=True)
        raise UploadError(str(e)) from e
    except Exception as e:
        shutil.rmtree(job.dir, ignore_errors=True)
        raise UploadError(f"Файлни очиб бўлмади: {e}") from e

    job.local_path = str(local_xlsx.relative_to(job.dir))
    job.res_path = str(res_xlsx.relative_to(job.dir))
    job.local_sheet, job.res_sheet = pair_sheets(job.local_sheets, job.res_sheets)
    job.save()
    return job


def _jsonable(v):
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return str(v)  # XlError ham oddiy "#VALUE!" satrga aylanadi
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    return str(v)


def run_job(job: Job, local_sheet: Optional[str], res_sheet: Optional[str], fuzzy: int) -> Job:
    job.local_sheet = local_sheet or job.local_sheet
    job.res_sheet = res_sheet or job.res_sheet
    job.fuzzy = max(80, min(100, int(fuzzy)))
    out = job.abs("out") / job.download_name
    try:
        result = run(job.abs(job.local_path), job.abs(job.res_path), Options(
            fuzzy_percent=job.fuzzy, local_sheet=job.local_sheet, res_sheet=job.res_sheet, output_path=out,
        ))
    except Exception as e:  # foydalanuvchiga ko'rsatamiz, server yiqilmasin
        job.status = Job.Status.FAILED
        job.error = f"{type(e).__name__}: {e}"
        job.save()
        return job

    job.status = Job.Status.DONE
    job.error = ""
    job.output_path = str(out.relative_to(job.dir))
    job.result = {
        "stats": result.stats,
        "totals": {k: _jsonable(v) for k, v in result.totals.items()},
        "problems": [{k: _jsonable(v) for k, v in p.items()} for p in result.problems],
        "rows": [{k: _jsonable(v) for k, v in r.items()} for r in result.rows],
        "start_row": result.start_row,
        "end_row": result.end_row,
        "sum_row": result.sum_row,
        "svodka_start": result.svodka_start,
    }
    job.save()
    return job

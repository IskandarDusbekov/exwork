"""Yuklash -> konvertatsiya -> narxlash. Django view'lari faqat shu funksiyalarni chaqiradi."""
import datetime as dt
import shutil
from pathlib import Path
from typing import Optional

from django.conf import settings
from openpyxl import load_workbook

from core import convert
from core.batch import apply_roles, inspect_workbook, mark_duplicates, plan_pairs
from core.detect import pair_sheets
from core.pipeline import MODES, Options, run
from core.svodka import DEFAULT_PERCENTS, parse_text_number

from .models import Batch, Job

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


def parse_percents(data, previous: Optional[dict] = None) -> dict:
    """Formadan ``pct_<kalit>`` maydonlari: «17,27» -> 0.1727. Bo'sh — standart;
    noto'g'ri yozilgan («abc», 150) — avvalgi qiymati qoladi."""
    previous = previous or {}
    out = {}
    for key in DEFAULT_PERCENTS:
        raw = (data.get(f"pct_{key}") or "").strip()
        if not raw:
            continue
        value = parse_text_number(raw.rstrip("%").strip())
        if value is not None and 0 <= value <= 100:
            out[key] = round(value / 100, 8)
        elif key in previous:
            out[key] = previous[key]
    return out


def run_job(job: Job, local_sheet: Optional[str], res_sheet: Optional[str], fuzzy: int,
            mode: Optional[str] = None, percents: Optional[dict] = None,
            categories: Optional[dict] = None, prices: Optional[dict] = None) -> Job:
    """``percents``/``categories``/``prices``: None — avvalgilari qoladi, {} — tozalanadi.
    Lokal list almashsa, qo'lda kategoriya va narxlar tozalanadi (qator raqamlari boshqa listga tegishli)."""
    if local_sheet and local_sheet != job.local_sheet:
        job.categories = {}
        job.prices = {}
    if categories is not None:
        job.categories = categories
    if prices is not None:
        job.prices = prices
    job.local_sheet = local_sheet or job.local_sheet
    job.res_sheet = res_sheet or job.res_sheet
    job.fuzzy = max(80, min(100, int(fuzzy)))
    job.mode = mode if mode in MODES else job.mode
    if percents is not None:
        job.percents = percents
    out = job.abs("out") / job.download_name
    try:
        result = run(job.abs(job.local_path), job.abs(job.res_path), Options(
            fuzzy_percent=job.fuzzy, mode=job.mode, local_sheet=job.local_sheet, res_sheet=job.res_sheet,
            output_path=out, percents=job.percents or None,
            categories={int(r): c for r, c in (job.categories or {}).items()} or None,
            prices={int(r): float(p) for r, p in (job.prices or {}).items()} or None,
            column_names=job.column_names or None,
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
        "mode": result.mode,
        "warnings": result.warnings,
        "columns": result.columns,
        "percents": result.percents,
        "categories": {str(r): c for r, c in result.categories.items()},
        "prices": {str(r): p for r, p in result.prices.items()},
        "res_sections": result.res_sections,
        "extra_columns": [list(c) for c in result.extra_columns],
        "tail_removed": result.tail_removed,
    }
    job.save()
    return job


# ===================== obyekt: bir yoki bir nechta fayl =====================
MAX_FILES = 10


def create_batch(files) -> "Batch":
    """Fayllarni saqlaydi, .xlsx ga o'giradi, har bir listni tahlil qilib juftliklarni taklif qiladi."""
    if not files:
        raise UploadError("Камида битта файл танланг")
    if len(files) > MAX_FILES:
        raise UploadError(f"Бир марта кўпи билан {MAX_FILES} та файл юкланг")
    names = [Path(f.name).name for f in files]
    batch = Batch(name=names[0] if len(names) == 1 else f"{names[0]} + {len(names) - 1} та файл")
    try:
        for i, f in enumerate(files):
            ext = _check_upload(f, f.name)
            _save(f, batch.abs(f"upload/{i}{ext}"))
            xlsx = convert.ensure_xlsx(batch.abs(f"upload/{i}{ext}"), batch.abs(f"conv/{i}"))
            batch.files.append({"name": names[i], "path": str(xlsx.relative_to(batch.dir)), "sheets": sheet_names(xlsx)})
        analyze_batch(batch)
    except convert.ConversionError as e:
        shutil.rmtree(batch.dir, ignore_errors=True)
        raise UploadError(str(e)) from e
    except UploadError:
        shutil.rmtree(batch.dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(batch.dir, ignore_errors=True)
        raise UploadError(f"Файлни очиб бўлмади: {e}") from e
    batch.save()
    return batch


def analyze_batch(batch: "Batch", roles: Optional[dict] = None) -> "Batch":
    """Listlar turini aniqlaydi (``roles`` — smetachining qo'lda tuzatishlari), nusxalarni
    ajratadi va juftliklarni taklif qiladi."""
    infos = []
    for i, f in enumerate(batch.files):
        wb = load_workbook(batch.abs(f["path"]))
        try:
            infos += inspect_workbook(wb, i, f["name"])
        finally:
            wb.close()
    if roles is not None:
        batch.roles = roles
    apply_roles(infos, batch.roles or {})
    mark_duplicates(infos)
    pairs, _unused = plan_pairs(infos)
    batch.sheets = [i.to_dict() for i in infos]
    batch.pairs = [dict(p.to_dict(), include=p.res is not None) for p in pairs]
    return batch


def confirm_batch(batch: "Batch", decisions: dict) -> list:
    """``decisions``: ``{juftlik_raqami: {"include": bool, "res": kalit|"", "label": str}}``.
    Har bir tanlangan juftlik uchun Job yaratib, darhol narxlaydi. Avvalgi natijalar o'chiriladi."""
    resources = {s["key"] for s in batch.sheets if s["role"] == "resource" and not s.get("duplicate_of")}
    for job in batch.jobs.all():
        shutil.rmtree(job.dir, ignore_errors=True)
        job.delete()
    for n, p in enumerate(batch.pairs):
        d = decisions.get(n, {})
        p["include"] = bool(d.get("include", p.get("include")))
        if "res" in d:
            p["res"] = d["res"] if d["res"] in resources else None
        if d.get("label", "").strip():
            p["label"] = d["label"].strip()[:200]

    jobs = []
    for n, p in enumerate(batch.pairs):
        if not p["include"] or not p["res"]:
            continue
        loc, res = batch.sheet(p["local"]), batch.sheet(p["res"])
        lf, rf = batch.files[loc["file"]], batch.files[res["file"]]
        job = Job(batch=batch, batch_label=p["label"], batch_file=loc["file"], batch_order=n,
                  local_name=p["label"], res_name=f'{rf["name"]} · {res["sheet"]}',
                  local_sheets=lf["sheets"], res_sheets=rf["sheets"],
                  local_sheet=loc["sheet"], res_sheet=res["sheet"], mode="smart")
        local_dst = job.abs(f"conv/{loc['file']}.xlsx")
        res_dst = job.abs(f"conv/{res['file']}.xlsx")
        local_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(batch.abs(lf["path"]), local_dst)
        if res_dst != local_dst:
            shutil.copy2(batch.abs(rf["path"]), res_dst)
        job.local_path = str(local_dst.relative_to(job.dir))
        job.res_path = str(res_dst.relative_to(job.dir))
        job.save()
        run_job(job, None, None, job.fuzzy, job.mode)
        jobs.append(job)
    batch.status = Batch.Status.DONE
    batch.save()
    return jobs


def batch_outputs(batch: "Batch") -> list:
    """Har bir lokal fayl uchun natija: «<fayl>_narxlandi.xlsx». Faylda bitta narxlangan lokal bo'lsa —
    o'sha qismning tayyor fayli (qo'lda o'zgarishlari bilan), bir nechta bo'lsa — hammasi bitta faylda:
    har bir lokal list narxlanadi va o'z «LOG …» listini oladi."""
    from collections import defaultdict

    from core.pipeline import run_many

    groups = defaultdict(list)
    for job in batch.jobs.filter(status=Job.Status.DONE).order_by("batch_order"):
        groups[job.batch_file].append(job)
    if not groups:
        raise UploadError("Нархланган қисм йўқ")
    out_dir = batch.abs("out")
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    outputs = []
    for f, jobs in groups.items():
        out = out_dir / f"{Path(batch.files[f]['name']).stem}_narxlandi.xlsx"
        if len(jobs) == 1 and jobs[0].output_path and jobs[0].abs(jobs[0].output_path).is_file():
            shutil.copy2(jobs[0].abs(jobs[0].output_path), out)
        else:
            steps = [(job.abs(job.res_path), Options(
                mode=job.mode, fuzzy_percent=job.fuzzy, local_sheet=job.local_sheet, res_sheet=job.res_sheet,
                percents=job.percents or None,
                categories={int(r): c for r, c in (job.categories or {}).items()} or None,
                prices={int(r): float(v) for r, v in (job.prices or {}).items()} or None,
                column_names=job.column_names or None,
            )) for job in jobs]
            run_many(batch.abs(batch.files[f]["path"]), steps, out)
        outputs.append(out)
    return outputs


def build_combined(batch: "Batch"):
    """Obyektning hamma natijasi: bitta fayl bo'lsa — .xlsx, bir nechta bo'lsa — .zip."""
    import zipfile

    outputs = batch_outputs(batch)
    if len(outputs) == 1:
        return outputs[0]
    zpath = outputs[0].parent / f"{Path(batch.files[0]['name']).stem}_narxlandi.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for o in outputs:
            z.write(o, o.name)
    return zpath

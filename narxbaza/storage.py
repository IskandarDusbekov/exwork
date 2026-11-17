"""Нарх базасининг сервердаги файллари: нима жой эгаллайди ва нимасини ўчириш мумкин.

Юклашда сақланадиганлар (``media/narxbaza/up_<hex>/``):

* ``src/`` — асл юкланган файллар (бутун ZIP / RAR ҳам — ичида PDF, расм, Word …);
* ``unpack/`` — архивдан чиқарилган Excel'лар (.xls ҳам, нархсизлари ҳам);
* ``view/`` — .xls дан ўгирилган .xlsx (Excel кўриниши шу).

Ўқиб бўлингач фақат ``files[].path`` (Excel кўриниши) керак — қолгани ўчирилади (``prune_upload``).
Аввал юкланганлар учун — ``cleanup`` (панелда «Тозалаш»).
"""
from __future__ import annotations

import logging
import shutil
import time
from datetime import timedelta
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.utils import timezone

from .models import Batch, Intake

log = logging.getLogger(__name__)

UPLOAD_DIR = "narxbaza"
KEEP_BACKUPS = 3  # авто захира нусхалар (тиклаш / қўшишдан олдин) — энг янги шунчаси қолади
OLD_CANCELLED_DAYS = 30
OLD_FAILED_DAYS = 1


def _media() -> Path:
    return Path(settings.MEDIA_ROOT)


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return 0


def upload_root(it: Intake) -> Path | None:
    """Юклашнинг ``up_*`` папкаси (бўлмаса — ``None``)."""
    rel = (it.progress or {}).get("root") or (PurePosixPath(it.file_path).parent.parent.as_posix() if it.file_path else "")
    if not rel:
        return None
    base = (_media() / UPLOAD_DIR).resolve()
    d = (_media() / rel).resolve()
    if d.parent != base or not d.name.startswith("up_"):
        return None
    return d


def _keep(it: Intake) -> set:
    """Юклашдан сақланиши керак бўлган файллар (мутлақ йўллар): Excel кўринишлари ва тўпламлар кўрсатадиган файллар."""
    m = _media()
    return {(m / f["path"]).resolve() for f in it.files or [] if f.get("path")}


def _empty_dirs(d: Path):
    for sub in sorted((x for x in d.rglob("*") if x.is_dir()), key=lambda x: len(x.parts), reverse=True):
        try:
            sub.rmdir()
        except OSError:
            pass


def prune_upload(it: Intake, dry=False) -> int:
    """Ўқиб бўлинган юклашдан керакмас файлларни ўчиради: архивлар (ичидаги PDF ва бошқалар билан), ўгирилган
    .xls асллари, нархсиз файллар. Excel кўринишлари қолади. Бўшаган байтлар сонини қайтаради."""
    if it.status not in (Intake.Status.OPEN, Intake.Status.DONE):
        return 0
    d = upload_root(it)
    if d is None or not d.is_dir():
        return 0
    keep = _keep(it)
    if not keep or not all(p.is_file() for p in keep):
        return 0  # кўриниши ёзилмаган ёки йўқолган (асл файлдан қайта тузиш керак бўлади) — ҳеч нарса ўчирилмайди
    freed = 0
    gone = set()
    for f in d.rglob("*"):
        if f.is_file() and f.resolve() not in keep:
            freed += f.stat().st_size
            gone.add(f.resolve())
            if not dry:
                f.unlink(missing_ok=True)
    if dry or not gone:
        return freed
    _empty_dirs(d)
    m = _media()
    # асл файл ўчди — «src» да белги, тўпламлар файли — кўринишга (битта файл бўлса) ёки бўш
    pr = dict(it.progress or {})
    pr["src"] = [x for x in pr.get("src") or [] if (m / x[1]).resolve() not in gone]
    pr["pruned"] = True
    one = (it.files or [{}])[0].get("path", "") if len(it.files or []) == 1 else ""
    fields = ["progress"]
    if it.file_path and (m / it.file_path).resolve() in gone:
        it.file_path = one
        fields.append("file_path")
    it.progress = pr
    Intake.objects.filter(pk=it.pk).update(**{k: getattr(it, k) for k in fields})
    prefix = d.relative_to(m.resolve()).as_posix() + "/"
    for b in Batch.all_objects.filter(file_path__startswith=prefix):
        if (m / b.file_path).resolve() in gone:
            Batch.all_objects.filter(pk=b.pk).update(file_path=_batch_view(it, b))
    log.info("narxbaza: «%s» — керакмас файллар ўчирилди (%s)", it.title, human(freed))
    return freed


def _batch_view(it: Intake, b: Batch) -> str:
    """Тўплам битта файлдан бўлса — шу файлнинг Excel кўриниши."""
    paths = [f.get("path") for f in it.files or [] if f.get("batch") == b.pk and f.get("path")]
    return paths[0] if len(paths) == 1 else ""


def human(n: int) -> str:
    for unit in ("Б", "КБ", "МБ"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ГБ"


# ===================== умумий кўриниш ва тозалаш =====================
def _auto_backups() -> list:
    from .backup import backups_dir
    d = backups_dir()
    return sorted((p for p in d.glob("*.zip") if p.name.startswith(("tiklashdan-oldin-", "qoshishdan-oldin-"))),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def _old_uploads():
    now = timezone.now()
    from django.db.models import Q
    return Intake.objects.filter(kind=Batch.Kind.FILE).filter(
        Q(status=Intake.Status.CANCELLED, updated__lt=now - timedelta(days=OLD_CANCELLED_DAYS))
        | Q(status=Intake.Status.FAILED, updated__lt=now - timedelta(days=OLD_FAILED_DAYS)))


def _owned_dirs() -> set:
    """Юклаш ёзуви ёки тўплам файли кўрсатадиган ``up_*`` папкалар."""
    base = (_media() / UPLOAD_DIR).resolve()
    out = {upload_root(it) for it in Intake.objects.filter(kind=Batch.Kind.FILE)}
    for fp in Batch.all_objects.exclude(file_path="").values_list("file_path", flat=True):
        parts = PurePosixPath(fp).parts
        if len(parts) > 1 and parts[1].startswith("up_"):
            out.add((base / parts[1]).resolve())
    out.discard(None)
    return out


def _orphans() -> list:
    cutoff = time.time() - 24 * 3600
    owned = _owned_dirs()
    return [d for d in (_media() / UPLOAD_DIR).glob("up_*")
            if d.is_dir() and d.resolve() not in owned and d.stat().st_mtime < cutoff]


def _done_views():
    """Ҳамма файли базага ёзилган юклашлар — уларнинг Excel кўринишлари фақат манбани очиб кўриш учун керак."""
    return [it for it in Intake.objects.filter(kind=Batch.Kind.FILE, status=Intake.Status.DONE) if upload_root(it)]


def drop_views(it: Intake) -> int:
    """Базага ёзилган юклашнинг ҳамма файлларини ўчиради (нархлар қолади, «Excel кўриниши» ва «асл файл» очилмайди)."""
    d = upload_root(it)
    n = _size(d) if d else 0
    if d is not None:
        shutil.rmtree(d, ignore_errors=True)
    files = [{**f, "path": ""} for f in it.files or []]
    pr = {**(it.progress or {}), "src": [], "pruned": True, "dropped": True}
    Intake.objects.filter(pk=it.pk).update(files=files, progress=pr, file_path="")
    if d is not None:
        prefix = d.relative_to(_media().resolve()).as_posix() + "/"
        Batch.all_objects.filter(file_path__startswith=prefix).update(file_path="")
    return n


def _written(it: Intake) -> bool:
    return any(f.get("done") for f in it.files or [])


def plan() -> dict:
    """Нима қанча жой эгаллайди ва тозаласа қанча бўшайди — «Тозалаш» саҳифаси учун."""
    m = _media()
    base = m / UPLOAD_DIR
    total = _size(base)
    prunable, n_prune = 0, 0
    for it in Intake.objects.filter(kind=Batch.Kind.FILE).exclude(status=Intake.Status.READING):
        if it.status in (Intake.Status.OPEN, Intake.Status.DONE):
            n = prune_upload(it, dry=True)
            if n:
                prunable += n
                n_prune += 1
    old = [it for it in _old_uploads() if not _written(it)]
    old_size = sum(_size(upload_root(it)) for it in old if upload_root(it))
    cutoff = time.time() - 24 * 3600
    orphans = _orphans()
    backups = _auto_backups()[KEEP_BACKUPS:]
    views = _done_views()
    preview = base / "preview"
    tmp = [p for p in (base / "backups" / "tmp").glob("*") if p.is_file() and p.stat().st_mtime < cutoff] if (base / "backups" / "tmp").is_dir() else []
    rows = [
        {"key": "prune", "label": "Ўқиб бўлинган юклашлардаги керакмас файллар",
         "hint": "ZIP / RAR архивлар (ичидаги PDF, расм, Word билан), Excel'га ўгирилган .xls асллари, нархсиз файллар. "
                 "Excel кўриниши қолади.", "size": prunable, "count": n_prune},
        {"key": "old", "label": "Эски бекор қилинган ва ўқилмаган юклашлар",
         "hint": f"Бекор қилинганига {OLD_CANCELLED_DAYS} кундан ошган ёки ўқиб бўлмаган, базага ҳеч нарса ёзилмаган — "
                 "навбати ва файллари бутунлай ўчади.", "size": old_size, "count": len(old)},
        {"key": "orphans", "label": "Эгаси йўқ папкалар", "hint": "Юклаш ёзуви ўчиб кетган, файллари қолган.",
         "size": sum(_size(d) for d in orphans), "count": len(orphans)},
        {"key": "backups", "label": "Эски авто захира нусхалар",
         "hint": f"Тиклаш / қўшишдан олдин ўзи олинганлар — энг янги {KEEP_BACKUPS} таси қолади.",
         "size": sum(p.stat().st_size for p in backups), "count": len(backups)},
        {"key": "views", "label": "Базага ёзилган файлларнинг Excel нусхалари", "optional": True,
         "hint": "Нархлар ва тарих қолади, лекин бу юклашларда «Excel кўриниши» ва «асл файл» энди очилмайди. "
                 "Жой жуда керак бўлгандагина.", "size": sum(_size(upload_root(it)) for it in views), "count": len(views)},
        {"key": "cache", "label": "Кўриниш кеши ва вақтинча файллар",
         "hint": "Excel кўриниши учун тайёрланган нусхалар — керак бўлса ўзи қайта тузилади.",
         "size": _size(preview) + sum(p.stat().st_size for p in tmp), "count": None},
    ]
    return {"total": total, "rows": rows, "free": sum(r["size"] for r in rows if not r.get("optional")),
            "any": any(r["size"] for r in rows)}


def cleanup(keys=None) -> dict:
    """``keys`` — ``plan()`` даги қаторлар (``None`` — ҳаммаси). ``{key: бўшаган байт}``."""
    keys = set(keys or ("prune", "old", "orphans", "backups", "cache"))  # «views» — фақат аниқ сўралса
    m = _media()
    base = m / UPLOAD_DIR
    out = {}
    if "prune" in keys:
        out["prune"] = sum(prune_upload(it) for it in Intake.objects.filter(
            kind=Batch.Kind.FILE, status__in=(Intake.Status.OPEN, Intake.Status.DONE)))
    if "old" in keys:
        n = 0
        for it in _old_uploads():
            if _written(it):
                continue
            d = upload_root(it)
            if d is not None:
                n += _size(d)
                shutil.rmtree(d, ignore_errors=True)
            it.delete()
        out["old"] = n
    if "orphans" in keys:
        n = 0
        for d in _orphans():
            n += _size(d)
            shutil.rmtree(d, ignore_errors=True)
        out["orphans"] = n
    if "backups" in keys:
        n = 0
        for p in _auto_backups()[KEEP_BACKUPS:]:
            n += p.stat().st_size
            p.unlink(missing_ok=True)
        out["backups"] = n
    if "views" in keys:
        out["views"] = sum(drop_views(it) for it in _done_views())
    if "cache" in keys:
        n = 0
        preview = base / "preview"
        if preview.is_dir():
            n += _size(preview)
            shutil.rmtree(preview, ignore_errors=True)
        cutoff = time.time() - 24 * 3600
        for p in (base / "backups" / "tmp").glob("*") if (base / "backups" / "tmp").is_dir() else []:
            if p.is_file() and p.stat().st_mtime < cutoff:
                n += p.stat().st_size
                p.unlink(missing_ok=True)
        out["cache"] = n
    log.info("narxbaza: тозалаш — %s", {k: human(v) for k, v in out.items()})
    return out

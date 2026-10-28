"""Butun papkani (yoki ZIP/RAR arxivni) yuklash: ichki papkalar bo'yicha qismlarga ajratish, fonda
tahlil, smetachi «Ҳисоблашни бошлаш»ni bosgach narxlash, daraxt va ZIP.

Smetachi papkasida odatda har bir ichki papka bitta qism: ``…_lokal.xls`` + ``…_res.xls``
(va oldingi natija ``…_narxlandi.xlsx``). Har bir ichki papka alohida ``Batch`` bo'ladi:
listlar turi mazmunidan aniqlanadi, juftlanadi va tasdiqlashsiz narxlanadi. Juftlik topilmasa —
qism «Текширинг» bo'lib qoladi, smetachi obyekt sahifasida o'zi tanlaydi.
"""
import shutil
import threading
import time
from datetime import timedelta
import zipfile
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from core import convert
from core.batch import KIND_LABELS

from .templatetags.narx import file_title, readable
from .archives import ArchiveError, extract_excel, is_archive, safe_part
from .models import Batch, Job, Project
from .services import ALLOWED_EXT, UploadError, _save, analyze_batch, batch_outputs, confirm_batch, sheet_names

MAX_PROJECT_FILES = 400
WORKING = (Batch.Stage.QUEUED, Batch.Stage.CONVERTING, Batch.Stage.PRICING)
STALE_AFTER = timedelta(minutes=5)  # shuncha vaqt belgi bo'lmasa — ish to'xtab qolgan (server qayta yondi)


# ===================== yuklash =====================
def skip_reason(rel: str) -> str:
    """Nega fayl olinmaydi ("" — olinadi)."""
    name = PurePosixPath(rel).name
    if name.startswith("~$"):
        return "вақтинча файл (Excel очиқ)"
    if PurePosixPath(name).suffix.lower() not in ALLOWED_EXT:
        return "Excel файл эмас"
    if "_narxlandi" in PurePosixPath(name).stem.lower():
        return "олдинги натижа"
    return ""


def is_shown(rel: str) -> bool:
    """Daraxtda faqat Excel fayllar (vaqtinchalik «~$» siz): PDF, rasm va boshqalar smetachini chalg'itadi."""
    name = PurePosixPath(rel).name
    return PurePosixPath(name).suffix.lower() in ALLOWED_EXT and not name.startswith("~$")


def clean_path(raw: str) -> PurePosixPath:
    """Brauzer bergan nisbiy yo'l: «..», bo'sh qismlar va diskni olib tashlaydi."""
    parts = [p.strip() for p in str(raw).replace("\\", "/").split("/")]
    parts = [safe_part(p) for p in parts if p and p not in (".", "..") and not p.endswith(":")]
    parts = [p for p in parts if p]
    if not parts:
        raise UploadError(f"Нотўғри файл йўли: {raw!r}")
    return PurePosixPath(*parts)


def create_project(owner, files, paths, skipped=()) -> Project:
    """``files`` — yuklangan fayllar, ``paths`` — ularning papka ichidagi yo'li (``webkitRelativePath``),
    ``skipped`` — brauzer yubormagan fayllar yo'li (daraxtda ko'rsatish uchun). ZIP/RAR/7z arxivlar
    ochiladi (ichma-ich): ichidagi Excel'lar arxiv turgan joyda, arxiv nomli papkada bo'ladi."""
    if not files:
        raise UploadError("Папкада Excel файл топилмади")
    if len(files) != len(paths):
        raise UploadError("Файллар ва йўллар сони мос эмас")
    project = Project(owner=owner)
    items = []  # (yo'l, manba: yuklangan fayl yoki arxivdan chiqqan fayl, hajmi)
    archives = []
    try:
        for f, raw in zip(files, paths):
            rel = clean_path(raw)
            if not is_archive(rel.name):
                items.append((rel, f, f.size))
                continue
            if f.size > settings.NARXLASH_MAX_ARCHIVE:
                raise UploadError(f"{rel.name}: архив жуда катта ({f.size // (1024 * 1024)} МБ)")
            saved = project.abs(f"unpack/{len(archives)}{rel.suffix.lower()}")
            _save(f, saved)
            archives.append(rel)
            try:
                found = extract_excel(saved, project.abs(f"unpack/{len(archives)}"), rel.parent, name=rel.name)
            except ArchiveError as e:
                raise UploadError(f"{rel.name}: {e}") from e
            items += [(r, p, p.stat().st_size) for r, p in found]
        if len(items) > MAX_PROJECT_FILES:
            raise UploadError(f"Бир марта кўпи билан {MAX_PROJECT_FILES} та файл")
        rels = [r for r, _src, _size in items]
        others = [clean_path(p) for p in skipped]
        if not any(is_shown(str(r)) for r in rels):
            raise UploadError("Папкада ва архивларда Excel файл топилмади (фақат .xls, .xlsx, .xlsm)")
        # ildiz papka nomi — loyiha nomi; yo'llar undan boshlab saqlanadi
        roots = {r.parts[0] for r in rels + others if len(r.parts) > 1}
        root = roots.pop() if len(roots) == 1 and all(len(r.parts) > 1 for r in rels) else ""
        strip = (lambda r: PurePosixPath(*r.parts[1:])) if root else (lambda r: r)
        project.name = root or (archives[0].stem if len(archives) == 1 else "Папка")

        entries, seen = [], set()
        for rel, src, size in items:
            rel = strip(rel)
            if str(rel).lower() in seen:
                continue  # bir xil yo'l ikki marta (masalan, arxiv va papkada) — birinchisi
            seen.add(str(rel).lower())
            reason = skip_reason(str(rel))
            if not reason and size > settings.NARXLASH_MAX_UPLOAD:
                reason = f"жуда катта ({size // (1024 * 1024)} МБ)"
            if not reason:
                dest = project.abs(f"src/{rel}")
                try:
                    if isinstance(src, Path):
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dest))
                    else:
                        _save(src, dest)
                except OSError as e:  # bitta fayl (nomi, yo'l uzunligi) butun yuklashni to'xtatmasin
                    reason = f"сақлаб бўлмади: {e.strerror or e}"
            if is_shown(str(rel)):
                entries.append({"path": str(rel), "size": size, "skip": reason})
        for rel in others:
            rel = strip(rel)
            if is_shown(str(rel)):
                entries.append({"path": str(rel), "size": None, "skip": skip_reason(str(rel)) or "юкланмади"})
        entries.sort(key=lambda e: e["path"].lower())
        project.files = entries
        shutil.rmtree(project.abs("unpack"), ignore_errors=True)
        project.save()

        folders = {}
        for e in entries:
            if not e["skip"]:
                parent = str(PurePosixPath(e["path"]).parent)
                folders.setdefault("" if parent == "." else parent, []).append(e["path"])
        if not folders:
            raise UploadError("Папкада нархланадиган Excel файл топилмади (фақат .xls, .xlsx, .xlsm)")
        for folder, srcs in sorted(folders.items()):
            Batch.objects.create(
                owner_id=project.owner_id, project=project, folder=folder, stage=Batch.Stage.QUEUED,
                name=PurePosixPath(folder).name or project.name,
                files=[{"name": PurePosixPath(s).name, "src": s, "path": "", "sheets": []} for s in srcs],
            )
    except Exception:
        shutil.rmtree(project.dir, ignore_errors=True)
        if project.pk and Project.objects.filter(pk=project.pk).exists():
            project.delete()
        raise
    start(project)
    return project


# ===================== fonda narxlash =====================
_running = set()
_lock = threading.Lock()


def start(project: Project):
    """Fonda ishlov berishni boshlaydi (allaqachon ishlayotgan bo'lsa — hech narsa qilmaydi)."""
    with _lock:
        if project.id in _running:
            return
        _running.add(project.id)
    threading.Thread(target=_run, args=(project.id,), daemon=True, name=f"project-{project.id}").start()


def ensure_running(project: Project):
    """Server qayta ishga tushsa, to'xtab qolgan loyiha davom ettiriladi. Boshqa jarayon hali
    ishlayotgan bo'lsa (yaqinda belgi qo'ygan) — tegilmaydi, aks holda qism ikki marta narxlanadi."""
    if project.status != Project.Status.PROCESSING or project.id in _running:
        return
    if project.heartbeat and timezone.now() - project.heartbeat < STALE_AFTER:
        return
    start(project)


class Stopped(Exception):
    """Smetachi «Тўхтатиш»ni bosdi."""


def _check_stop(project_id):
    if Project.objects.filter(id=project_id, stop_requested=True).exists():
        raise Stopped


def _run(project_id):
    """Fondagi ish: yangi qismlar ochiladi va juftlanadi (narxlanmaydi — smetachi avval tekshiradi);
    «Ҳисоблашни бошлаш» bosilgan (PRICING) qismlar narxlanadi. «Тўхтатиш» — har bir fayldan oldin tekshiriladi."""
    close_old_connections()
    try:
        project = Project.objects.get(id=project_id)
        Project.objects.filter(id=project_id).update(heartbeat=timezone.now())
        try:
            for part in project.parts.filter(stage__in=WORKING).order_by("folder"):
                _check_stop(project_id)
                if part.stage == Batch.Stage.PRICING:
                    price_part(part)
                else:
                    analyze_part(part)
        except Stopped:
            pass
        project.refresh_from_db()
        if project.stop_requested:
            _finish_stop(project)
        else:
            settle(project)
    finally:
        with _lock:
            _running.discard(project_id)
        close_old_connections()


def settle(project: Project):
    """Fondagi ish tugagach: hisoblashni kutayotgan qism bo'lsa yoki hali hech narsa narxlanmagan bo'lsa —
    «текширинг», aks holda «тайёр»."""
    parts = project.parts.all()
    waiting = parts.filter(stage=Batch.Stage.READY).exists()
    priced = Job.objects.filter(batch__project=project).exists()
    project.status = Project.Status.READY if waiting or not priced else Project.Status.DONE
    project.save(update_fields=["status"])


def start_pricing(project: Project) -> int:
    """«Ҳисоблашни бошлаш»: tayyor qismlar navbatga qo'yiladi va fonda narxlanadi. Nechta qism."""
    n = project.parts.filter(stage=Batch.Stage.READY).update(stage=Batch.Stage.PRICING, message="")
    if n:
        project.status = Project.Status.PROCESSING
        project.save(update_fields=["status"])
        start(project)
    return n


def _finish_stop(project: Project):
    """To'xtatilgandan keyin: ochilmagan qismlar «Тўхтатилди», narxlash navbatidagilari yana «тайёр»."""
    project.parts.filter(stage__in=(Batch.Stage.QUEUED, Batch.Stage.CONVERTING)).update(
        stage=Batch.Stage.STOPPED, message="тўхтатилди — «Давом эттириш» билан қайта бошланади")
    project.parts.filter(stage=Batch.Stage.PRICING).update(stage=Batch.Stage.READY, message="")
    project.stop_requested = False
    project.save(update_fields=["stop_requested"])
    settle(project)


def stop_project(project: Project):
    """«Тўхтатиш»: fondagi ish keyingi fayldan oldin to'xtaydi. Ish allaqachon to'xtab qolgan bo'lsa — darhol."""
    project.stop_requested = True
    project.save(update_fields=["stop_requested"])
    if project.id not in _running:
        _finish_stop(project)


def resume_project(project: Project) -> int:
    """«Давом эттириш»: to'xtatilgan qismlar qayta navbatga."""
    n = project.parts.filter(stage=Batch.Stage.STOPPED).update(stage=Batch.Stage.QUEUED, message="")
    if n:
        project.status, project.stop_requested = Project.Status.PROCESSING, False
        project.save(update_fields=["status", "stop_requested"])
        start(project)
    return n


def wait_stopped(project: Project, seconds: float = 30) -> bool:
    """Fondagi ish to'xtaguncha kutadi (bitta fayl o'girilishi bir necha soniya)."""
    deadline = time.monotonic() + seconds
    while project.id in _running and time.monotonic() < deadline:
        time.sleep(0.3)
    return project.id not in _running


def is_working(project: Project) -> bool:
    """Fonda ishlanyaptimi (ochilyapti / narxlanyapti) — bunday paytda o'chirib bo'lmaydi."""
    if project.id in _running:
        return True
    return (project.status == Project.Status.PROCESSING and project.heartbeat is not None
            and timezone.now() - project.heartbeat < STALE_AFTER)


def delete_project(project: Project, force: bool = False):
    """Papka butunlay: yuklangan fayllar, har bir qism va natijalar (bazadan ham, diskdan ham).
    ``force`` — ishlayotgan bo'lsa, avval to'xtatiladi."""
    if is_working(project):
        if not force:
            raise UploadError("Ҳозир ишланяпти — аввал тўхтатинг")
        stop_project(project)
        if not wait_stopped(project):
            raise UploadError("Тўхтатиляпти — бир неча сониядан кейин қайта ўчиринг")
    dirs = [project.dir]
    for part in project.parts.all():
        dirs.append(part.dir)
        dirs += [j.dir for j in part.jobs.all()]
    project.delete()  # qismlar va smetalar CASCADE bilan
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


def _set(part: Batch, stage, message=""):
    part.stage, part.message = stage, message
    part.save()
    Project.objects.filter(id=part.project_id).update(heartbeat=timezone.now())


def ready_stage(part: Batch):
    """Juftliklar bo'yicha qism holati (narxlashdan oldin): tayyor / resurs tanlang / smeta emas."""
    ready = [p for p in part.pairs if p.get("include") and p.get("res")]
    locals_ = [s for s in part.sheets if s["role"] == "local" and not s.get("duplicate_of")]
    if ready:
        missing = len(part.pairs) - len(ready)
        return Batch.Stage.READY, (f"{missing} та локал учун ресурс топилмади — қўлда танланг" if missing else "")
    if locals_:
        return Batch.Stage.REVIEW, "локал бор, лекин унга мос ресурс топилмади — қўлда танланг"
    kinds = sorted({KIND_LABELS[s["kind"]] for s in part.sheets if s.get("kind") in KIND_LABELS})
    return Batch.Stage.EMPTY, f"смета эмас ({', '.join(kinds) or 'умумий ҳисоб ёки бошқа файл'}) — нархланмайди"


def analyze_part(part: Batch):
    """Bitta ichki papka: .xls -> .xlsx, listlar tahlili va juftlash. Narxlash — smetachi bosganda."""
    project = part.project
    try:
        _set(part, Batch.Stage.CONVERTING)
        for i, f in enumerate(part.files):
            _check_stop(part.project_id)
            src = project.abs(f"src/{f['src']}")
            xlsx = convert.ensure_xlsx(src, part.abs(f"conv/{i}"))
            if xlsx == src:  # .xlsx o'zgarishsiz qaytadi — qismning o'z nusxasi bo'lsin
                xlsx = part.abs(f"conv/{i}{src.suffix.lower()}")
                xlsx.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, xlsx)
            f["path"], f["sheets"] = str(xlsx.relative_to(part.dir)), sheet_names(xlsx)
        analyze_batch(part)
        # Papkada bitta resurs bo'lsa — juftsiz qolgan lokallar shu bilan narxlanadi
        # (smetachi har bir smetani o'z papkasiga «…_lokal» + «…_res» qilib qo'yadi)
        resources = [s for s in part.sheets if s["role"] == "resource" and not s.get("duplicate_of")]
        if len(resources) == 1:
            for p in part.pairs:
                if not p.get("res"):
                    p["res"], p["include"] = resources[0]["key"], True
                    p.setdefault("reasons", []).append("папкада ягона ресурс")
        part.save()
        _set(part, *ready_stage(part))
    except Stopped:
        _set(part, Batch.Stage.STOPPED, "тўхтатилди — «Давом эттириш» билан қайта бошланади")
        raise
    except Exception as e:  # bitta papka xatosi butun loyihani to'xtatmasin
        _set(part, Batch.Stage.FAILED, f"{type(e).__name__}: {e}")


def price_part(part: Batch, decisions=None):
    """Qismning tanlangan juftliklarini narxlaydi (``decisions`` — obyekt sahifasidagi tanlov)."""
    try:
        _set(part, Batch.Stage.PRICING)
        jobs = confirm_batch(part, decisions or {})
        ready = [p for p in part.pairs if p.get("include") and p.get("res")]
        failed = [j for j in jobs if j.status != j.Status.DONE]
        missing = len(part.pairs) - len(ready)
        if failed:
            _set(part, Batch.Stage.FAILED, failed[0].error)
        elif not jobs:
            _set(part, *ready_stage(part))
        elif missing:
            _set(part, Batch.Stage.REVIEW, f"{missing} та локал учун ресурс танланмаган")
        else:
            _set(part, Batch.Stage.DONE)
    except Exception as e:
        _set(part, Batch.Stage.FAILED, f"{type(e).__name__}: {e}")


# ===================== daraxt (VS Code «Explorer» kabi) =====================
_STAGE_RANK = {"failed": 6, "converting": 5, "pricing": 5, "queued": 4, "stopped": 3.5, "review": 3, "ready": 2.5,
               "warn": 2, "done": 1, "empty": 0}


def part_state(part: Batch, jobs) -> str:
    if part.stage == Batch.Stage.DONE and any(j.result.get("checks") for j in jobs):
        return "review"  # tekshiruvdan o'tmagan — «тайёр» deb ko'rsatilmaydi
    if part.stage == Batch.Stage.DONE and any(j.result.get("stats", {}).get("NOT_PRICED") for j in jobs):
        return "warn"
    return part.stage or "done"


def build_tree(project: Project, parts):
    """Papkalar daraxti: har bir tugunda holat, fayllar (roli, ochish havolasi) va natija fayllari."""
    root = {"name": project.name, "path": "", "folders": {}, "files": [], "part": None, "depth": 0}

    def folder_node(path: str):
        node = root
        if not path:
            return node
        acc = []
        for name in PurePosixPath(path).parts:
            acc.append(name)
            node = node["folders"].setdefault(name, {"name": name, "path": "/".join(acc), "folders": {},
                                                     "files": [], "part": None, "depth": len(acc)})
        return node

    by_folder = {p.folder: p for p in parts}
    for e in project.files:
        if not is_shown(e["path"]):
            continue  # eski loyihalarda saqlangan PDF va boshqalar
        parent = str(PurePosixPath(e["path"]).parent)
        folder_node("" if parent == "." else parent)["files"].append(dict(e, name=PurePosixPath(e["path"]).name))
    for p in parts:
        node = folder_node(p.folder)
        if p.stage == Batch.Stage.EMPTY:
            # papkada faqat umumiy hisob («Умумий қиймати») — smeta emas, asl smetalar ichki papkalarda
            for f in node["files"]:
                if not f["skip"]:
                    f["role"] = "summary"
            continue
        jobs = list(p.jobs.all())
        node["part"] = {"batch": p, "jobs": jobs, "state": part_state(p, jobs),
                        "not_priced": sum(j.result.get("stats", {}).get("NOT_PRICED", 0) for j in jobs),
                        "total": sum(t for j in jobs for t in [j.result.get("totals", {}).get("s_nds")]
                                     if isinstance(t, (int, float))),
                        # jadval uchun: qaysi lokal fayl qaysi resurs bilan narxlandi
                        "pairs": [dict(_job_keys(p, j), local=p.files[j.batch_file]["name"] if j.batch_file is not None else j.local_name,
                                       res=j.res_name.split(" · ")[0], job=j, title=readable(j.batch_label or j.local_sheet))
                                  for j in jobs],
                        "planned": _planned(p), "unused": _unused(p)}
        roles = {}
        for s in p.sheets:
            if not s.get("duplicate_of") and s["role"] != "unknown":
                roles.setdefault(s["file"], set()).add(s["role"])
        src_index = {f["src"]: i for i, f in enumerate(p.files)}
        for f in node["files"]:
            i = src_index.get(f["path"])
            if i is None:
                continue
            r = roles.get(i, set())
            f["role"] = "local" if "local" in r else "resource" if "resource" in r else ("unknown" if p.sheets else "")
            f["jobs"] = [j for j in jobs if j.batch_file == i]
        node["results"] = [{"name": n, "job": j} for n, j in _result_names(p, jobs)]

    def finish(node):
        node["folders"] = [finish(c) for c in sorted(node["folders"].values(), key=lambda c: _natural(c["name"]))]
        node["files"].sort(key=lambda f: _natural(f["name"]))
        states = [c["state"] for c in node["folders"] if c["state"]]
        if node["part"]:
            states.append(node["part"]["state"])
        node["state"] = max(states, key=lambda s: _STAGE_RANK.get(s, 0)) if states else ""
        node["not_priced"] = sum(c["not_priced"] for c in node["folders"]) + (node["part"]["not_priced"] if node["part"] else 0)
        node.setdefault("results", [])
        return node

    return finish(root)


def _planned(part: Batch):
    """Narxlashdan oldin: tanlangan juftliklar — «файл · лист → файл · лист» (jadvalda ko'rsatish uchun)."""
    sheets = {s["key"]: s for s in part.sheets}
    out = []
    for i, p in enumerate(part.pairs):
        loc, res = sheets.get(p["local"]), sheets.get(p.get("res"))
        if not loc or (res and not p.get("include")):
            continue
        out.append({"local": loc["file_name"], "local_sheet": loc["sheet"],
                    "title": readable(p.get("label") or loc.get("part") or file_title(loc["file_name"])),
                    "res": res["file_name"] if res else "", "res_sheet": res["sheet"] if res else "",
                    "same_file": bool(res) and loc["file"] == res["file"], "overlap": p.get("overlap", 0),
                    "lk": p["local"], "rk": p.get("res") or "", "i": i})
    return out


def _unused(part: Batch) -> int:
    """Hech bir lokalga biriktirilmagan resurs listlari soni — smetachi qismni ochib biriktirishi mumkin."""
    used = {p.get("res") for p in part.pairs}
    return sum(1 for s in part.sheets if s["role"] == "resource" and not s.get("duplicate_of") and s["key"] not in used)


def _job_keys(part: Batch, job) -> dict:
    """Narxlangan smetaning lokal va resurs listi kalitlari («fayl:list») — ko'rish oynasi uchun."""
    lk = f"{job.batch_file}:{job.local_sheet}" if job.batch_file is not None else ""
    i = next((n for n, p in enumerate(part.pairs) if p["local"] == lk), None)
    return {"lk": lk, "rk": part.pairs[i].get("res") if i is not None else "", "i": i}


def _natural(name: str):
    """«2. Блок-А» < «10. Благоустройство» (raqam bo'yicha)."""
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _result_names(part: Batch, jobs):
    """Natija fayllari nomi: «<lokal fayl>_narxlandi.xlsx» (smetachi kabi)."""
    out = []
    for j in jobs:
        if j.status != j.Status.DONE:
            continue
        stem = Path(part.files[j.batch_file]["name"]).stem if j.batch_file is not None else j.batch_label
        same = [x for x in jobs if x.batch_file == j.batch_file]
        name = f"{stem}_narxlandi.xlsx" if len(same) == 1 else f"{stem} · {j.local_sheet}_narxlandi.xlsx"
        out.append((name, j))
    return out


# ===================== hammasi bitta ZIP da =====================
def build_project_zip(project: Project) -> Path:
    """Har bir ichki papkaning natijasi o'z papkasida: «2. Блок-А/1. Подземный часть/…_narxlandi.xlsx».
    Smetachining qo'lda o'zgarishlari (narx, ustun, foiz) ham kiradi."""
    out_dir = project.abs("out")
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    zpath = out_dir / f"{project.name}_narxlandi.zip"
    n = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for part in project.parts.order_by("folder"):
            if not part.jobs.filter(status="done").exists():
                continue
            for path in batch_outputs(part):
                z.write(path, str(PurePosixPath(part.folder) / path.name) if part.folder else path.name)
                n += 1
    if not n:
        raise UploadError("Ҳали нархланган қисм йўқ")
    return zpath

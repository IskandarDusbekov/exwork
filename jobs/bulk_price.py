"""Папка бўйича бир хил нархланмаганлар: ҳамма сметалардаги «ТОПИЛМАДИ» қаторлари (бирлиги бор ресурслар)
номи ва бирлиги бўйича гуруҳланади — смета­чи бир марта нарх ёзади, у белгиланган файллардаги ҳамма шундай
қаторга қўйилади.

Гуруҳлаш фақат аниқ мослик бўйича (ном ва бирлик нормаллаштирилгандан кейин бир хил): «ўхшаш» номлар
бирлаштирилмайди — хато нарх бир неча файлга тарқалмасин. Нарх фақат ҳали нархланмаган қаторга ёзилади,
смета­чи қўлда қўйганининг устидан ёзилмайди.
"""
from collections import Counter

from core.normalize import norm_name, norm_unit

from . import recalc
from .models import Job, Project


def _jobs(project: Project, ids=None):
    qs = Job.objects.filter(batch__project=project, status=Job.Status.DONE, mode="smart").select_related("batch")
    if ids is not None:
        qs = qs.filter(id__in=ids)
    return qs.order_by("batch__folder", "batch_order")


def _title(job: Job) -> str:
    folder = job.batch.folder if job.batch else ""
    sheet = job.batch_label or job.local_sheet or job.local_name
    return f"{folder} · {sheet}" if folder else sheet


def _key(row) -> tuple:
    return norm_name(row.get("name")), norm_unit(row.get("unit"))


def open_rows(job: Job) -> dict:
    """Ҳали нархланмаган ресурс қаторлари {қатор: row}: бирлиги бор, тозаланмаган, қўлда нарх қўйилмаган
    (нарх қўйилиб, қайта ҳисоб кутаётгани ҳам олинмайди)."""
    from .views import _not_found_stage
    priced = set((job.prices or {}).keys())
    return {r["row"]: r for r in _not_found_stage(job.result.get("rows", []))["with_unit"]
            if str(r["row"]) not in priced and _key(r)[0]}


def _price(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def groups(project: Project) -> list:
    """[{name, unit, code, rows, status, files: [{job, title, rows: [...], near: {қатор: нарх}}], suggest: [...]}] —
    кўп файлда учрагани олдин. ``suggest`` — қаторларнинг яқин ресурс (RES) таклифлари бирлаштирилган: бир хил
    ном, бирлик ва нарх — битта, нечта қаторда таклиф қилингани билан (кўпи олдин)."""
    by_key = {}
    for job in _jobs(project).filter(sum_not_priced__gt=0):
        for r in open_rows(job).values():
            g = by_key.setdefault(_key(r), {"name": str(r["name"]).strip(), "unit": str(r.get("unit") or "").strip(),
                                            "codes": set(), "files": {}, "status": Counter(), "suggest": {},
                                            "cats": Counter()})
            if r.get("code"):
                g["codes"].add(str(r["code"]).strip())
            g["status"][r.get("status") or "NOT_FOUND"] += 1
            f = g["files"].setdefault(str(job.id), {"job": str(job.id), "title": _title(job), "rows": [], "near": {},
                                                    "details": []})
            f["rows"].append(r["row"])
            g["cats"][r.get("category") or "K"] += 1
            # жадвал учун: қатор сметада қаерда ва қанча
            qty_col = "E" if r.get("qty_total") in (None, "") else "F"
            f["details"].append({"row": r["row"], "num": _text(r.get("num")), "code": _text(r.get("code")),
                                 "qty": _price(r.get("qty")), "qty_col": qty_col,
                                 "work": r.get("ctx_work") or "", "section": r.get("ctx_section") or ""})
            near = r.get("near") or {}
            price = _price(near.get("price"))
            if price is not None:
                f["near"][r["row"]] = price
                k = (norm_name(near.get("name")), norm_unit(near.get("unit")), price)
                s = g["suggest"].setdefault(k, {"name": str(near.get("name") or "").strip(),
                                                "unit": str(near.get("unit") or "").strip(), "price": price,
                                                "score": 0, "same_unit": bool(near.get("same_unit", True)),
                                                "rows": 0, "files": set()})
                s["score"] = max(s["score"], near.get("score") or 0)
                s["rows"] += 1
                s["files"].add(str(job.id))
    out = []
    for g in by_key.values():
        files = list(g["files"].values())
        suggest = sorted(g["suggest"].values(), key=lambda s: (-s["rows"], -s["score"], s["price"]))
        for s in suggest:
            s["files"] = len(s["files"])
        out.append({"name": g["name"], "unit": g["unit"],
                    "code": next(iter(g["codes"])) if len(g["codes"]) == 1 else "",
                    "rows": sum(len(f["rows"]) for f in files), "files": files,
                    "status": g["status"].most_common(1)[0][0], "suggest": suggest[:5],
                    "category": g["cats"].most_common(1)[0][0],
                    "near_rows": sum(len(f["near"]) for f in files)})
    out.sort(key=lambda g: (-len(g["files"]), -g["rows"], g["name"].lower()))
    return out


def pending(project: Project) -> int:
    """Нечта смета қайта ҳисобланяпти (нарх қўйилгандан кейин)."""
    return sum(1 for j in Job.objects.filter(batch__project=project).only("recalc_seq", "recalc_done")
               if j.recalc_pending)


def apply(project: Project, items: list, undo: bool = False) -> dict:
    """``items``: [{"price": float, "rows": {job_id: [қатор, …]}}]. Нарх фақат ҳали нархланмаган қаторга ёзилади;
    ``undo`` — шу нарх қўйилган қаторлардан олиб ташланади (бошқа нарх қўйилган бўлса — тегилмайди).
    Ҳар бир смета бир марта қайта ҳисобланади. {"rows": ўзгарган қаторлар, "files": сметалар, "skipped": …}."""
    wanted = {}  # job_id → {қатор: нарх}
    for it in items:
        for job_id, rows in it["rows"].items():
            for r in rows:
                wanted.setdefault(str(job_id), {})[int(r)] = it["price"]
    changed_rows, changed_files, skipped = 0, 0, 0
    for job in _jobs(project, list(wanted)):
        want = wanted.pop(str(job.id))
        prices = dict(job.prices or {})
        allowed = set() if undo else set(open_rows(job))
        n = 0
        for r, price in want.items():
            if undo:
                if prices.get(str(r)) == price:
                    prices.pop(str(r))
                    n += 1
                else:
                    skipped += 1
            elif r in allowed:
                prices[str(r)] = price
                n += 1
            else:
                skipped += 1
        if n:
            recalc.request(job, prices=prices)
            changed_rows += n
            changed_files += 1
    skipped += sum(len(v) for v in wanted.values())  # бошқа папканинг ёки нархлаб бўлмайдиган сметаси
    return {"rows": changed_rows, "files": changed_files, "skipped": skipped}

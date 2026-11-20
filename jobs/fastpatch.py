"""Smetachi o'zgarishi — butun smetani qayta narxlamasdan, natija Excel faylining o'zida (``core.patch``).

20 000 qatorli smetada to'liq qayta hisob ~1 daqiqa; yamash — bir-ikki soniya. Qaysi o'zgarishlar:
qo'lda narx (yozish, taklifni qabul qilish, bekor qilish), L/M va boshqa ustunga o'tkazish, «ТОПИЛМАДИ»ni
olib tashlash / sariq belgilash / qaytarish, svodka foizlari.

Qanday: to'liq narxlash har qatorning smetachi qaroridan **oldingi** holatini (``calc_kit["base"]``) saqlaydi.
Yangi sozlamalar bilan har qator o'sha funksiya bilan (``core.pipeline.row_outcome``) qayta tuziladi, biz yozadigan
kataklar (G, H, I…M) to'liq narxlashdagi qoidalar bilan hisoblanadi va faylda turganidan farq qilganlari yoziladi.
Shuning uchun natija to'liq qayta narxlash bilan bir xil (testlarda katakma-katak solishtiriladi).

Qoidalar:
* Haqiqiy manba — sozlamalar (``job.prices``, ``categories``, ``cleared``, ``marked``, ``percents``); Excel ularning
  nusxasi. Keyingi to'liq qayta hisob aynan shu natijani beradi.
* Yamash qayta hisoblash navbati bilan **bir lease**'da (``recalc_worker``): bir vaqtda ikki yozuv bo'lmaydi.
  Navbatda yoki ketayotgan hisob bo'lsa — yamalmaydi, o'zgarish o'sha hisobga qo'shiladi (``recalc.request``).
* Yamab bo'lmasa (to'plam yo'q yoki eski, katak topilmadi, lokalning o'z yozuvi ustiga tushadi) — ``False``:
  chaqiruvchi to'liq hisobga o'tadi.

Bitta farq qoladi: qo'lda narx RES'dagi boshqa resursnikiga teng bo'lsa, to'liq hisob L/M **taklifini** o'sha
resurs bo'limidan oladi; yamash taklifni o'zgartirmaydi (narx va summalar baribir bir xil) — keyingi to'liq
hisobda yangilanadi.
"""
import gzip
import json
import logging
import os
import shutil
import threading
from copy import deepcopy

from django.db.models import F
from django.utils import timezone

from core.classify import COLUMN_KIND, KIND_COLUMN
from core.patch import NotPatchable, apply_cells
from core.pipeline import CLEARED, KIT_VERSION, MANUAL, NOT_FOUND, _check_priced, final_report, row_outcome
from core.svodka import percent_text, resolve_percents, svodka_layout
from core.writer import SUM_COLS, safe_amount_formula, target_column

from .models import Job

log = logging.getLogger(__name__)

KIT_NAME = "calc_kit.json.gz"
_SNAPSHOT = ("prices", "categories", "cleared", "marked", "percents")


def save_kit(job: Job, kit: dict):
    """To'liq hisobdan keyin (``recompute``) va har yamashdan keyin. Bo'sh to'plam — eski fayl o'chadi."""
    path = job.abs(KIT_NAME)
    if not kit:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_name(f".{path.name}.tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=3) as fh:
        json.dump(kit, fh, ensure_ascii=False, separators=(",", ":"), default=str)
    os.replace(tmp, path)


def load_kit(job: Job):
    path = job.abs(KIT_NAME)
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            kit = json.load(fh)
    except (OSError, ValueError):
        return None
    return kit if kit.get("version") == KIT_VERSION else None


def _jsonable(v):
    from .services import _jsonable as j
    return j(v)


def try_apply(job: Job, prices=None, categories=None, cleared=None, marked=None, percents=None) -> bool:
    """Sozlamani saqlab, natija faylini yamaydi (``recalc.request`` bilan bir xil argumentlar; None — o'zgarmaydi).
    True — yamaldi va saqlandi; False — hech narsa saqlanmadi, chaqiruvchi ``recalc.request`` qiladi."""
    from . import recalc
    from .services import apply_settings
    if job.mode != "smart" or job.status != Job.Status.DONE or not job.output_path:
        return False
    me = f"patch:{os.getpid()}:{threading.get_ident()}"
    # navbatda hisob yo'q va hech kim hisoblamayapti — shundagina egallaymiz
    if not Job.objects.filter(pk=job.pk, recalc_seq=F("recalc_done"), status=Job.Status.DONE).filter(
            recalc._free()).update(recalc_worker=me, recalc_heartbeat=timezone.now()):
        return False
    before = {f: deepcopy(getattr(job, f)) for f in _SNAPSHOT}
    ok = False
    try:
        apply_settings(job, None, None, job.fuzzy, None, percents, categories, prices, cleared, marked)
        ok = _patch(job)
    except Exception:  # noqa: BLE001 — yamab bo'lmadi: to'liq hisob (asl fayl buzilmagan)
        log.exception("Excel'ни ямаб бўлмади: %s", job.pk)
    finally:
        if not ok:
            for f, v in before.items():
                setattr(job, f, v)
        Job.objects.filter(pk=job.pk, recalc_worker=me).update(recalc_worker="", recalc_heartbeat=None)
    if Job.objects.filter(pk=job.pk).filter(recalc._pending()).exists():
        recalc.start(job.pk)  # yamash paytida boshqa o'zgarish navbatga tushgan
    if ok:
        try:
            from resbase.lm import harvest_safe
            harvest_safe(job)  # ускуна ва кабель базаси смета­чи танловидан ўрганади (тўлиқ ҳисобдагидек)
        except Exception:  # noqa: BLE001
            log.exception("lm harvest: %s", job.pk)
        start_warm(job.pk)
    return ok


def try_prices(job: Job, prices: dict) -> bool:
    return try_apply(job, prices=prices)


def warm_now(job_id):
    """Natija ko'rinishini tayyorlaydi (sahifa yangilanganda jadval darhol ochilsin)."""
    from .preview import warm
    warm(Job.objects.get(pk=job_id))


def start_warm(job_id):
    """Worker rejimida — worker bo'sh paytda o'zi tayyorlaydi (``preview.warm_next``); thread rejimida — oqimda."""
    from . import recalc
    if recalc.worker_mode():
        return

    def go():
        from django.db import close_old_connections
        try:
            warm_now(job_id)
        finally:
            close_old_connections()

    threading.Thread(target=go, daemon=True, name=f"warm-{job_id}").start()


def _ikeys(d) -> dict:
    return {int(k): v for k, v in (d or {}).items()}


def _blank_yellow(rows) -> tuple:
    """G'da «narx kutyapti» rangi: qizil — tozalangan, topilmagan; sariq — smetachi belgilagan."""
    blank = sorted(r["row"] for r in rows if r.get("cleared") and not r.get("marked") and r.get("status") == NOT_FOUND)
    yellow = sorted(r["row"] for r in rows if r.get("marked"))
    return blank, yellow


def _patch(job: Job) -> bool:
    kit = load_kit(job)
    if kit is None:
        return False
    from .services import lm_auto_of
    if bool(kit.get("lm_auto", True)) != lm_auto_of(job.owner_id):
        return False  # «Ускуна ва кабель» созламаси ўзгарган — L/M тақсимоти бошқа: тўлиқ ҳисоб
    result = deepcopy(job.result or {})
    rows = result.get("rows", [])
    start, end = int(kit["start"]), int(kit["end"])
    written = kit["written"]
    base, kinds0, hints = _ikeys(kit["base"]), _ikeys(kit["kinds0"]), _ikeys(kit["hints"])
    repeats, lm_orig = set(kit["repeats"]), set(kit["lm_orig"])
    extra = [tuple(c) for c in kit["extra"]]
    extra_letters = {c for c, _n in extra}
    lm_cols = [c for c in SUM_COLS[4:]] + [c for c, _n in extra if c not in SUM_COLS]

    prices = {int(r): float(p) for r, p in (job.prices or {}).items()}
    cats = {int(r): c for r, c in (job.categories or {}).items() if start <= int(r) <= end}
    if any(c not in COLUMN_KIND and c not in extra_letters for c in cats.values()):
        return False
    to_clear = {int(r) for r in (job.cleared or [])} | {int(r) for r in (job.marked or [])}
    marked = {int(r) for r in (job.marked or [])}
    if set(prices) - set(base):
        return False  # narx resurs bo'lmagan qatorga — to'liq hisob hal qiladi

    old_blank, old_yellow = _blank_yellow(rows)
    cells, drop, log_rows = {}, set(), {}
    stats = dict(result.get("stats") or {})
    for row in rows:
        r = row["row"]
        if r in repeats:
            continue
        if r in base:
            value, st, note, orig, real_unit = base[r]
            value, st, note, orig, cleared_now = row_outcome(value, st, note, orig, real_unit, r in to_clear,
                                                             prices.get(r), row.get("suggested_price"))
        else:  # nomsiz qator — faqat ustuni o'zgarishi mumkin
            value, st, note, orig, cleared_now = written.get(f"G{r}"), row.get("status"), row.get("note"), None, False
        kind = COLUMN_KIND.get(cats[r], cats[r]) if r in cats else kinds0[r]
        col, skip = target_column(kind), st == CLEARED
        want = {f"G{r}": value, f"H{r}": None if skip else safe_amount_formula(r)}
        for c in "IJK":
            want[f"{c}{r}"] = f"=H{r}" if col == c and not skip else None
        if col not in "IJK" and not skip:
            want[f"{col}{r}"] = f"=H{r}"
        for addr, v in want.items():
            if addr not in written or written[addr] != v or type(written[addr]) is not type(v):
                cells[addr] = v
        for c in lm_cols:
            addr = f"{c}{r}"
            if addr not in want and addr in written:
                if addr in lm_orig:
                    return False  # lokalning o'z yozuvi bor edi — to'liq hisob uni tiklaydi
                drop.add(addr)

        if r in base and (st, value, note) != (row.get("status"), row.get("g"), row.get("note")):
            log_rows[r] = (st, value, note)
        if st != row.get("status"):
            stats[row.get("status")] = stats.get(row.get("status"), 0) - 1
            stats[st] = stats.get(st, 0) + 1
        hint = hints.get(r)
        row.update(g=value, status=st, note=note, orig_status=orig if orig is not None else st, manual=st == MANUAL,
                   cleared=cleared_now, marked=cleared_now and r in marked, kind=kind, category=col, override=r in cats,
                   suggested_category=None, suggest_reason="")
        row.pop("suggest_sure", None)
        if hint and KIND_COLUMN[hint[0]] != col:
            row["suggested_category"], row["suggest_reason"], row["suggest_sure"] = KIND_COLUMN[hint[0]], hint[1], hint[2]

    # svodka foizlari: D kataklari va «Всего с НДС … %» matni
    percents = resolve_percents(job.percents)
    layout = svodka_layout(extra)
    for key, _text, pct in layout:
        if pct is None or key not in kit["svodka"]:
            continue
        r = int(kit["svodka"][key][1:])
        want = {f"D{r}": percents[key]}
        if key == "s_nds":
            want[f"C{r}"] = f"Всего с НДС      {percent_text(percents[key])} %"
        for addr, v in want.items():
            if written.get(addr) != v:
                cells[addr] = v

    blank, yellow = _blank_yellow(rows)
    cf = {"blank": blank, "yellow": yellow} if (blank, yellow) != (old_blank, old_yellow) else None
    out = job.abs(job.output_path)
    tmp = out.with_name(f".{out.stem}.patch{out.suffix}")
    shutil.copyfile(out, tmp)
    try:
        done = apply_cells(tmp, kit, cells, drop, log=log_rows, cf=cf)
    except NotPatchable as e:
        tmp.unlink(missing_ok=True)
        log.info("ямалмади (%s): %s", job.pk, e)
        return False
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    new_written = done.kit["written"]
    for row in rows:
        h = f"H{row['row']}"
        if row["row"] not in repeats:
            row["h"] = _jsonable(done.values.get(h)) if isinstance(new_written.get(h), str) else None
    problems = []
    for row in rows:
        r = row["row"]
        g = row.get("g")
        if r in base and not (isinstance(g, (int, float)) and not isinstance(g, bool)) and row["status"] != CLEARED:
            problems.append({"row": r, "name": str(row.get("name")), "unit": "" if row.get("unit") is None else str(row["unit"]),
                             "status": row["status"], "picked_name": row.get("picked_name"), "score": row.get("score"),
                             "written": g, "note": row.get("note") or "", "suggested_price": row.get("suggested_price")})
    totals = {k: _jsonable(v) for k, v in done.totals.items()}
    checks = _check_priced(new_written, rows, start, end, kit["ln"], kit["lu"])
    warnings, checks = final_report(kit.get("base_warnings") or [], stats, problems, rows, totals, checks)
    result.update(stats=stats, problems=problems, totals=totals, checks=checks[:200], warnings=warnings,
                  percents=percents, categories={str(r): c for r, c in cats.items()},
                  prices={str(r["row"]): r["g"] for r in rows if r.get("status") == MANUAL},
                  cleared=sorted(r["row"] for r in rows if r.get("cleared")),
                  marked=sorted(r["row"] for r in rows if r.get("marked")))
    if done.widened:
        cols = dict(result.get("columns") or {})
        cols["widened"] = ", ".join(sorted(set(filter(None, (cols.get("widened") or "").split(", "))) | set(done.widened)))
        result["columns"] = cols

    from .services import replace_file
    try:
        replace_file(tmp, out)  # файлни шу пайт ким­дир ўқиётган бўлса (Windows) — бир оз кутади
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    save_kit(job, done.kit)
    job.result, job.preview_ready = result, False
    from .services import SETTING_FIELDS
    job.save(update_fields=SETTING_FIELDS + ["result", "preview_ready"])  # recalc.request сақлагани каби
    return True

"""Ускуна ва кабель базаси: ўзи ўрганиш (нархланган сметалардан), админ ўргатиши (бирма-бир ва Excel) ва
нархлашга бериш (``core.lmbase.LMBase``).

Ўзи ўрганиш — ҳар смета қайта ҳисобланганда (``harvest_job``) шу сметанинг далиллари алмаштирилади:

* RES'да аниқ топилган ресурс «ОБОРУДОВАНИЕ» / «КАБЕЛЬ» бўлимида — шу тур (``RES``);
* RES'да материал бўлимида, лекин номи «кабель…/насос…» — «ускуна эмас» (сўз бўйича нотўғри таклиф қилинмасин);
* смета­чи қаторни тизим таклифидан бошқа устунга кўчирган (L/M га ёки таклиф қилинган L/M дан K га) — ``USER``.
  Таклифнинг ўзини қабул қилгани ҳисобланмайди: «Автоматик» усул ҳам шундай ёзади, база ўзини ўзи
  «тасдиқлаб» кетмасин.

Тури — овозлар кўпи (смета­чи овози 2 та RES овозига тенг); тенг бўлса — аниқ эмас (таклиф берилмайди).
Смета­чи овози фойдаланувчилар бўйича саналади: битта смета­чининг хатоси (масалан «ВОДА»ни L га кўчиргани)
унинг 20 та сметасида такрорланса ҳам — битта овоз. RES далили бўлмаса, тур камида ``USER_OWNERS_MIN`` та
ҳар хил смета­чи бир хил деганда белгиланади; ундан кам — панелда «тасдиқланг» бўлиб кутади.
Админ ўргатгани (``locked``) овозлардан устун.
"""
import hashlib
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, Optional

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.classify import CABLE, EQUIPMENT, KIND_COLUMN, name_hint
from core.lmbase import LMBase, lm_key

from .models import LMEntry, LMVote
from .resfile import res_code

log = logging.getLogger(__name__)

USER_WEIGHT = 2
USER_OWNERS_MIN = 2  # RES далилисиз — шунча ҳар хил смета­чи бир хил деса
STRONG_VOTES = 3  # шунча (ва ундан кўп) бир хил овоз, қарама-қаршиси йўқ — ишончли
LM = ("L", "M")
LABELS = {"M": "ускуна", "L": "кабель", "K": "ускуна эмас"}
ZERO = ("WORK_HEADER", "MACHINIST", "CLEARED", "")


# ===================== ўзи ўрганиш =====================
def accepted_rows(job) -> set:
    """L/M га тизим таклифини қабул қилиб ўтказилган қаторлар (смета­чи овози эмас). Эски сметада маълумот йўқ —
    номидаги сўз шу турни айтадиган кўчиришлар қабул қилинган таклиф деб ҳисобланади."""
    if getattr(job, "lm_accepted", None) is not None:
        return {int(r) for r in job.lm_accepted}
    names = {r.get("row"): r.get("name") for r in (job.result or {}).get("rows", [])}
    out = set()
    for r, c in (job.categories or {}).items():
        hint = name_hint(names.get(int(r)) or "") if c in LM else None
        if hint and KIND_COLUMN[hint[0]] == c:
            out.add(int(r))
    return out


_FP = {}


def res_source(job) -> str:
    """RES файлнинг изи (мазмуни бўйича): бир хил файл бир неча сметада ёки қайта юкланса — битта манба."""
    rel = getattr(job, "res_path", "") or ""
    if not rel:
        return ""
    try:
        path = Path(job.dir) / rel
        st = path.stat()
        k = (str(path), st.st_size, st.st_mtime_ns)
        if k not in _FP:
            if len(_FP) > 5000:
                _FP.clear()
            _FP[k] = hashlib.sha1(path.read_bytes()).hexdigest()
        return _FP[k]
    except (OSError, ValueError, TypeError, AttributeError):
        return ""


def job_evidence(job) -> List[dict]:
    """Сметадан далиллар: [{key, name, unit, code, category, kind, owner, src}]."""
    res = job.result or {}
    secs = res.get("res_sections") or []
    overrides = {int(r): c for r, c in (job.categories or {}).items()}
    accepted = accepted_rows(job) if overrides else set()
    src = res_source(job) if secs else ""
    out = []
    for r in res.get("rows", []):
        name = str(r.get("name") or "").strip()
        key = lm_key(name)
        if not key or r.get("status") in ZERO or not str(r.get("unit") or "").strip():
            continue
        if r.get("default_category") in ("I", "J"):
            continue  # иш ҳақи ва машина — ускуна/кабель бўлмайди (L га кўчирилган бўлса ҳам — хато)
        base = {"key": key, "name": name[:500], "unit": str(r.get("unit") or "").strip()[:40],
                "code": res_code(r.get("code"))[:12], "owner": getattr(job, "owner_id", None), "src": ""}
        idx = r.get("res_section")
        col = secs[idx]["column"] if r.get("status") == "EXACT" and isinstance(idx, int) and 0 <= idx < len(secs) else None
        if col in LM:
            out.append({**base, "category": col, "kind": LMVote.Kind.RES, "src": src})
        elif col == "K":
            hint = name_hint(name)
            if hint and hint[0] in (CABLE, EQUIPMENT):
                out.append({**base, "category": "K", "kind": LMVote.Kind.RES, "src": src})
        chosen = overrides.get(r["row"])
        hint_col = r.get("suggested_category")
        if chosen in LM and chosen != hint_col and r["row"] not in accepted:
            out.append({**base, "category": chosen, "kind": LMVote.Kind.USER})
        elif chosen == "K" and hint_col in LM:
            out.append({**base, "category": "K", "kind": LMVote.Kind.USER})
    return out


def _decide(votes: dict) -> str:
    """``votes``: {"M": RES сметалар сони, "user_M": смета­чилар (фойдаланувчилар) сони}."""
    score = Counter()
    for k, n in votes.items():
        score[k[-1]] += n * (USER_WEIGHT if k.startswith("user_") else 1)
    if not score:
        return ""
    has_res = any(not k.startswith("user_") for k in votes)
    if not has_res and max(votes.values()) < USER_OWNERS_MIN:
        return ""  # битта смета­чининг танлови — админ тасдиқлагунча таклиф қилинмайди
    (lead, a), *rest = score.most_common()
    return "" if rest and rest[0][1] == a else lead


def recount(entry_ids) -> None:
    """Овозларни қайта санайди ва (админ ўргатмаганларда) турини белгилайди. Овози қолмаган ўзи ўрганган ёзув
    ўчирилади."""
    ids = set(entry_ids)
    if not ids:
        return
    tally, owners, files = defaultdict(Counter), defaultdict(set), defaultdict(set)
    for eid, cat, kind, job_id, owner, src in LMVote.objects.filter(entry_id__in=ids).values_list(
            "entry_id", "category", "kind", "job_id", "owner_id", "src"):
        if kind == LMVote.Kind.USER:
            owners[(eid, cat)].add(owner or job_id)  # смета­чи бўйича (эгаси номаълум — смета бўйича)
        else:
            files[(eid, cat)].add(src or job_id)  # RES файл бўйича (изи йўқ — смета бўйича)
    for (eid, cat), who in owners.items():
        tally[eid]["user_" + cat] = len(who)
    for (eid, cat), which in files.items():
        tally[eid][cat] = len(which)
    changed, empty = [], []
    for e in LMEntry.objects.filter(id__in=ids):
        votes = dict(tally.get(e.id, {}))
        if not votes and not e.locked:
            empty.append(e.id)
            continue
        cat = e.category if e.locked else _decide(votes)
        if votes != e.votes or cat != e.category:
            e.votes, e.category = votes, cat
            changed.append(e)
    if changed:
        now = timezone.now()
        for e in changed:
            e.updated = now  # bulk_update auto_now'ни ўзи қўймайди
        LMEntry.objects.bulk_update(changed, ["votes", "category", "updated"])
    if empty:
        LMEntry.objects.filter(id__in=empty, locked=False).delete()


@transaction.atomic
def harvest_job(job) -> int:
    """Сметанинг далилларини базага ёзади (аввалгиларининг ўрнига). Нечта далил."""
    ev = job_evidence(job)
    old = set(LMVote.objects.filter(job_id=job.id).values_list("entry_id", flat=True))
    LMVote.objects.filter(job_id=job.id).delete()
    if not ev:
        recount(old)
        return 0
    keys = {e["key"] for e in ev}
    entries = {e.key: e for e in LMEntry.objects.filter(key__in=keys)}
    new = []
    for e in ev:
        if e["key"] not in entries:
            entries[e["key"]] = LMEntry(key=e["key"], name=e["name"], unit=e["unit"], code=e["code"])
            new.append(entries[e["key"]])
    if new:
        LMEntry.objects.bulk_create(new, ignore_conflicts=True)
        entries.update({x.key: x for x in LMEntry.objects.filter(key__in=[n.key for n in new])})
    seen, votes = set(), []
    for e in ev:
        ent = entries[e["key"]]
        if (ent.id, e["kind"]) in seen:
            continue  # битта сметада бир ном бир неча марта — битта овоз
        seen.add((ent.id, e["kind"]))
        votes.append(LMVote(entry=ent, job_id=job.id, owner_id=e["owner"], src=e["src"], category=e["category"],
                            kind=e["kind"]))
        if not ent.code and e["code"]:
            LMEntry.objects.filter(id=ent.id, code="").update(code=e["code"])
    LMVote.objects.bulk_create(votes, ignore_conflicts=True)
    recount(old | {v.entry_id for v in votes})
    return len(votes)


def harvest_safe(job) -> None:
    """Нархлашдан кейин: база хатоси нархлашни бузмасин."""
    try:
        if job.mode == "smart" and job.status == "done":
            harvest_job(job)
    except Exception:  # noqa: BLE001
        log.exception("ускуна базаси: %s сметадан ўрганиб бўлмади", job.id)


def harvest_all(progress=None) -> dict:
    """Ҳамма нархланган сметалардан қайтадан (ўчирилган сметаларнинг далиллари олиб ташланади)."""
    from jobs.models import Job
    qs = Job.objects.filter(status="done", mode="smart")
    alive = set(qs.values_list("id", flat=True))
    stale = LMVote.objects.exclude(job_id__in=alive)
    touched = set(stale.values_list("entry_id", flat=True))
    stale.delete()
    recount(touched)
    n = votes = 0
    for job in qs.iterator(chunk_size=20):
        votes += harvest_job(job)
        n += 1
        if progress:
            progress(n, len(alive))
    return {"jobs": n, "votes": votes}


# ===================== нархлашга =====================
def _reason(e: LMEntry) -> str:
    if e.locked:
        return f"базада: админ «{LABELS[e.category]}» деб ўргатган"
    v = e.votes or {}
    res, user = v.get(e.category, 0), v.get("user_" + e.category, 0)
    parts = []
    if res:
        parts.append(f"{res} та RES файлида {'ускуна' if e.category == 'M' else 'кабель' if e.category == 'L' else 'материал'} бўлимида")
    if user:
        parts.append(f"{user} та смета­чи шундай танлаган")
    return "базада: " + ", ".join(parts) if parts else "базада"


def _strong(e: LMEntry) -> bool:
    if e.locked:
        return True
    v = e.votes or {}
    same = v.get(e.category, 0) + v.get("user_" + e.category, 0)
    other = sum(n for k, n in v.items() if k[-1] != e.category)
    return same >= STRONG_VOTES and not other


def load_base() -> Optional[LMBase]:
    """Тури аниқ ёзувлар. Ўхшаш ном бўйича фақат RES далили бор ёки админ ўргатганлари (смета­чилар танлови
    фақат аниқ номда — бир-бирига ўхшаш номлар орқали тарқалиб кетмасин)."""
    try:
        rows = list(LMEntry.objects.exclude(category="").only("key", "name", "code", "category", "locked", "votes"))
    except Exception:  # noqa: BLE001 — жадвал ҳали йўқ (миграция)
        return None
    if not rows:
        return None
    return LMBase({"key": e.key, "name": e.name, "code": e.code, "category": e.category,
                   "reason": _reason(e), "strong": _strong(e),
                   "fuzzy": e.locked or any(not k.startswith("user_") for k in (e.votes or {}))} for e in rows)


# ===================== админ ўргатиши =====================
def teach(name, category, unit="", code="", origin=LMEntry.Origin.ADMIN, note="") -> Optional[LMEntry]:
    key = lm_key(name)
    if not key or category not in ("L", "M", "K"):
        return None
    e, _ = LMEntry.objects.get_or_create(key=key, defaults={"name": str(name).strip()[:500]})
    e.category, e.locked, e.origin = category, True, origin
    e.unit = (unit or e.unit or "")[:40]
    e.code = res_code(code)[:12] or e.code
    if note:
        e.note = note[:200]
    e.save()
    return e


def teach_many(items, origin=LMEntry.Origin.EXCEL, note="") -> dict:
    """[{name, unit, code, category}] → {"new", "changed", "same"}."""
    out = Counter()
    keys = {lm_key(i["name"]): i for i in items if lm_key(i["name"]) and i.get("category") in ("L", "M", "K")}
    have = {e.key: e for e in LMEntry.objects.filter(key__in=keys)}
    for key, i in keys.items():
        e = have.get(key)
        if e and e.locked and e.category == i["category"]:
            out["same"] += 1
            continue
        out["changed" if e else "new"] += 1
        teach(i["name"], i["category"], i.get("unit", ""), i.get("code", ""), origin, note)
    return dict(out)


def unlock(entry: LMEntry) -> None:
    """Админ ўргатганини бекор қилиш: тури яна овозлардан (овози бўлмаса — ўчади)."""
    entry.locked, entry.origin = False, LMEntry.Origin.AUTO
    entry.save(update_fields=["locked", "origin", "updated"])
    recount([entry.id])


# ===================== Excel'дан ўқиш =====================
_UNIT_RE = re.compile(r"^(шт|компл|комплект|к-т|м|м2|м3|т|кг|км|пм|п\.м|набор|ед|пара|л|упак|рулон|100\s*м|1000\s*м)\.?$", re.I)
_SKIP_RE = re.compile(r"^(итого|всего|жами|наименование|номи|№|п/п|раздел|глава|бўлим)", re.I)
_HEAD_NAME = re.compile(r"наименован|^номи|ресурс\s*номи|^название", re.I)
_HEAD_UNIT = re.compile(r"^(ед|бирлиг|изм|ўлчов)", re.I)
_HEAD_CODE = re.compile(r"^(код|шифр|ресурс\s*коди|коди)", re.I)
_CODE_RE = re.compile(r"0*\d{2,6}")
_LETTERS = re.compile(r"[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ]{3}")


def _header(row) -> Optional[dict]:
    """Сарлавҳа қатори: {"name": устун, "unit": устун|None, "code": устун|None} ёки None."""
    cols = {"name": None, "unit": None, "code": None}
    for i, v in enumerate(row):
        t = str(v or "").strip()
        if not t or len(t) > 40:
            continue
        for k, rx in (("name", _HEAD_NAME), ("unit", _HEAD_UNIT), ("code", _HEAD_CODE)):
            if cols[k] is None and rx.search(t):
                cols[k] = i
                break
    return cols if cols["name"] is not None else None


def _cell(row, i) -> str:
    return str(row[i]).strip() if i is not None and i < len(row) and row[i] is not None else ""


def parse_excel(path, category: str, soffice=None) -> List[dict]:
    """Excel'даги ресурс номлари. ``category``: L / M / K — ҳаммаси шу турга; ``"res"`` — RES файлнинг
    бўлимлари бўйича («ОБОРУДОВАНИЕ» → M, «КАБЕЛЬ…» → L, бошқаси → K). [{name, unit, code, category}].

    Оддий Excel'да сарлавҳа қатори («Наименование», «Ед. изм.», «Код») қидирилади: ном, бирлик ва код шу
    устунлардан. Сарлавҳа йўқ бўлса — қатордаги энг узун матн ном, код эса олинмайди (миқдор «100» код бўлиб
    қолмасин: базада код бўйича қидириш биринчи туради)."""
    from core import convert
    path = Path(path)
    if category == "res":
        from .resfile import parse
        rf = parse(path, soffice=soffice)
        seen, out = set(), []
        for r in rf.rows:
            cat = r.category if r.category in ("L", "M") else "K"
            k = lm_key(r.name)
            if k and k not in seen:
                seen.add(k)
                out.append({"name": r.name, "unit": r.unit, "code": r.code, "category": cat})
        return out
    import tempfile
    from openpyxl import load_workbook
    tmp = tempfile.TemporaryDirectory(prefix="lm_xl_")
    xlsx = convert.ensure_xlsx(path, Path(tmp.name), soffice=soffice)
    wb = load_workbook(xlsx, read_only=True, data_only=True)
    seen, out = set(), []
    try:
        for ws in wb.worksheets:
            head = None
            for n, row in enumerate(ws.iter_rows(values_only=True)):
                row = list(row)
                if head is None and n < 40:
                    head = _header(row)
                    if head:
                        continue
                if head:
                    name = _cell(row, head["name"])
                    unit = _cell(row, head["unit"])
                    code = _cell(row, head["code"])
                    code = code if _CODE_RE.fullmatch(code) else ""
                else:
                    cells = [str(v).strip() for v in row if v is not None and str(v).strip()]
                    texts = [c for c in cells if _LETTERS.search(c)]
                    name = max(texts, key=len) if texts else ""
                    unit = next((c for c in cells if c != name and _UNIT_RE.match(c)), "")
                    code = ""
                if len(name) < 4 or not _LETTERS.search(name) or _SKIP_RE.match(name) or _UNIT_RE.match(name):
                    continue
                k = lm_key(name)
                if k and k not in seen:
                    seen.add(k)
                    out.append({"name": name[:500], "unit": unit[:40], "code": code, "category": category})
    finally:
        wb.close()
        tmp.cleanup()
    return out


def stats() -> dict:
    c = Counter(LMEntry.objects.values_list("category", "locked"))
    by = Counter()
    for (cat, locked), n in c.items():
        by[cat or "?"] += n
        by["locked" if locked else "auto"] += n
    return {"M": by["M"], "L": by["L"], "K": by["K"], "unclear": by["?"], "locked": by["locked"], "auto": by["auto"],
            "total": sum(c.values())}


def needs_confirm(e: LMEntry) -> bool:
    """Фақат смета­чи(лар) танлови, RES далили йўқ — тури админ тасдиқлагунча белгиланмайди."""
    v = e.votes or {}
    return not e.locked and not e.category and bool(v) and all(k.startswith("user_") for k in v)


def conflicts_q() -> Q:
    """Овозлари бир-бирига қарши (аниқ эмас ёки озчилик қарши) — админ кўриб чиқсин."""
    return Q(category="", locked=False)

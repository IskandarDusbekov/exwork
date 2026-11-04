"""Bazaga yig'ish (narxlangan smetadan kod + narx), median narxlar va statistika.

Faqat ishonchli qatorlar olinadi: holati EXACT (RES bilan aniq moslangan) yoki smetachi qo'lda qo'ygan narx
(``manual`` belgisi bilan, medianaga kirmaydi). Shubhali moslik, «ТОПИЛМАДИ», nol narx va kodsiz qatorlar olinmaydi."""
import logging
import re
import uuid
from collections import Counter, defaultdict
from types import SimpleNamespace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.normalize import norm_code, norm_name, unit_key  # noqa: F401 — panel ham shu yerdan oladi
from jobs.models import Batch, Job, Project

from . import normbook, resfile
from .models import Observation, Resource, ResourceName, Source, WorkItem, WorkResource, counted_q
from .regions import guess_region

log = logging.getLogger(__name__)

_CACHE_VER = "resbase:ver"
SKIP_LABELS = {
    "no_code": "кодсиз (маҳаллий материал, иш сарлавҳаси)",
    "not_exact": "аниқ мослашмаган (тахминий, топилмади)",
    "zero_price": "нарх йўқ ёки 0",
    "duplicate": "шу сметада такрор",
    "code_conflict": "бир кодга ҳар хил ресурс (номи ва нархи бошқа)",
}


def bump():
    """Bazada nimadir o'zgardi — median keshi yangilanadi."""
    try:
        cache.incr(_CACHE_VER)
    except ValueError:
        cache.set(_CACHE_VER, 2, None)


def _ver():
    return cache.get_or_set(_CACHE_VER, 1, None)


# ===================== ish (papka / obyekt / smeta) =====================
def work_of(kind, work_id):
    model = {"folder": Project, "object": Batch, "smeta": Job}[kind]
    return model.objects.get(pk=work_id)


def jobs_of(kind, work) -> list:
    if kind == "folder":
        qs = Job.objects.filter(batch__project=work)
    elif kind == "object":
        qs = Job.objects.filter(batch=work)
    else:
        qs = Job.objects.filter(pk=work.pk)
    return list(qs.filter(status=Job.Status.DONE))


def title_of(kind, work) -> str:
    return work.local_name if kind == "smeta" else work.name


def region_suggestion(kind, work) -> tuple:
    """Viloyat taxmini: ish nomi, papkalar va bir nechta smetaning sarlavha/svodka matni."""
    texts = [title_of(kind, work)]
    if kind == "folder":
        texts += [f.get("path", "") for f in work.files[:400]]
    jobs = jobs_of(kind, work)[:4]
    for j in jobs:
        texts += [j.local_name, j.batch_label]
        texts += _head_texts(j)
    return guess_region(texts)


def _head_texts(job, rows=14) -> list:
    """Lokal faylning hamma listlari boshidagi matn (obyekt nomi, «… туманида жойлашган»)."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(job.abs(job.local_path), read_only=True, data_only=True, keep_links=False)
    except Exception:  # noqa: BLE001 — fayl o'chirilgan bo'lishi mumkin
        return []
    out = []
    try:
        for ws in wb.worksheets[:6]:
            for row in ws.iter_rows(max_row=rows, values_only=True):
                out += [str(v) for v in row if isinstance(v, str)]
    finally:
        wb.close()
    return out


# ===================== yig'ish =====================
def _num(v):
    try:
        d = Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d.is_finite() else None


def rows_from_job(job) -> tuple:
    """(olinadigan [{code, name, unit, price, manual, row, category}], tashlab ketilganlar Counter, jami resurs qatori)."""
    taken, cand, skipped, seen, total = [], [], Counter(), set(), 0
    for r in (job.result or {}).get("rows") or []:
        status = r.get("status")
        if status in ("WORK_HEADER",) or not r.get("unit"):
            continue
        total += 1
        code = norm_code(r.get("code"))
        if not code:
            skipped["no_code"] += 1
            continue
        manual = bool(r.get("manual")) or status == "MANUAL"
        if status != "EXACT" and not manual:
            skipped["not_exact"] += 1
            continue
        price = _num(r.get("g"))
        if price is None or price <= 0:
            skipped["zero_price"] += 1
            continue
        cand.append({"code": code, "name": str(r.get("name") or "").strip()[:500], "unit": str(r.get("unit")).strip()[:40],
                     "price": price.quantize(Decimal("0.0001")), "manual": manual, "row": int(r.get("row") or 0),
                     "category": (r.get("default_category") or r.get("category") or "")[:1]})
    bad = resfile.conflict_codes((c["code"], c["name"], c["price"]) for c in cand)
    for c in cand:
        if c["code"] in bad:
            skipped["code_conflict"] += 1
        elif c["code"] in seen:
            skipped["duplicate"] += 1
        else:
            seen.add(c["code"])
            taken.append(c)
    return taken, skipped, total


def _store(source: Source, job_id, taken: list) -> int:
    """Olingan qatorlarni manbaga kuzatuv sifatida yozadi (yangi kod — yangi resurs); nom variantlari sanaladi."""
    codes = {t["code"] for t in taken}
    resources = {r.code: r for r in Resource.objects.filter(code__in=codes)}
    names, obs = Counter(), []
    for t in taken:
        res = resources.get(t["code"])
        if res is None:
            res = resources[t["code"]] = Resource.objects.create(
                code=t["code"], name=t["name"], unit=t["unit"], unit_key=unit_key(t["unit"]), category=t["category"])
        elif not res.category and t["category"] and not res.edited:
            res.category = t["category"]
            res.save(update_fields=["category", "updated"])
        obs.append(Observation(source=source, resource=res, job_id=job_id, row=t["row"], name=t["name"],
                               unit=t["unit"], price=t["price"], manual=t["manual"]))
        names[(res.pk, t["name"], t["unit"])] += 1
    Observation.objects.bulk_create(obs, batch_size=1000)
    for (rid, name, unit), n in names.items():
        obj, created = ResourceName.objects.get_or_create(resource_id=rid, name=name, unit=unit, defaults={"seen": n})
        if not created:
            obj.seen += n
            obj.save(update_fields=["seen"])
    return len(obs)


@transaction.atomic
def harvest(source: Source) -> Source:
    """Manbaning narxlarini (qayta) yig'adi: eski kuzatuvlar o'chiriladi, smetalardan (fayl — saqlangan RES
    nusxasidan) yangidan olinadi."""
    if source.is_file:
        return _harvest_file(source)
    work = work_of(source.kind, source.work_id)
    touched = set(source.observations.values_list("resource_id", flat=True))
    source.observations.all().delete()
    skipped, rows, taken_n, jobs = Counter(), 0, 0, jobs_of(source.kind, work)
    works, detail = [], []
    for job in jobs:
        taken, sk, total = rows_from_job(job)
        skipped.update(sk)
        rows += total
        n = _store(source, job.id, taken)
        taken_n += n
        jw = works_from_job(job)
        works += jw
        detail.append({"job": str(job.id), "local": job.local_name, "res": job.res_name, "rows": total, "taken": n,
                       "works": len(jw), "skipped": dict(sk)})
    n_works = _store_works(source, SimpleNamespace(works=works))
    source.title = title_of(source.kind, work)[:500]
    source.owner_id = work.owner_id
    source.stats = {"jobs": len(jobs), "rows": rows, "taken": taken_n, "skipped": dict(skipped), "works": n_works,
                    "books": dict(Counter(w.book for w in works)), "detail": detail}
    source.harvested = timezone.now()
    source.save()
    relink_books([source])
    recheck(touched | set(source.observations.values_list("resource_id", flat=True)))
    transaction.on_commit(bump)
    log.info("resbase: %s «%s» — %s ta narx, %s ta ish (%s qator)", source.kind, source.title, taken_n, n_works, rows)
    return source


def works_from_job(job) -> list:
    """Нархланган смета натижасидан ишлар: иш қатори (WORK_HEADER, коди — норма шифри) ва остидаги ресурслар
    («1.1», «1.2» — нормаси ва жами миқдори). Иш ортидаги рақамли қатор (масалан «3 ПЕСЧАНО-ГРАВИЙНАЯ СМЕСЬ») —
    шу ишнинг қўшимча материали."""
    out, work = [], None
    for r in (job.result or {}).get("rows") or []:
        code, name, unit = r.get("code"), str(r.get("name") or "").strip(), str(r.get("unit") or "").strip()
        if r.get("status") == "WORK_HEADER":
            shifr = normbook.parse(code)
            work = None
            if shifr and name:
                work = resfile.Work(job.local_name[:60], int(r.get("row") or 0), shifr[0], str(code)[:120], shifr[1],
                                    name[:500], unit[:40], _float(r.get("qty_unit") if r.get("qty") is None else r.get("qty")))
                out.append(work)
            continue
        if work is None or not name or not unit:
            if not unit:
                work = None  # бўлим сарлавҳаси
            continue
        sub = "." in str(r.get("num") or "")
        work.resources.append(resfile.WorkRes(int(r.get("row") or 0), norm_code(code), str(code or "")[:40], name[:500],
                                              unit[:40], _float(r.get("qty_unit")) if sub else None,
                                              _float(r.get("qty_total") if sub else r.get("qty")), extra=not sub))
    return out


def _float(v):
    n = _num(v)
    return float(n) if n is not None else None


# ===================== RES файлдан юклаш =====================
UPLOAD_DIR = "resbase"  # MEDIA_ROOT ichida: yuklangan RES nusxalari
UPLOAD_EXTS = (".xls", ".xlsx", ".xlsm")
FILE_SKIP_LABELS = {**resfile.SKIP_LABELS, "no_code": SKIP_LABELS["no_code"]}


def rows_from_file(parsed) -> tuple:
    """``resfile.ResFile`` dan bazaga olinadiganlar (smetadagi kabi: faqat kodli) va tashlab ketilganlar."""
    skipped = Counter(parsed.skipped)
    taken = []
    for r in parsed.rows:
        if not r.code:  # kodsiz material — keyingi bosqichda (nom bo'yicha moslash)
            skipped["no_code"] += 1
            continue
        taken.append({"code": r.code, "name": r.name, "unit": r.unit, "price": Decimal(str(r.price)).quantize(Decimal("0.0001")),
                      "manual": False, "row": r.row, "category": r.category})
    return taken, skipped


def file_duplicates(file_hash, fingerprint, region, year, quarter, exclude=None):
    """(shu davrdagi takrorlar, boshqa davrlardagilar): bayt xeshi yoki mazmun izi bir xil «Файл» manbalari."""
    qs = Source.objects.filter(kind=Source.Kind.FILE).filter(
        Q(file_hash=file_hash) | (Q(fingerprint=fingerprint) & ~Q(fingerprint="")))
    if exclude:
        qs = qs.exclude(pk=exclude)
    same = [s for s in qs if (s.region, s.year, s.quarter) == (region, year, quarter)]
    other = [s for s in qs if (s.region, s.year, s.quarter) != (region, year, quarter)]
    return same, other


def _media(rel) -> Path:
    from django.conf import settings
    return Path(settings.MEDIA_ROOT) / rel


_PAIR = re.compile(r"^b[pv](\d+)$", re.I)


def pair_key(title, region, year, quarter) -> str:
    """«bp1010.xlsx» (narxlar) va «bv1010.xlsx» (ishlar) — bitta smeta: shu davrda raqami bir xil fayllar juft."""
    m = _PAIR.match(Path(str(title)).stem.strip())
    return f"{region}:{year}:{quarter}:{m.group(1)}" if m and region and year and quarter else ""


def _group(source):
    """Manba va uning jufti (bp↔bv). Juft yo'q — faqat o'zi."""
    if source.pair_key:
        return Source.objects.filter(pair_key=source.pair_key)
    return Source.objects.filter(pk=source.pk)


def relink_books(sources):
    """Narx kuzatuvlarining иш турлари (``Observation.books``): shu fayl va jufti ЛРВ'sida kod qaysi to'plam
    ishlarida uchragan. Fayl qo'shilganda, almashtirilganda, o'chirilganda yoki davri o'zgarganda qayta hisoblanadi."""
    done = set()
    for s in sources:
        group = list(_group(s).values_list("pk", flat=True)) or [s.pk]
        if done & set(group):
            continue
        done |= set(group)
        books = defaultdict(set)
        for code, book in (WorkResource.objects.filter(work__source_id__in=group).exclude(code="")
                           .values_list("code", "work__book")):
            books[code].add(book)
        changed = []
        for o in Observation.objects.filter(source_id__in=group).select_related("resource"):
            b = books.get(o.resource.code)
            new = "," + ",".join(sorted(b, key=normbook.book_sort)) + "," if b else ""
            if o.books != new:
                o.books = new
                changed.append(o)
        Observation.objects.bulk_update(changed, ["books"], batch_size=1000)


def _dec(v):
    return None if v is None else Decimal(str(round(v, 6)))


def _store_works(source, parsed) -> int:
    """ЛРВ ishlari va ularning resurslari (eskilari o'chiriladi — qayta yig'ishda takrorlanmasin)."""
    source.works.all().delete()
    items = [WorkItem(source=source, sheet=w.sheet[:60], row=w.row, shifr=w.shifr[:40], raw=w.raw[:120], book=w.book,
                      name=w.name[:500], unit=w.unit[:60], qty=_dec(w.qty), section=w.section[:300]) for w in parsed.works]
    WorkItem.objects.bulk_create(items, batch_size=1000)
    WorkResource.objects.bulk_create(
        [WorkResource(work=item, row=x.row, code=x.code, raw_code=x.raw_code[:40], name=x.name[:500], unit=x.unit[:40],
                      norma=_dec(x.norma), qty=_dec(x.qty), extra=x.extra)
         for item, w in zip(items, parsed.works) for x in w.resources], batch_size=1000)
    return len(items)


@transaction.atomic
def add_file(rel_path, title, parsed, *, region, year, quarter, file_hash, user=None, replace=()) -> Source:
    """Saqlangan fayldan (RES narxlari va/yoki ЛРВ ishlari) yangi manba. ``replace`` — o'rniga keladigan
    (o'chiriladigan) manbalar."""
    for old in replace:
        delete_source(old, keep_file=old.file_path == rel_path)
    source = Source.objects.create(kind=Source.Kind.FILE, work_id=uuid.uuid4(), title=title[:500], owner=user,
                                   added_by=user, region=region, year=year, quarter=quarter, file_path=rel_path,
                                   file_hash=file_hash, fingerprint=parsed.fingerprint,
                                   pair_key=pair_key(title, region, year, quarter))
    return _fill_file(source, parsed)


def _fill_file(source, parsed) -> Source:
    taken, skipped = rows_from_file(parsed)
    n = _store(source, source.work_id, taken)
    n_works = _store_works(source, parsed)
    source.stats = {"rows": len(parsed.rows) + sum(parsed.skipped.values()), "taken": n, "skipped": dict(skipped),
                    "sheets": parsed.sheets, "lrv_sheets": parsed.lrv_sheets, "works": n_works,
                    "books": dict(Counter(w.book for w in parsed.works))}
    source.harvested = timezone.now()
    source.save()
    relink_books([source])
    recheck(set(source.observations.values_list("resource_id", flat=True)))
    transaction.on_commit(bump)
    log.info("resbase: fayl «%s» — %s ta narx, %s ta ish", source.title, n, n_works)
    return source


@transaction.atomic
def set_period(source, region, year, quarter):
    """Manba davri o'zgaradi; fayl uchun jufti (bp↔bv) ham shu davr bo'yicha qayta topiladi."""
    old = list(_group(source).exclude(pk=source.pk))
    source.region, source.year, source.quarter = region, year, quarter
    if source.is_file:
        source.pair_key = pair_key(source.title, region, year, quarter)
    source.save(update_fields=["region", "year", "quarter", "pair_key"])
    relink_books([source, *old])
    transaction.on_commit(bump)


def explore(f: dict):
    """«Маълумотлар» саҳифаси: фильтрланган кузатувлар ва ресурс тури таблари учун сонлар.

    ``f`` — {region, year, quarter, cat, book, src, q, sort}; бўш қиймат — фильтр йўқ; book «-» — иш тури
    аниқланмаган. Қайтаради: (qs, {"cat": {I: n}, "book": {Е01: n}}) — ҳар таб сонлари ўз фильтрисиз."""
    from django.db.models import Count
    base = Observation.objects.all()
    if f.get("region"):
        base = base.filter(source__region=f["region"])
    if f.get("year"):
        base = base.filter(source__year=f["year"])
    if f.get("quarter"):
        base = base.filter(source__quarter=f["quarter"])
    if f.get("src"):
        base = base.filter(source_id=f["src"])
    q = (f.get("q") or "").strip()
    if q:
        code = norm_code(q)
        text = Q()
        for v in {q, q.upper(), q.lower(), q.capitalize()}:  # SQLite кирилл ҳарфларида катта-кичикни фарқлайди
            text |= Q(name__icontains=v)
        base = base.filter(Q(resource__code=code) | text if code else text)
    cat, book = f.get("cat"), f.get("book")
    cat_q = Q(resource__category=cat) if cat else Q()
    book_q = (Q(books="") if book == "-" else Q(books__contains=f",{book},")) if book else Q()
    books = Counter()
    for b in base.filter(cat_q).values_list("books", flat=True):
        books.update([x for x in b.split(",") if x] or ["-"])
    counts = {"cat": dict(base.filter(book_q).values_list("resource__category").annotate(n=Count("id")).order_by()),
              "book": books}
    order = {"code": ("resource__code",), "name": ("name",), "price": ("price",), "-price": ("-price",),
             "new": ("-source__created", "row")}.get(f.get("sort") or "", ("resource__category", "row"))
    qs = base.filter(cat_q & book_q).select_related("resource", "source").order_by(*order, "pk")
    return qs, counts


def resource_rows(f: dict):
    """«Ресурс нархлари»: битта ресурс (код) — битта қатор. Танланган вилоят/давр/файл бўйича шу коднинг нархлари
    бирлаштирилади: ўрта нарх (медиана), энг арзони ва энг қиммати, нечта манбадан. Қўлда қўйилган, истисно
    қилинган ва текширувдаги нархлар ўрта нархга кирмайди (лекин сони кўрсатилади).

    Қайтаради: (қаторлар, {"cat": {I: n}, "book": {Е01: n}}) — сонлар ресурс бўйича, ҳар таб ўз фильтрисиз."""
    qs, _ = explore({**f, "cat": "", "book": "", "sort": ""})
    agg = {}
    for code, name, unit, cat, price, manual, excluded, review, books, sid in qs.values_list(
            "resource__code", "resource__name", "resource__unit", "resource__category", "price", "manual", "excluded",
            "review", "books", "job_id").order_by():
        a = agg.get(code)
        if a is None:
            a = agg[code] = {"code": code, "name": name, "unit": unit, "category": cat, "prices": [], "pending": 0,
                             "other": 0, "books": set(), "sources": set()}
        if review == Observation.Review.PENDING:
            a["pending"] += 1
        elif manual or excluded:
            a["other"] += 1
        else:
            a["prices"].append(price)
        a["books"].update(b for b in books.split(",") if b)
        a["sources"].add(sid)
    rows = []
    for a in agg.values():
        ps = a.pop("prices")
        a.update(n=len(ps), n_src=len(a["sources"]), books=sorted(a["books"], key=normbook.book_sort),
                 median=Decimal(median(ps)).quantize(Decimal("0.01")) if ps else None,
                 lo=min(ps) if ps else None, hi=max(ps) if ps else None)
        rows.append(a)
    cat, book = f.get("cat"), f.get("book")

    def in_book(r):
        return not book or (not r["books"] if book == "-" else book in r["books"])

    counts = {"cat": Counter(r["category"] for r in rows if in_book(r)), "book": Counter()}
    for r in rows:
        if not cat or r["category"] == cat:
            counts["book"].update(r["books"] or ["-"])
    rows = [r for r in rows if (not cat or r["category"] == cat) and in_book(r)]
    code_key = lambda r: (len(r["code"]), r["code"])  # noqa: E731 — «258» < «1360»
    sort = f.get("sort") or ""
    if sort == "name":
        rows.sort(key=lambda r: r["name"])
    elif sort in ("price", "-price"):
        rows.sort(key=lambda r: (r["median"] is None, r["median"] or 0), reverse=False)
        if sort == "-price":
            rows.sort(key=lambda r: (r["median"] is not None, r["median"] or 0), reverse=True)
    elif sort == "n":
        rows.sort(key=lambda r: (-r["n"], code_key(r)))
    elif sort == "code":
        rows.sort(key=code_key)
    else:
        rows.sort(key=lambda r: (r["category"] or "Z", code_key(r)))
    return rows, counts


# ===================== нормалар (ЛРВ ишлари) =====================
def _works(f: dict):
    qs = WorkItem.objects.all()
    if f.get("region"):
        qs = qs.filter(source__region=f["region"])
    if f.get("year"):
        qs = qs.filter(source__year=f["year"])
    if f.get("quarter"):
        qs = qs.filter(source__quarter=f["quarter"])
    if f.get("src"):
        qs = qs.filter(source_id=f["src"])
    return qs


def norms(f: dict):
    """«Нормалар»: шифр бўйича битта қатор (нечта файлда, неча марта ишлатилган) ва тўпламлар сони.
    Бир хил шифр бир неча файлда (ёки бир файлда бир неча марта) учраса — бир қатор, сонлари билан."""
    from django.db.models import Count, Min
    qs = _works(f)
    q = (f.get("q") or "").strip()
    if q:
        text = Q(shifr__icontains=q.upper())
        for v in {q, q.upper(), q.lower(), q.capitalize()}:
            text |= Q(name__icontains=v)
        qs = qs.filter(text)
    books = dict(qs.values_list("book").annotate(n=Count("shifr", distinct=True)).order_by())
    if f.get("book"):
        qs = qs.filter(book=f["book"])
    rows = (qs.values("shifr", "book").annotate(n_files=Count("source", distinct=True), n_uses=Count("id"),
                                                 name=Min("name"), unit=Min("unit"))
            .order_by("shifr"))
    return rows, books


def book_names() -> dict:
    """Админ ёзган тўплам номлари {«Е01»: ном} (кеш — база ўзгарганда янгиланади)."""
    from .models import BookName
    key = f"resbase:books:{_ver()}"
    names = cache.get(key)
    if names is None:
        names = dict(BookName.objects.values_list("key", "name"))
        cache.set(key, names, 3600)
    return names


def book_label(key: str) -> str:
    """Тўплам номи — фақат админ ёзгани; ёзилмаган бўлса бўш (кодининг ўзи кўрсатилади)."""
    return book_names().get(key, "")


@transaction.atomic
def save_book_names(names: dict, user=None) -> int:
    """{калит: ном} — бўш ном = стандартга қайтиш (админ ёзгани ўчади). Ўзгарганлар сони."""
    from .models import BookName
    current = dict(BookName.objects.values_list("key", "name"))
    changed = 0
    for key, name in names.items():
        name = " ".join(str(name or "").split())[:200]
        if not name:
            changed += BookName.objects.filter(key=key).delete()[0]
        elif current.get(key) != name:
            BookName.objects.update_or_create(key=key, defaults={"name": name, "updated_by": user})
            changed += 1
    if changed:
        bump()  # шу сўровнинг ўзида ҳам янги ном кўринсин
        transaction.on_commit(bump)  # бошқа сўров commit'дан олдин эски номни кешлаган бўлса
    return changed


def obs_shifrs(objs) -> dict:
    """{кузатув pk: [шифр, …]} — нархнинг коди шу манба (ёки жуфти bp↔bv) ишларида қайси шифрларда ишлатилган."""
    objs = list(objs)
    src_ids = {o.source_id for o in objs}
    pair = dict(Source.objects.filter(pk__in=src_ids).values_list("pk", "pair_key"))
    by_key = defaultdict(set)
    for pk, key in Source.objects.filter(pair_key__in={k for k in pair.values() if k}).values_list("pk", "pair_key"):
        by_key[key].add(pk)
    groups = {sid: by_key.get(pair.get(sid)) or {sid} for sid in src_ids}
    found = defaultdict(set)
    for sid, code, shifr in (WorkResource.objects.filter(work__source_id__in=set().union(*groups.values()) if groups else [],
                                                          code__in={o.resource.code for o in objs}, extra=False)
                             .values_list("work__source_id", "code", "work__shifr").order_by().distinct()):
        found[(sid, code)].add(shifr)
    return {o.pk: sorted(set().union(*(found.get((sid, o.resource.code), set()) for sid in groups[o.source_id])))
            for o in objs}


def resource_works(code: str, limit: int = 200) -> list:
    """Ресурс саҳифаси учун: бу код қайси ишларда (шифрларда) ишлатилган — нормаси (мин–макс) ва нечта файлда."""
    from django.db.models import Count, Max, Min
    rows = (WorkResource.objects.filter(code=code, extra=False)
            .values("work__shifr", "work__book")
            .annotate(name=Min("work__name"), unit=Min("work__unit"), n_files=Count("work__source", distinct=True),
                      lo=Min("norma"), hi=Max("norma"))
            .order_by("work__book", "work__shifr")[:limit])
    return [{**r, "label": book_label(r["work__book"])} for r in rows]


def norm_detail(shifr: str, f: dict) -> dict:
    """Битта норма: номи, бирлиги ва таркиби — файллардан йиғилган. Такрорлар:
    * бир хил ресурс бир хил норма билан бир неча файлда — бир қатор, «N файлда»;
    * нормаси фарқ қилса — вариантлар (энг кўп учрагани асосий, қолганлари «бошқача норма» деб кўрсатилади);
    * нормада йўқ, иш ортидан ёзилган материаллар — алоҳида рўйхат (нормаси йўқ, фақат миқдори)."""
    works = list(_works(f).filter(shifr=shifr).select_related("source"))
    if not works:
        return {}
    names, units = Counter(w.name for w in works), Counter(w.unit for w in works)
    n_files = len({w.source_id for w in works})
    comp = defaultdict(lambda: {"variants": defaultdict(set), "names": Counter(), "unit": Counter(), "code": ""})
    extras = defaultdict(lambda: {"files": set(), "qty": [], "name": "", "unit": "", "code": ""})
    for x in WorkResource.objects.filter(work__in=works).select_related("work"):
        key = x.code or resfile.name_key(x.name, x.unit)
        if x.extra:
            e = extras[key]
            e["files"].add(x.work.source_id)
            e["qty"].append(x.qty)
            e.update(name=e["name"] or x.name, unit=e["unit"] or x.unit, code=x.code)
            continue
        c = comp[key]
        c["variants"][x.norma.normalize() if x.norma is not None else None].add(x.work.source_id)
        c["names"][x.name] += 1
        c["unit"][x.unit] += 1
        c["code"] = x.code
    rows = []
    for key, c in comp.items():
        vs = sorted(((n, len(s)) for n, s in c["variants"].items()), key=lambda v: -v[1])
        rows.append({"code": c["code"], "name": c["names"].most_common(1)[0][0], "unit": c["unit"].most_common(1)[0][0],
                     "norma": vs[0][0], "n": vs[0][1], "others": vs[1:]})
    rows.sort(key=lambda r: (r["code"] == "", int(r["code"]) if r["code"].isdigit() else 0, r["name"]))
    return {"shifr": shifr, "book": works[0].book, "name": names.most_common(1)[0][0],
            "names": [n for n, _ in names.most_common()[1:4]], "unit": units.most_common(1)[0][0],
            "n_files": n_files, "n_uses": len(works), "rows": rows, "n_variants": sum(1 for r in rows if r["others"]),
            "extras": sorted(({**e, "files": len(e["files"])} for e in extras.values()), key=lambda e: -e["files"]),
            "uses": works[:200]}


def explore_tree(sel: dict) -> list:
    """Чап дарахт: вилоят → давр → манбалар (кузатувлар сони билан). Манбалар фақат очилган давр учун."""
    from django.db.models import Count
    # сонлар — ресурс (код) бўйича: бир код бир неча сметада учраса ҳам бир марта
    n_obs = dict(((r, y, q), n) for r, y, q, n in Observation.objects.values_list(
        "source__region", "source__year", "source__quarter").annotate(n=Count("resource", distinct=True)).order_by())
    n_reg = dict(Observation.objects.values_list("source__region").annotate(n=Count("resource", distinct=True)).order_by())
    regions = defaultdict(lambda: {"n": 0, "periods": []})
    for r, y, q in Source.objects.values_list("region", "year", "quarter").distinct().order_by():
        n = n_obs.get((r, y, q), 0)
        regions[r]["n"] = n_reg.get(r, 0)
        regions[r]["periods"].append({"year": y, "quarter": q, "n": n})
    out = []
    from .regions import REGIONS
    order = [k for k, _ in REGIONS]
    for r in sorted(regions, key=lambda k: order.index(k) if k in order else 99):
        node = regions[r]
        node["periods"].sort(key=lambda p: (-(p["year"] or 0), -(p["quarter"] or 0)))
        open_ = sel.get("region") == r
        for p in node["periods"]:
            p["open"] = open_ and (sel.get("year"), sel.get("quarter")) == (p["year"], p["quarter"])
            if p["open"]:
                p["sources"] = list(Source.objects.filter(region=r, year=p["year"], quarter=p["quarter"])
                                    .annotate(n=Count("observations", distinct=True), w=Count("works", distinct=True))
                                    .order_by("title"))
        out.append({"region": r, "n": node["n"], "open": open_, "periods": node["periods"]})
    return out


def _harvest_file(source) -> Source:
    touched = set(source.observations.values_list("resource_id", flat=True))
    source.observations.all().delete()
    parsed = resfile.parse(_media(source.file_path))
    source.fingerprint = parsed.fingerprint
    source = _fill_file(source, parsed)
    recheck(touched)
    return source


def add_source(kind, work_id, *, region="", year=None, quarter=None, user=None) -> Source:
    work = work_of(kind, work_id)
    hint_region, hint_word = region_suggestion(kind, work) if not region else (region, "")
    source, _ = Source.objects.update_or_create(kind=kind, work_id=work.pk, defaults={
        "title": title_of(kind, work)[:500], "owner_id": work.owner_id, "region": region or hint_region,
        "region_hint": hint_word, "year": year, "quarter": quarter, "added_by": user})
    return harvest(source)


def delete_source(source: Source, keep_file=False):
    touched = set(source.observations.values_list("resource_id", flat=True))
    pair = list(_group(source).exclude(pk=source.pk))  # jufti qoladi — uning иш турлари qayta hisoblanadi
    if source.is_file and source.file_path and not keep_file:
        _media(source.file_path).unlink(missing_ok=True)
    source.delete()
    relink_books(pair)
    recheck(touched)
    Resource.objects.filter(observations__isnull=True).delete()  # hech qayerda uchramaydigan kodlar qolmasin
    bump()


def wipe_all():
    Observation.objects.all().delete()
    Source.objects.all().delete()
    Resource.objects.all().delete()
    bump()


# ===================== narxlar =====================
def price_table(region, year, quarter) -> dict:
    """{kod: (mediana Decimal, kuzatuvlar soni)} — faqat shu viloyat va davr; qo'lda va istisno qilinganlar kirmaydi."""
    if not (region and year and quarter):
        return {}
    key = f"resbase:prices:{_ver()}:{region}:{year}:{quarter}"
    table = cache.get(key)
    if table is None:
        prices = defaultdict(list)
        qs = Observation.objects.filter(counted_q(), source__region=region, source__year=year,
                                        source__quarter=quarter).values_list("resource__code", "price")
        for code, price in qs:
            prices[code].append(price)
        table = {c: (Decimal(median(ps)).quantize(Decimal("0.01")), len(ps)) for c, ps in prices.items()}
        cache.set(key, table, 3600)
    return table


def resource_medians(resource) -> list:
    """Resurs sahifasi uchun: [{region, year, quarter, median, n, manual, min, max}] — viloyat va davr bo'yicha."""
    groups = defaultdict(list)
    for o in resource.observations.select_related("source").filter(excluded=False).exclude(review=Observation.Review.PENDING):
        s = o.source
        groups[(s.region, s.year, s.quarter)].append(o)
    out = []
    for (region, year, quarter), obs in groups.items():
        auto = [o.price for o in obs if not o.manual]
        out.append({"region": region, "year": year, "quarter": quarter, "n": len(auto), "manual": len(obs) - len(auto),
                    "median": Decimal(median(auto)).quantize(Decimal("0.01")) if auto else None,
                    "min": min(auto) if auto else None, "max": max(auto) if auto else None})
    out.sort(key=lambda g: (-(g["year"] or 0), -(g["quarter"] or 0), g["region"]))
    return out


def matrix():
    """Viloyat × davr: nechta kodning narxi bor (medianaga kiradigan kuzatuvlar bo'yicha)."""
    from django.db.models import Count
    rows = (Observation.objects.filter(counted_q())
            .values("source__region", "source__year", "source__quarter")
            .annotate(codes=Count("resource", distinct=True), obs=Count("id")))
    periods = sorted({(r["source__year"], r["source__quarter"]) for r in rows},
                     key=lambda p: (p[0] is None, -(p[0] or 0), -(p[1] or 0)))
    cells = {(r["source__region"], r["source__year"], r["source__quarter"]): r for r in rows}
    return periods, cells


# ===================== қамров =====================
COVER_KINDS = ("exact", "other", "none", "no_code")


def coverage(job, region, year, quarter, *, own=False) -> dict:
    """Baza bu smetani qanchalik narxlay oladi: har resurs qatori — shu viloyat/davr narxi bor (exact), faqat boshqa
    davr yoki viloyatda bor (other), kodi bor-u narxi yo'q (none), kodsiz (no_code). ``own=False`` — smetaning
    o'zidan yig'ilgan narxlar hisobga olinmaydi (aks holda o'z narxi bilan «qoplanib» qoladi)."""
    rows = [r for r in (job.result or {}).get("rows") or [] if r.get("status") != "WORK_HEADER" and r.get("unit")]
    codes = {norm_code(r.get("code")) for r in rows} - {""}
    cells = defaultdict(lambda: defaultdict(list))  # kod → (viloyat, yil, chorak) → narxlar
    qs = Observation.objects.filter(counted_q(), resource__code__in=codes)
    if not own:
        qs = qs.exclude(job_id=job.id)
    for code, r, y, q, price in qs.values_list("resource__code", "source__region", "source__year", "source__quarter",
                                                "price"):
        cells[code][(r, y, q)].append(price)
    want = (region, year, quarter) if region and year and quarter else None
    counts, missing, other, items = Counter(), {}, {}, []
    for r in rows:
        code = norm_code(r.get("code"))
        median_ = n = None
        if not code:
            kind = "no_code"
        elif want and want in cells.get(code, {}):
            ps = cells[code][want]
            kind, median_, n = "exact", Decimal(median(ps)).quantize(Decimal("0.01")), len(ps)
        elif cells.get(code):
            kind = "other"
            other.setdefault(code, {"code": code, "name": r.get("name") or "", "unit": r.get("unit"), "rows": 0,
                                    "where": sorted(cells[code], key=lambda c: (c[1] is None, -(c[1] or 0), -(c[2] or 0)))})
            other[code]["rows"] += 1
        else:
            kind = "none"
            missing.setdefault(code, {"code": code, "name": r.get("name") or "", "unit": r.get("unit"), "rows": 0})
            missing[code]["rows"] += 1
        counts[kind] += 1
        items.append({"row": r.get("row"), "code": code or str(r.get("code") or ""), "name": r.get("name") or "",
                      "unit": r.get("unit"), "kind": kind, "median": median_, "n": n, "price": _num(r.get("g"))})
    total = len(rows)
    return {"total": total, "counts": [(k, counts[k], round(100 * counts[k] / total) if total else 0) for k in COVER_KINDS],
            "missing": sorted(missing.values(), key=lambda m: (-m["rows"], int(m["code"]))),
            "other": sorted(other.values(), key=lambda m: (-m["rows"], int(m["code"]))), "items": items}


# ===================== нархлаш учун =====================
def period_complete(period) -> bool:
    return bool(period and period.get("region") and period.get("year") and period.get("quarter"))


def period_label(period) -> str:
    from .regions import REGION_LABELS
    if not period_complete(period):
        return ""
    return f"{REGION_LABELS.get(period['region'], period['region'])} · {period['year']}-{period['quarter']}"


def price_map(period) -> dict:
    """Narxlash uchun (``core.pipeline.Options.base_prices``): {kod: {price, n, unit_key, unit, category}} — faqat
    tanlangan viloyat va davr medianasi. Baza o'chirilgan yoki davr to'liq emas — {}."""
    from .models import BaseConfig
    if not period_complete(period) or not BaseConfig.get().enabled:
        return {}
    table = price_table(period["region"], int(period["year"]), int(period["quarter"]))
    meta = {c: (k, u, cat) for c, k, u, cat in
            Resource.objects.filter(code__in=table).values_list("code", "unit_key", "unit", "category")}
    return {code: {"price": float(price), "n": n, "unit_key": meta[code][0], "unit": meta[code][1],
                   "category": meta[code][2]} for code, (price, n) in table.items() if code in meta}


def parse_period(data) -> dict:
    """Формадан {region, year, quarter} — нотўғри ёки бўш қиймат = номаълум."""
    from .regions import REGION_LABELS
    region = data.get("region", "")
    try:
        year = int(data.get("year") or 0) or None
        quarter = int(data.get("quarter") or 0) or None
    except (TypeError, ValueError):
        year = quarter = None
    if year and not 2000 <= year <= 2100:
        year = None
    return {"region": region if region in REGION_LABELS else "", "year": year,
            "quarter": quarter if quarter in (1, 2, 3, 4) else None}


# ===================== текширув навбати =====================
PRICE_RATIO = 3  # medianadan shuncha baravardan ko'p farq — shubhali
NAME_MIN = resfile.NAME_MIN  # nom o'xshashligi shundan past — shubhali
name_similarity = resfile.name_similarity


def recheck(resource_ids=None):
    """Shubhali kuzatuvlarni qayta belgilaydi (``resource_ids`` — faqat shu resurslar, None — hammasi):
    * name — smetadagi nom resurs nomiga (va qabul qilingan nomlarga) o'xshamaydi;
    * unit — birlik boshqa (qabul qilingan birliklardan ham);
    * price — shu viloyat va davr narxlari medianasidan (3 tadan kam bo'lsa — hamma narxlar) 3 baravardan ko'p farq.
    Belgisi bor kuzatuv admin qaror qilguncha ``pending`` (medianaga kirmaydi); qabul/rad qarori saqlanadi."""
    R = Observation.Review
    qs = Observation.objects.select_related("source", "resource")
    if resource_ids is not None:
        qs = qs.filter(resource_id__in=list(resource_ids))
    by_res = defaultdict(list)
    for o in qs:
        by_res[o.resource_id].append(o)
    changed = []
    for obs in by_res.values():
        res = obs[0].resource
        ok_names = [res.name] + [o.name for o in obs if o.review == R.ACCEPTED]
        ok_units = {res.unit_key} | {unit_key(o.unit) for o in obs if o.review == R.ACCEPTED}
        flags = {}
        for o in obs:
            f = []
            if not o.manual:
                if max(name_similarity(o.name, n) for n in ok_names) < NAME_MIN:
                    f.append("name")
                if unit_key(o.unit) not in ok_units:
                    f.append("unit")
            flags[o.pk] = f
        # narx solishtiriladigan asos: ishonchli (nomi va birligi mos yoki qabul qilingan) kuzatuvlar
        base = [o for o in obs if not o.manual and not o.excluded and o.review != R.REJECTED
                and (not flags[o.pk] or o.review == R.ACCEPTED)]
        cells = defaultdict(list)
        for o in base:
            cells[(o.source.region, o.source.year, o.source.quarter)].append(o)
        for o in obs:
            if o.manual or o.excluded or o.price <= 0:
                continue
            # o'zi bilan birga medianasi (kamida 3 ta narx): 2 tadan qaysi biri xato ekanini bilib bo'lmaydi
            group = [x.price for x in cells[(o.source.region, o.source.year, o.source.quarter)] if x.pk != o.pk] + [o.price]
            if len(group) < 3:
                group = [x.price for x in base if x.pk != o.pk] + [o.price]
                if len(group) < 3:
                    continue
            m = median(group)
            if m > 0 and max(o.price / m, m / o.price) > PRICE_RATIO:
                flags[o.pk].append("price")
        for o in obs:
            new = ",".join(flags[o.pk])
            if o.review in (R.REJECTED, R.ACCEPTED):
                review = o.review  # admin qarori saqlanadi (qabul qilingan nom/birlik boshqalar uchun ham asos)
            else:
                review = R.PENDING if new else R.NONE
            if (o.flags, o.review) != (new, review):
                o.flags, o.review = new, review
                changed.append(o)
    Observation.objects.bulk_update(changed, ["flags", "review"], batch_size=500)
    if changed:
        transaction.on_commit(bump)
    return len(changed)


def decide(obs_ids, accept: bool) -> int:
    """Admin qarori: «қабул» — medianaga kiradi (shu nom va birlik keyin shubhali bo'lmaydi), «рад» — istisno."""
    R = Observation.Review
    qs = Observation.objects.filter(pk__in=list(obs_ids))
    rids = set(qs.values_list("resource_id", flat=True))
    n = qs.update(review=R.ACCEPTED, excluded=False) if accept else qs.update(review=R.REJECTED, excluded=True)
    recheck(rids)
    bump()
    return n

"""Texnazor sahifalari: obyektlar ro'yxati, obyekt qo'shish (ofyorta yuklash), obyekt — oylar va hajmlar.

Obyekt sahifasi og'ir emas: avval mundarija va jami summalar, butun ofyorta jadvali — alohida so'rov bilan
(bo'laklarda, brauzer faqat ko'rinib turganini chizadi). Hajm yozilsa faqat shu qator saqlanadi va jami summalar qaytariladi."""
import json

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import content_disposition_header
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import f2, f3, services, tasks
from . import calc
from .calc import STEPS
from .models import Correction, Item, MyOfyorta, Period, Section, SiteObject
from .ofyorta import CATEGORY_LABELS
from .templatetags import texnazor_tags as tn
from jobs.templatetags.narx import readable
from panel import conf
from panel.audit import log as _log
from panel.models import Event

SEARCH_LIMIT = 200


def audit(request, o, text: str):
    """Panel jurnaliga: texnazorda kim nima qildi (hajmlar har katakda emas — ``Done.updated`` dan ko'rinadi)."""
    _log(request, Event.Kind.TEXNAZOR, work=o, work_kind="texnazor", detail=f"{o.name[:200]}: {text}")


def _obj(request, obj_id) -> SiteObject:
    return get_object_or_404(SiteObject, pk=obj_id, owner=request.user)


def _period(obj, request, data=None) -> Period:
    pid = (data or {}).get("period") or request.GET.get("p") or request.POST.get("period")
    qs = obj.periods.all()
    if pid:
        return get_object_or_404(qs, pk=pid)
    p = qs.filter(closed=False).order_by("-year", "-month").first() or qs.order_by("-year", "-month").first()
    if p is None:
        raise Http404("Ой йўқ")
    return p


def index(request):
    return render(request, "texnazor/index.html", {"nav": "texnazor", "cards": services.object_cards(request.user),
                                                    "my_ofyorta": conf.get("my_ofyorta")})


def create(request):
    error = None
    on = conf.get("my_ofyorta")  # «Менинг офёрталарим» hozircha yashirin (panel → Созламалар)
    mine = list(MyOfyorta.objects.filter(owner=request.user, status=MyOfyorta.Status.READY)) if on else []
    if request.method == "POST":
        f = request.FILES.get("ofyorta")
        fields = {k: request.POST.get(k, "").strip() for k in ("name", "address", "customer", "contractor", "contract")}
        try:
            from jobs.services import UploadError, check_quota
            try:
                check_quota(request.user)  # soatiga yuklashlar va disk — narxlashdagi bilan bitta hisob
            except UploadError as e:
                raise services.TexnazorError(str(e))
            if on and request.POST.get("source") == "my":  # «Менинг офёрталарим» dan — bloklar tuzilishidan aniq
                doc = next((d for d in mine if str(d.pk) == request.POST.get("my")), None)
                if doc is None:
                    raise services.TexnazorError("Офёртани танланг")
                obj = services.create_from_my(request.user, doc, **fields)
            else:
                if not f:
                    raise services.TexnazorError("Офёрта файлини танланг")
                obj = services.create_object(request.user, f, **fields)
            tasks.start(obj.pk)
            _log(request, Event.Kind.UPLOAD, work=obj, work_kind="texnazor",
                 detail=f"{obj.name[:200]}: техназор объекти (" + ("«Менинг офёрталарим» дан" if obj.made_from_id
                                                                   else f"файл: {obj.source_name}") + ")")
            return redirect("texnazor:object", obj_id=obj.pk)
        except services.TexnazorError as e:
            error = str(e)
    data = request.POST if request.method == "POST" else {"source": "my" if on and request.GET.get("my") else "file",
                                                          "my": request.GET.get("my", "")}
    return render(request, "texnazor/create.html", {"nav": "texnazor", "error": error, "data": data, "mine": mine,
                                                    "my_ofyorta": on})


def state(request, obj_id):
    """O'qish jarayoni (sahifa so'rab turadi)."""
    o = _obj(request, obj_id)
    return JsonResponse({"status": o.status, "error": o.error, "progress": o.progress})


def detail(request, obj_id):
    o = _obj(request, obj_id)
    if o.status != SiteObject.Status.READY:
        return render(request, "texnazor/loading.html", {"nav": "texnazor", "o": o})
    periods = list(o.periods.all())
    period = None
    if periods:
        try:
            period = _period(o, request)
        except Http404:
            period = periods[-1]
    ctx = {"nav": "texnazor", "o": o, "periods": periods, "period": period, "categories": CATEGORY_LABELS,
           "next": services.next_month(o), "months": Period.MONTHS, "warnings": services.warnings(o),
           "corrections": _history(o), "info": services.f3_settings(o), "info_missing": services.f3_missing(o)}
    if period:
        totals = services.period_totals(period)
        ctx.update(totals=totals, tree=services.section_tree(o, services.section_stats(period)), pay=services.payments(o),
                   period_info=services.period_info(period), sel=_int(request.GET.get("s")), f2=services.month_f2(period), f3=services.f3_data(period),
                   months_base=MONTHS_BASE, months_block=MONTHS_BLOCK,
                   is_last=period == periods[-1],
                   steps=[(k, label, totals["contract"].get(k, 0.0), totals["before"][k], totals["this"][k],
                           totals["cum"][k], totals["left"][k]) for k, label in STEPS])
    return render(request, "texnazor/object.html", ctx)


def _int(v) -> int:
    try:
        return int(v or 0)
    except ValueError:
        return 0


def _history(o) -> list:
    """Ofyorta tuzatishlari tarixi (yangisi tepada) — qaysi blok / bo'limda ekani bilan."""
    paths = services.section_paths(o)
    out = list(o.corrections.select_related("item", "author", "period", "reverted_by")[:300])
    last = services.latest_corrections(o)  # bekor qilsa bo'ladigani — har ishning oxirgi amaldagisi
    for c in out:
        c.where = paths.get(c.item.section_id)
        c.can_revert = c.item_id in last and last[c.item_id].pk == c.pk
    return out


def _warn(o) -> dict:
    """Xatosi bor ishlar: qatorida «⚠» belgisi (ish -> qisqa izoh)."""
    return {w["item"]: w["title"] for w in services.warnings(o) if w["item"]}


CHUNK = 40  # bo'lak: shuncha ish (resurslari bilan); brauzer faqat ko'rinib turgan bo'laklarni chizadi


def rows(request, obj_id):
    """Butun ofyorta (JSON): jadval sarlavhasi va bo'laklar — har biri tayyor HTML qatorlar (blok, bo'lim, ish, resurs).
    18 ming qatorni brauzer birdan chizsa qotadi: sahifa bo'laklarni ko'rinishiga qarab qo'yadi va olib turadi."""
    o = _obj(request, obj_id)
    period = _period(o, request)
    months = request.GET.get("view") == "months"  # «Ҳамма ойлар»: har oy alohida ustun, oxirida qoldiq
    f2v = request.GET.get("view") == "f2"  # «Ф-2»: faqat shu oyda yozilganlar, resurslari bilan, oxirida svodka
    periods = list(o.periods.all())
    heads, blocks = _month_blocks(periods, period, request.GET.get("all") == "1", _int(request.GET.get("fit")) or MONTHS_SHOWN) \
        if months else ([], [])
    cols = 5 + 4 + 2 * (len(blocks) + 1) if months else 9 if f2v else 10 if period.closed else 11
    per = services.done_by_period(o) if months else {}
    money = services.section_money_by_period(o) if months else {}
    stats, warn, fixes = services.section_stats(period), _warn(o), services.latest_corrections(o)
    by_sec = {}
    for r in services.item_rows(period, o.items.filter(kind__in=services.ITEM_KINDS).prefetch_related("resources")):
        if not f2v or (r["this"] and r["item"].kind != Item.Kind.EXTRA):  # Ф-2: qo'shimcha qatorlar — svodkada
            by_sec.setdefault(r["item"].section_id, []).append(r)
    chunks, secs = [], {}

    def chunk():
        if not chunks or chunks[-1]["w"] >= CHUNK:
            chunks.append({"h": [], "w": 0, "r": 0, "s": 0, "p": 0, "t": [], "items": [], "skip": [], "secs": []})
        return chunks[-1]

    def add_rows(rs):
        for r in rs:
            c = chunk()
            c["h"].append(tn.item_row_months(r, blocks, per.get(r["item"].pk, {}), period) if months
                          else tn.item_row_f2(r) if f2v else tn.item_row(r, period, warn, fixes))
            c["w"] += 1
            c["r"] += len(r["res"])
            c["items"].append(r["item"].pk)
            if r["prev"]:  # oldingi oylarda tugagan — belgilanmaydi, «Қолганлари» filtrida yashirin
                c["p"] += 1
                c["skip"].append(r["item"].pk)
            if r["this"]:  # shu oyda yozilgan — «… да ёзилганлар» filtri
                c["t"].append(r["item"].pk)

    def add_head(html, sid=None):
        c = chunk()
        c["h"].append(html)
        c["s"] += 1
        if sid:
            c["secs"].append(sid)

    add_rows(by_sec.get(None, []))
    block = ""
    pending = []  # Ф-2: blok / oraliq sarlavha faqat ostida shu oy ishi bo'lsa yoziladi
    for s in o.sections.all():
        if f2v:
            if s.is_block:
                pending = [(tn.head_row_f2(s, cols, block=True), None)]
            elif s.pk not in by_sec:
                pending = pending[:1] + [(tn.head_row("group", s, period, cols), None)] if pending and "tn-bh" in pending[0][0] \
                    else [(tn.head_row("group", s, period, cols), None)]
            else:
                for html, sid in pending + [(tn.head_row_f2(s, cols), s.pk)]:
                    add_head(html, sid)
                pending = []
                st = services.section_pct(stats.get(s.pk) or {"contract": 0.0, "before": 0.0, "this": 0.0})
                secs[s.pk] = {"t": readable(s.title), "b": "", "n": len(by_sec[s.pk]), "this": st["this"], "pct": st["pct"]}
                add_rows(by_sec[s.pk])
            continue
        if s.is_block:
            block = services.block_label(s.title)
            add_head(tn.head_row("block", s, period, cols, block=block))
        elif s.pk not in by_sec:
            add_head(tn.head_row("group", s, period, cols))
        else:
            st = services.section_pct(stats.get(s.pk) or {"contract": 0.0, "before": 0.0, "this": 0.0})
            secs[s.pk] = {"t": readable(s.title), "b": readable(block), "n": s.items_count, "this": st["this"], "pct": st["pct"],
                          "left": st["contract"] - sum(money.get(s.pk, {}).values())}  # «Ҳамма ойлар»: hamma oylardan keyingi qoldiq
            add_head(tn.head_row_months(s, blocks, money.get(s.pk, {}), st) if months
                     else tn.head_row("section", s, period, cols, st), s.pk)
            add_rows(by_sec[s.pk])
    sv = None
    if months:  # oxirida — Ф-2 dagidek «JAMI» va svodka, har blok uchun (alohida bo'lak: saqlanganda yangilanadi)
        chunks.append({"h": [_months_svodka(o, blocks, cols)], "w": 0, "r": 0, "s": len(STEPS) + 1, "p": 0, "t": [],
                       "items": [], "skip": [], "secs": []})
        sv = len(chunks) - 1
    if f2v:  # Ф-2 oxiri: JAMI (turlar bo'yicha, soatlar bilan) va svodka — shu oy akti
        f2m = services.month_f2(period)
        chunks.append({"h": [tn.svodka_f2(STEPS, services.period_totals(period)["this"], f2m["money"], f2m["hours"], cols)],
                       "w": 0, "r": 0, "s": len(STEPS) + 3, "p": 0, "t": [], "items": [], "skip": [], "secs": []})
    for c in chunks:
        c["h"] = "".join(c["h"])
    panel = render_to_string("texnazor/_panel.html", {"o": o, "period": period, "full": True, "chunks": range(len(chunks)),
                                                      "works": sum(c["w"] for c in chunks), "res": sum(c["r"] for c in chunks),
                                                      "earlier": _earlier(o, period), "rows": True, "months": months, "f2v": f2v, "cols": cols,
                                                      "heads": heads, "hidden": len(blocks) < len(periods) or request.GET.get("all") == "1",
                                                      "all_months": request.GET.get("all") == "1",
                                                      "months_width": MONTHS_BASE + MONTHS_BLOCK * (len(blocks) + 1)}, request=request)
    return JsonResponse({"panel": panel, "chunks": chunks, "secs": secs, "cols": cols, "sv": sv}, json_dumps_params={"ensure_ascii": False})  # кирилл \uXXXX бўлмасин (6 баробар катта)


def _months_svodka(o, blocks, cols) -> str:
    """Svodka zanjiri (calc.chain) har blokka: ofyorta (shartnoma), har oy / oylar guruhi va qoldiq (shartnoma − hamma oylar)."""
    by, rates = services.cats_by_period(o), o.rates or {}

    def chain(pids):
        cat = {}
        for pid in pids:
            for k, v in by.get(pid, {}).items():
                cat[k] = cat.get(k, 0.0) + v
        return calc.chain(cat, rates)
    contract = o.contract_totals or calc.chain({}, rates)
    done = chain(list(by))
    return tn.svodka_months(STEPS, cols, contract, [chain(pids) for pids, _ in blocks], [sel for _, sel in blocks],
                            {k: contract.get(k, 0.0) - done[k] for k in done})


MONTHS_SHOWN = 3  # «Ҳамма ойлар»da alohida ko'rinadigan oylar (sahifa ekran eniga qarab ``fit`` yuboradi)
MONTHS_BASE, MONTHS_BLOCK = 926, 170  # jadval kengligi: №…birlik (nom — 300 px) + «Офёрта» bloki va har bir 2 ustunli blok (px)


def _month_blocks(periods, period, show_all: bool, fit: int = MONTHS_SHOWN):
    """«Ҳамма ойлар» bloklari: ekranga sig'adigan ``fit`` ta blok. Oylar ko'p bo'lsa — oxirgilari (tanlangan oy albatta)
    alohida, eskilari bitta «Олдинги ойлар» blokida, keyingilari — «Кейинги ойлар». ``show_all`` — hammasi alohida.
    Natija: sarlavhalar [(nomi, izoh, tanlanganmi)] va qatorlar uchun [(oylar id lari, tanlanganmi)]."""
    fit = max(1, min(fit, 24))
    if show_all or len(periods) <= fit:
        groups = [[p] for p in periods]
    else:
        i = periods.index(period)
        for k in range(fit, 0, -1):  # alohida oylar soni: jamlangan bloklar bilan birga ``fit`` dan oshmasin
            last = len(periods) - k  # odatda — oxirgi k oy; tanlangan oy eskiroq bo'lsa — u bilan tugaydigan k oy
            start = last if i >= last else max(0, i - k + 1)
            if k + (start > 0) + (start + k < len(periods)) <= fit:
                break
        before, shown, after = periods[:start], periods[start:start + k], periods[start + k:]
        groups = ([before] if before else []) + [[p] for p in shown] + ([after] if after else [])
    heads, blocks = [], []
    for g in groups:
        sel = period in g and len(g) == 1
        if len(g) == 1:
            heads.append((g[0].title, "ёпилган" if g[0].closed else "очиқ", sel))
        else:
            name = "Олдинги ойлар" if g[-1].key < period.key else "Кейинги ойлар"
            heads.append((name, f"{g[0].title} — {g[-1].title}" if len(g) > 1 else g[0].title, False))
        blocks.append(([p.pk for p in g], sel))
    return heads, blocks


def _earlier(o, period) -> list:
    return [p for p in o.periods.all() if p.key < period.key]  # «Аввал» устуни шу ойлар йиғиндиси


def _section_stats(period, section) -> dict:
    """Bo'lim raqamlari (hajm saqlangandan keyin mundarija va sarlavhada yangilanadi)."""
    st = services.section_stats(period).get(section.pk) if section else None
    return services.section_pct(st or {"contract": 0.0, "before": 0.0, "this": 0.0}) | {"id": section.pk if section else None}


def search(request, obj_id):
    o = _obj(request, obj_id)
    period = _period(o, request)
    q = request.GET.get("q", "").strip()
    ctx = {"o": o, "period": period, "search": q}
    if len(q) < 2:
        return render(request, "texnazor/_panel.html", {**ctx, "rows": [], "hint": "Камида 2 та ҳарф ёзинг"})
    cond = Q(search__contains=services.search_key(q)) | Q(num=q)
    if q.isdigit():  # Excel qatori bo'yicha ham (resurs qatori bo'lsa — uning ishi)
        hit = o.items.filter(excel_row=int(q)).values_list("pk", "parent_id").first()
        if hit:
            cond |= Q(pk=hit[1] or hit[0])
    items = o.items.filter(kind__in=services.ITEM_KINDS).filter(cond).prefetch_related("resources")[:SEARCH_LIMIT]
    rows = services.item_rows(period, items)
    where = services.section_paths(o)  # qidiruvda — qaysi blok / bo'limdan
    for r in rows:
        r["where"] = where.get(r["item"].section_id)
    return render(request, "texnazor/_panel.html", {**ctx, "rows": rows, "limited": len(rows) >= SEARCH_LIMIT, "warn": _warn(o),
                                                    "fixes": services.latest_corrections(o), "earlier": _earlier(o, period)})


def _summary(period):
    t = services.period_totals(period)
    return {"this": t["this"]["contractor"], "cum": t["cum"]["contractor"], "left": t["left"]["contractor"],
            "contract": t["contract"].get("contractor", 0.0), "html": render_to_string("texnazor/_summary.html", {
                "period": period, "f2": services.month_f2(period), "steps": [(k, label, t["contract"].get(k, 0.0), t["before"][k], t["this"][k],
                                             t["cum"][k], t["left"][k]) for k, label in STEPS]})}


@require_POST
def set_done(request, obj_id):
    """JSON: ``{"period": 1, "item": 5, "value": "35" | "60%" | ""}`` -> qator va jami summalar."""
    o = _obj(request, obj_id)
    try:
        data = json.loads(request.body or b"{}")
        period = _period(o, request, data)
        item = get_object_or_404(Item, pk=int(data["item"]), obj=o)
        services.set_done(period, item, data.get("value"))
    except services.TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    row = services.item_rows(period, Item.objects.filter(pk=item.pk).prefetch_related("resources"))[0]
    extra = {}
    if data.get("view") == "months":  # «Ҳамма ойлар»da yozildi — shu ko'rinishdagi qator va oxiridagi svodka
        periods = list(o.periods.all())
        _, blocks = _month_blocks(periods, period, bool(data.get("all")), _int(data.get("fit")) or MONTHS_SHOWN)
        vals = services.done_by_period(o).get(item.pk, {})
        html = tn.item_row_months(row, blocks, vals, period)
        extra["svodka"] = _months_svodka(o, blocks, 5 + 4 + 2 * (len(blocks) + 1))
    else:
        html = tn.item_row(row, period, _warn(o), services.latest_corrections(o, [item.pk]))
    return JsonResponse({"ok": True, "row": html, "section": _section_stats(period, item.section),
                         "summary": _summary(period), **extra})


@require_POST
def correct(request, obj_id, item_id):
    """JSON: ``{"period": 1, "value": "0,4", "basis": "Қўшимча келишув №2", "note": ""}`` — shartnoma hajmini tuzatish."""
    o = _obj(request, obj_id)
    item = get_object_or_404(Item, pk=item_id, obj=o)
    try:
        data = json.loads(request.body or b"{}")
        period = o.periods.filter(pk=data.get("period") or 0).first()
        c = services.correct_item(request.user, item, period, data.get("value"), data.get("basis"), data.get("note", ""))
        audit(request, o, f"офёртага тузатиш: {item.num} {item.name[:60]} — {services.fmt_qty(c.old_qty)} → "
                          f"{services.fmt_qty(c.new_qty)} {item.unit} ({c.basis[:80]})")
    except services.TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    return JsonResponse({"ok": True, "id": c.pk})


@require_POST
def set_paid(request, obj_id, period_id):
    """Ф-3 bo'yicha haqiqatda to'langan summa (bo'sh — o'chiriladi)."""
    o = _obj(request, obj_id)
    p = get_object_or_404(Period, pk=period_id, obj=o)
    text = request.POST.get("paid", "").replace(" ", "").replace(" ", "").replace(",", ".").strip()
    try:
        p.paid = float(text) if text else None
    except ValueError:
        messages.error(request, f"Тўланган сумма нотўғри: «{request.POST.get('paid')}»")
        return _to(o, p)
    p.save(update_fields=["paid"])
    audit(request, o, f"{p.title}: тўланган сумма " + (f"{p.paid:,.2f}".replace(",", " ") if p.paid is not None else "ўчирилди"))
    return _to(o, p)


@require_POST
def fill_items(request, obj_id):
    """JSON: ``{"period": 1, "items": [5, 6], "pct": 100}`` — tanlangan ishlar jami shuncha % bajarilgan bo'lsin."""
    o = _obj(request, obj_id)
    try:
        data = json.loads(request.body or b"{}")
        period = _period(o, request, data)
        ids = [int(x) for x in data.get("items") or []][:5000]
        if not ids:
            raise services.TexnazorError("Аввал ишларни белгиланг")
        items = list(o.items.filter(pk__in=ids, kind__in=services.ITEM_KINDS))
        pct = float(str(data.get("pct", "100")).replace(",", ".").replace("%", ""))
        n = services.fill_items(period, items, pct)
        audit(request, o, f"{period.title}: {len(items)} та иш жами {pct:g}% бажарилди деб белгиланди")
    except services.TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "фоиз нотўғри"}, status=400)
    stats = services.section_stats(period)
    secs = [services.section_pct(stats.get(sid) or {"contract": 0.0, "before": 0.0, "this": 0.0}) | {"id": sid}
            for sid in {it.section_id for it in items if it.section_id}]
    return JsonResponse({"ok": True, "changed": n, "sections": secs, "summary": _summary(period)})


@require_POST
def fill_section(request, obj_id, section_id):
    o = _obj(request, obj_id)
    section = get_object_or_404(Section, pk=section_id, obj=o)
    try:
        data = json.loads(request.body or b"{}")
        period = _period(o, request, data)
        pct = float(str(data.get("pct", "100")).replace(",", "."))
        n = services.fill_section(period, section, pct)
        audit(request, o, f"{period.title}: «{section.title[:80]}» бўлими {pct:g}% бажарилди деб белгиланди")
    except services.TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "фоиз нотўғри"}, status=400)
    return JsonResponse({"ok": True, "changed": n, "section": _section_stats(period, section),
                         "summary": _summary(period)})


def _to(o, period=None):
    url = reverse("texnazor:object", args=[o.pk])
    return redirect(f"{url}?p={period.pk}" if period else url)


def f2_download(request, obj_id, period_id):
    """Ф-2 (bajarilgan ishlar dalolatnomasi) — shu oy, Excel: texnazor faylidagi varaq bilan bir xil, formulalar bilan."""
    o = _obj(request, obj_id)
    p = get_object_or_404(Period, pk=period_id, obj=o)
    resp = HttpResponse(f2.build(p), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    audit(request, o, f"{p.title}: Ф-2 юклаб олинди")
    resp["Content-Disposition"] = content_disposition_header(True, f2.file_name(p))
    return resp


def f3_download(request, obj_id, period_id):
    """Ф-3 (справка-счёт-фактура) — shu oy, Excel: texnazor faylidagi varaq bilan bir xil."""
    o = _obj(request, obj_id)
    p = get_object_or_404(Period, pk=period_id, obj=o)
    resp = HttpResponse(f3.build(p), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    audit(request, o, f"{p.title}: Ф-3 юклаб олинди")
    resp["Content-Disposition"] = content_disposition_header(True, f3.file_name(p))
    return resp


def _money(text):
    """«29 630 408 740,15» -> 29630408740.15; bo'sh -> None."""
    t = (text or "").replace(" ", "").replace(" ", "").replace(",", ".").strip()
    return float(t) if t else None


def _f3_back(o, p):
    return redirect(f"{reverse('texnazor:object', args=[o.pk])}?p={p.pk}#f3")


@require_POST
def f3_period(request, obj_id, period_id):
    """Shu oyning Ф-3 si: hujjat raqami, tuzilgan sana, davr boshi (bo'sh — avtomatik)."""
    o = _obj(request, obj_id)
    p = get_object_or_404(Period, pk=period_id, obj=o)
    m = {}
    num = request.POST.get("num", "").strip()
    if num:
        if not num.isdigit():
            messages.error(request, f"Ф-3 рақами нотўғри: «{num}»")
            return _f3_back(o, p)
        m["num"] = int(num)
    for k in ("date", "start"):
        v = request.POST.get(k, "").strip()
        if v and services._date(v) is None:
            messages.error(request, f"Сана нотўғри: «{v}»")
            return _f3_back(o, p)
        if v:
            m[k] = v
    p.f3 = m
    p.save(update_fields=["f3"])
    audit(request, o, f"{p.title}: Ф-3 рақами / санаси сақланди")
    return _f3_back(o, p)


@require_POST
def f3_settings(request, obj_id):
    """Obyekt va pudratchi ma'lumotlari (bir marta, obyektga saqlanadi): nomi, manzili, buyurtmachi / pudratchi va
    rekvizitlari, shartnoma, imzolovchilar, joriy yilga mo'ljallangan summa."""
    o = _obj(request, obj_id)
    url = reverse("texnazor:object", args=[o.pk])
    back = redirect(f"{url}?p={int(request.POST['period'])}#info" if request.POST.get("period", "").isdigit() else url)
    P = request.POST
    try:
        plan = _money(P.get("year_plan"))
    except ValueError:
        messages.error(request, f"Жорий йилга сумма нотўғри: «{P.get('year_plan')}»")
        return back
    if P.get("contract_date") and services._date(P["contract_date"]) is None:
        messages.error(request, f"Шартнома санаси нотўғри: «{P['contract_date']}»")
        return back
    if P.get("name", "").strip():
        o.name = P["name"].strip()[:300]
    o.address = P.get("address", "").strip()[:300]
    o.customer = P.get("customer", "").strip()[:300]
    o.contractor = P.get("contractor", "").strip()[:300]
    o.f3 = {k: " ".join(P.get(k, "").split())[:300] for k in services.F3_KEYS}
    o.f3["year_plan"] = plan
    s = o.f3
    if s["contract_no"] or s["contract_date"]:  # sarlavhada ko'rinadigan qisqa yozuv
        d = services._date(s["contract_date"])
        o.contract = " ".join(x for x in (f"№ {s['contract_no']}" if s["contract_no"] else "", f"{d:%d.%m.%Y}" if d else "") if x)
    o.save(update_fields=["name", "address", "customer", "contractor", "contract", "f3"])
    messages.success(request, "Объект маълумотлари сақланди")
    audit(request, o, "объект ва пудратчи маълумотлари сақланди")
    return back


@require_POST
def add_period(request, obj_id):
    o = _obj(request, obj_id)
    try:
        p = services.add_period(o, int(request.POST.get("year") or 0), int(request.POST.get("month") or 0))
    except (services.TexnazorError, ValueError) as e:
        messages.error(request, str(e))
        return _to(o)
    audit(request, o, f"{p.title} ойи қўшилди")
    return _to(o, p)


@require_POST
def close_period(request, obj_id, period_id):
    o = _obj(request, obj_id)
    p = get_object_or_404(Period, pk=period_id, obj=o)
    p.closed = request.POST.get("closed") == "1"
    p.save(update_fields=["closed"])
    audit(request, o, f"{p.title} " + ("ёпилди" if p.closed else "қайта очилди"))
    return _to(o, p)


@require_POST
def delete(request, obj_id):
    """Obyektni o'chirish. Tayyor obyekt — ikkinchi bosqichda «ЎЧИРИШ» deb yozilgandagina (o'qilmagan — darhol)."""
    o = _obj(request, obj_id)
    if o.status == SiteObject.Status.READY and not services._confirmed(request.POST.get("confirm"), services.DELETE_WORD):
        messages.error(request, f"Объект ўчирилмади: тасдиқлаш учун «{services.DELETE_WORD}» деб ёзинг")
        return _to(o)
    audit(request, o, "объект ўчирилди")
    services.delete_object(o)
    return redirect("texnazor:index")


@require_POST
def delete_period(request, obj_id, period_id):
    o = _obj(request, obj_id)
    p = get_object_or_404(Period, pk=period_id, obj=o)
    try:
        title = p.title
        services.delete_period(p, request.POST.get("confirm"))
    except services.TexnazorError as e:
        messages.error(request, str(e))
        return _to(o, p)
    audit(request, o, f"{title} ойи ўчирилди")
    return _to(o)


@require_POST
def revert_correction(request, obj_id, correction_id):
    o = _obj(request, obj_id)
    c = get_object_or_404(Correction, pk=correction_id, obj=o)
    try:
        services.revert_correction(request.user, c, json.loads(request.body or b"{}").get("confirm"))
        audit(request, o, f"тузатиш бекор қилинди: {c.item.num} {c.item.name[:60]}")
    except services.TexnazorError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "нотўғри сўров"}, status=400)
    return JsonResponse({"ok": True, "item": c.item_id, "section": c.item.section_id})

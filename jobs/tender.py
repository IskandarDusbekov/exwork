"""Объект таҳлили: смета нархлари ↔ бизнинг (бозор) нархлар, ҳажм (норма) текшируви ва
кутилаётган фойда. Ҳисоб — фақат рақамлар (тахмин эмас); «нима яхши, нима ёмон» шу рақамлардан ёзилади.

Таҳлил нимага асосланиши — панелда (``TenderSettings`` ва ``TenderFactor``): чегаралар, ҳақиқий харажат
коэффициентлари ва админнинг тажриба омиллари. Омилларнинг ўзи фойдаланувчига кўринмайди (фақат админ
«кўринсин» деганлари) — ҳисобга эса ҳаммаси киради."""
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional

from django.core.cache import cache

from core.svodka import DEFAULT_PERCENTS

from .models import Job, TenderFactor, TenderSettings

CATS = ["I", "J", "K", "L", "M"]
CAT_LABELS = {"I": "Иш ҳақи", "J": "Машина ва механизмлар", "K": "Материаллар", "L": "Кабель маҳсулотлари",
              "M": "Ускуналар"}
SKIP_STATUSES = {"WORK_HEADER", "MACHINIST", "CLEARED"}
SETTINGS_KEY = "tender:settings"
VERDICTS = {
    "go": ("Олиш мумкин", "Ҳисоб бўйича фойда етарли."),
    "careful": ("Эҳтиёт бўлинг", "Фойда бор, лекин кам — хавфларни кўриб чиқинг."),
    "no": ("Тавсия этилмайди", "Ҳисоб бўйича фойда етарли эмас ёки зарар."),
}
ORIG_TTL = 7 * 24 * 3600

# (калит, номи, изоҳи, бирлиги, стандарт, энг ками, энг кўпи, гуруҳ)
PARAMS = [
    ("good_margin", "«Кириш мумкин» — фойда камида", "Кутилаётган фойда (даромадга нисбатан) шундан кўп бўлса — яшил хулоса.",
     "%", 10, -100, 100, "Хулоса"),
    ("min_margin", "«Эҳтиёт бўлинг» — фойда камида", "Шундан кам бўлса — «Тавсия этилмайди». Оралиғи — «Эҳтиёт бўлинг».",
     "%", 3, -100, 100, "Хулоса"),
    ("min_coverage", "Ишончли таҳлил: солиштирилган улуш", "Смета суммасининг шунча қисми бизнинг нархлар билан "
     "солиштирилган бўлиши керак, акс ҳолда «маълумот етарли эмас» деб огоҳлантирилади.", "%", 80, 0, 100, "Хулоса"),
    ("low_price", "«Нарх паст ёзилган»: бозордан камида", "Сметадаги нарх бозор нархидан шунча фоиз арзон бўлса — "
     "ресурс «паст ёзилган» рўйхатига тушади.", "%", 25, 1, 100, "Текширувлар"),
    ("outlier", "Шубҳали нарх: фарқ камида", "Сметадаги ва бозор нархи шунча баравар фарқ қилса (ёки сметада нарх 1 сўм "
     "бўлса) — бу кўпинча бирлик (кг/т) ёки маълумот хатоси: ресурс солиштирилмайди, «Шубҳали нархлар» рўйхатига тушади.",
     "марта", 3, 1.5, 100, "Текширувлар"),
    ("norm_gap", "Ҳажм (норма) фарқи: камида", "Сметадаги норма базадаги одатий нормадан шунча фоиз фарқ қилса — "
     "«ҳажм кам/кўп ёзилган» деб кўрсатилади.", "%", 15, 1, 100, "Текширувлар"),
    ("norm_files", "Норма ишончли: камида нечта файлда", "Базадаги норма шунча хил файлда учраган бўлсагина "
     "солиштирилади.", "та", 2, 1, 100, "Текширувлар"),
    ("norm_in_cost", "Норма фарқини харажатга қўшиш", "Ёқилса — ҳажм кам ёзилган ресурсларнинг етишмаган қисми "
     "харажатга қўшилади (кўп ёзилгани — айрилади).", "", 0, 0, 1, "Текширувлар"),
    ("cost_I", "Иш ҳақи — ҳақиқий харажат", "Қурувчи амалда смета (бозор) нархининг неча фоизини сарфлайди.",
     "%", 100, 0, 300, "Ҳақиқий харажат"),
    ("cost_J", "Машина ва механизмлар — ҳақиқий харажат", "", "%", 100, 0, 300, "Ҳақиқий харажат"),
    ("cost_K", "Материаллар — ҳақиқий харажат", "", "%", 100, 0, 300, "Ҳақиқий харажат"),
    ("cost_L", "Кабель маҳсулотлари — ҳақиқий харажат", "", "%", 100, 0, 300, "Ҳақиқий харажат"),
    ("cost_M", "Ускуналар — ҳақиқий харажат", "", "%", 100, 0, 300, "Ҳақиқий харажат"),
    ("cost_prochie", "Прочие затраты — ҳақиқатда сарфланадиган қисми", "Сметадаги «Прочие затраты подрядчика»нинг "
     "неча фоизи амалда харажат бўлади (қолгани — фойда).", "%", 100, 0, 300, "Ҳақиқий харажат"),
]
PARAM_KEYS = {p[0]: p for p in PARAMS}


# ===================== созламалар =====================
def config() -> dict:
    """{"enabled", "p": {калит: қиймат}, "factors": [...]} — кеш 30 сония (панелда сақланганда тозаланади)."""
    data = cache.get(SETTINGS_KEY)
    if data is None:
        s = TenderSettings.objects.filter(pk=1).first()
        stored = (s.params if s else {}) or {}
        p = {k: float(stored.get(k, default)) for k, _l, _h, _u, default, *_ in PARAMS}
        factors = [{"title": f.title, "scope": f.scope, "effect": f.effect, "visible": f.visible,
                    "user_text": f.user_text} for f in TenderFactor.objects.filter(active=True)]
        data = {"enabled": bool(s and s.enabled), "p": p, "factors": factors}
        cache.set(SETTINGS_KEY, data, 30)
    return data


def forget_config():
    cache.delete(SETTINGS_KEY)


def enabled() -> bool:
    return config()["enabled"]


def parse_params(post) -> dict:
    """Панел формасидан: сон (вергул ҳам) ва чегарада. Хато — ValueError (қайси майдон)."""
    out = {}
    for key, label, _h, unit, default, lo, hi, _g in PARAMS:
        if key == "norm_in_cost":
            out[key] = 1 if post.get(key) else 0
            continue
        raw = str(post.get(key, "")).strip().replace(" ", "").replace(",", ".")
        if raw == "":
            out[key] = default
            continue
        try:
            v = float(raw)
        except ValueError:
            raise ValueError(f"«{label}» — сон бўлиши керак")
        if not lo <= v <= hi:
            raise ValueError(f"«{label}» — {lo} дан {hi} гача")
        out[key] = int(v) if v.is_integer() else round(v, 2)
    if out["min_margin"] > out["good_margin"]:
        raise ValueError("«Эҳтиёт бўлинг» чегараси «Кириш мумкин»дан катта бўлмасин")
    return out


# ===================== сметадан маълумот =====================
def _num(v) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
        try:
            return float(t)
        except ValueError:
            return None
    return None


def _orig_prices(job: Job) -> Dict[int, tuple]:
    """Эски (смета нархлари сақланмаган) смета: асл файлдан ўқилади, кешда сақланади."""
    path = job.abs(job.local_path)
    if not job.local_path or not path.exists():
        return {}
    key = f"tender:orig:{job.pk}:{int(path.stat().st_mtime)}:{job.local_sheet}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    from openpyxl import load_workbook

    from core.columns import detect_local_columns
    from core.tender import tender_prices
    out = {}
    try:
        wb = load_workbook(path, data_only=True, keep_links=False, read_only=False)
        try:
            ws = wb[job.local_sheet] if job.local_sheet in wb.sheetnames else wb.worksheets[0]
            out = tender_prices(ws, detect_local_columns(ws, job.local_columns or None))
        finally:
            wb.close()
    except Exception:  # бузуқ ёки ўчирилган файл — таҳлилсиз
        out = {}
    cache.set(key, out, ORIG_TTL)
    return out


def job_items(job: Job) -> dict:
    """Битта смета: ресурслар (тендер ва бозор суммаси билан) ва ЛРВ ишлари (норма текшируви учун)."""
    from resbase.normbook import parse as parse_shifr
    from resbase.resfile import res_code
    res = job.result or {}
    rows = res.get("rows") or []
    has_t = any("t_price" in r for r in rows)
    orig = {} if has_t else _orig_prices(job)

    def own(r):  # сметанинг ўзидаги нарх ва сумма
        if has_t:
            return _num(r.get("t_price")), _num(r.get("t_amount"))
        tp, ta = orig.get(r["row"]) or (None, None)
        return _num(tp), _num(ta)

    priced_rows = [r for r in rows if r.get("status") not in SKIP_STATUSES and not r.get("cleared")]
    with_own = sum(1 for r in priced_rows if any(own(r)))
    # Смета нархлари қаерда: сметанинг ўзида («smeta») ёки смета нархланган тендер RES файлида («res») —
    # иккинчисида бозор нархи ресурс базасидан (вилоят ва давр медианаси)
    mode, market, period = "", {}, {}
    if priced_rows and with_own >= 0.2 * len(priced_rows):
        mode = "smeta"
    elif job.res_path:
        from resbase.services import period_complete, price_map
        from .services import base_period_of
        period = job.base_period if period_complete(job.base_period) else base_period_of(job.owner)
        market = price_map(period) if period_complete(period) else {}
        mode = "res"
    from core.normalize import unit_key
    items, works = [], []
    work = None
    for r in rows:
        code = r.get("code")
        shifr = parse_shifr(code) if code else None
        if shifr:
            work = {"shifr": shifr[0], "name": str(r.get("name") or "")[:200],
                    "qty": _num(r.get("qty_total")) or _num(r.get("qty")), "res": []}
            works.append(work)
            continue
        if not mode or r.get("status") in SKIP_STATUSES or r.get("cleared"):
            continue
        qty = _num(r.get("qty"))
        rcode = res_code(code) if code else ""
        g, h = _num(r.get("g")), _num(r.get("h"))
        if h is None and g is not None and qty:
            h = g * qty
        if mode == "smeta":
            tp, ta = own(r)
            if ta is None and tp is not None and qty:
                ta = tp * qty
            if tp is None and ta is not None and qty:
                tp = ta / qty
            rp, ra = g, h
            base = r.get("status") == "BASE"
        else:
            from_res = g is not None and r.get("status") != "BASE"
            tp, ta = (g, h) if from_res else (None, None)
            m = market.get(rcode)
            if m and m["unit_key"] == unit_key(r.get("unit")) and qty:
                rp, ra = m["price"], m["price"] * qty
            elif r.get("status") == "BASE" and g is not None:
                rp, ra = g, h  # RES'да йўқ — нархи базадан олинган
            else:
                rp, ra = None, None
            base = False  # бу усулда бозор нархи ҳамиша базадан — алоҳида белгиланмайди
        if not ta and not ra:
            continue
        cat = r.get("category") if r.get("category") in CATS else "K"
        item = {"name": str(r.get("name") or "").strip()[:200], "unit": str(r.get("unit") or "").strip()[:30],
                "cat": cat, "qty": qty or 0, "tp": tp, "ta": ta or 0, "rp": rp, "ra": ra,
                "base": base, "job": str(job.pk), "row": r["row"]}
        items.append(item)
        norma = _num(r.get("qty_unit"))
        if work is not None and rcode and norma is not None:
            work["res"].append({"code": rcode, "norma": norma, "name": item["name"], "unit": item["unit"],
                                "price": rp if rp is not None else tp})
    pct = dict(DEFAULT_PERCENTS)
    pct.update({k: v for k, v in (res.get("percents") or {}).items() if k in pct and isinstance(v, (int, float))})
    return {"job": job, "items": items, "works": works, "pct": pct, "mode": mode, "period": period,
            "priced": len(priced_rows)}


def _periods(data) -> List[str]:
    from resbase.services import period_label
    return sorted({period_label(d["period"]) for d in data if d["mode"] == "res" and d["period"]} - {""})


# ===================== ҳисоб =====================
def _svodka(x: Dict[str, float], pct: dict) -> dict:
    """Сметадаги сводка формулалари (core.svodka) — сумма бўйича: тўғри харажат, прочие, суғурта, НДСсиз."""
    pr = (x["I"] + x["J"] + x["K"] * (1 + pct["transport_mat"]) + x["L"] * (1 + pct["transport_kabel"])
          + x["M"] * (1 + pct["transport_obor"]))
    prochie = (pr - x["M"] * (1 + pct["transport_obor"])) * pct["prochie"]
    strah = (pr + prochie) * pct["strahovanie"]
    return {"pryamye": pr, "prochie": prochie, "strah": strah, "bez_nds": pr + prochie + strah}


def _factor_mult(factors, scope) -> float:
    m = 1.0
    for f in factors:
        if f["scope"] == scope or (f["scope"] == "all" and scope in CATS):
            m *= 1 + f["effect"] / 100
    return m


def _base_norms(shifrs, codes, min_files: int) -> Dict[tuple, dict]:
    """Базадаги одатий нормалар {(шифр, код): {"norma", "files"}} — энг кўп файлда учрагани."""
    from resbase.models import WorkResource
    if not shifrs or not codes:
        return {}
    seen = defaultdict(lambda: defaultdict(set))
    for shifr, code, norma, src in (WorkResource.objects.filter(work__shifr__in=list(shifrs), code__in=list(codes),
                                                                 extra=False, norma__isnull=False)
                                    .values_list("work__shifr", "code", "norma", "work__source_id")):
        seen[(shifr, code)][float(norma)].add(src)
    out = {}
    for key, variants in seen.items():
        norma, files = max(variants.items(), key=lambda kv: len(kv[1]))
        n_files = len(set().union(*variants.values()))
        if len(files) >= min_files and norma > 0:
            out[key] = {"norma": norma, "files": len(files), "all_files": n_files}
    return out


def analyse(jobs: List[Job], rise: float = 0) -> dict:
    cfg = config()
    p, factors = cfg["p"], cfg["factors"]
    data = [job_items(j) for j in jobs]
    items = [it for d in data for it in d["items"]]
    report = {"jobs": len(jobs), "rows": len(items)}
    if not items:
        # нархланган қаторлар бор, лекин смета нархи йўқ (смета нархсиз ва RES'сиз нархланган) — бошқа хабар
        report.update(status="no_prices" if any(d["priced"] for d in data) else "empty")
        return report
    with_t = [it for it in items if it["ta"]]
    if len(with_t) < max(1, 0.2 * len(items)):
        report.update(status="no_prices")
        return report

    # ---- шубҳали нархлар: сметада 1 сўм (нарх қўйилмаган) ёки нархлар бир неча баравар фарқ қилади
    #      (кўпинча бирлик: кг ўрнига т) — солиштирилмайди, зарар/фойдага кирмайди ----
    out_k = p["outlier"]
    suspicious = []
    for it in items:
        tp, rp = it["tp"], it["rp"]
        why = ""
        if tp is not None and tp <= 1 and (rp or 0) > 1:
            why = "сметада нархи 1 сўм — нарх қўйилмаган"
        elif tp and rp and (tp / rp > out_k or rp / tp > out_k):
            k = max(tp / rp, rp / tp)
            why = f"нархлар {k:,.0f} баравар фарқ қилади — бирлиги ёки нархи хато бўлиши мумкин".replace(",", " ")
        it["susp"] = why
        if why:
            suspicious.append({**it, "why": why})
            it["ra"] = None
    grouped = {}
    for x in suspicious:  # бир хил ресурс бир неча қаторда — битта қатор, миқдор жамланади
        k = (x["name"], x["unit"], x["tp"], x["rp"])
        if k in grouped:
            grouped[k]["qty"] = (grouped[k]["qty"] or 0) + (x["qty"] or 0)
            grouped[k]["ta"] = (grouped[k]["ta"] or 0) + (x["ta"] or 0)
        else:
            grouped[k] = dict(x)
    suspicious = sorted(grouped.values(), key=lambda x: -abs((x["ta"] or 0) - (x["rp"] or 0) * (x["qty"] or 0)))

    coef = {c: p[f"cost_{c}"] / 100 * _factor_mult(factors, c) for c in CATS}
    prochie_coef = p["cost_prochie"] / 100 * _factor_mult(factors, "prochie")
    rev_mult = _factor_mult(factors, "revenue")

    # ---- суммалар (ҳар смета ўз фоизлари билан) ----
    cat_t, cat_r = defaultdict(float), defaultdict(float)
    tender_bez = tender_snds = cost = cost_k = 0.0
    compared_t = total_t = 0.0
    unpriced = {"n": 0, "amount": 0.0}
    missing = {"n": 0, "amount": 0.0, "rows": []}
    for d in data:
        pct = d["pct"]
        t, r = defaultdict(float), defaultdict(float)
        for it in d["items"]:
            ta, ra = it["ta"], it["ra"]
            if ta:
                total_t += ta
            if ta and ra is not None:
                compared_t += ta
            if ra is None:  # бозор нархи номаълум ёки шубҳали — сметадагидек олинади (таъсирсиз)
                if not it["susp"]:
                    unpriced["n"] += 1
                    unpriced["amount"] += ta
                ra = ta
            if not ta and ra:
                missing["n"] += 1
                missing["amount"] += ra
                missing["rows"].append(it)
            t[it["cat"]] += ta
            r[it["cat"]] += ra
        for c in CATS:
            cat_t[c] += t[c]
            cat_r[c] += r[c]
        sv_t = _svodka({c: t[c] for c in CATS}, pct)
        tender_bez += sv_t["bez_nds"]
        tender_snds += sv_t["bez_nds"] * (1 + pct["s_nds"])
        real = _svodka({c: r[c] for c in CATS}, pct)
        own = _svodka({c: r[c] * coef[c] for c in CATS}, pct)
        c_pr = own["pryamye"]
        c_proch = real["prochie"] * prochie_coef
        cost += c_pr + c_proch + (c_pr + c_proch) * pct["strahovanie"]
        cost_k += r["K"] * coef["K"] * (1 + pct["transport_mat"]) * (1 + pct["strahovanie"])

    # ---- ҳажм (норма) ----
    under, over = [], []
    shifrs = {w["shifr"] for d in data for w in d["works"]}
    codes = {x["code"] for d in data for w in d["works"] for x in w["res"]}
    base = _base_norms(shifrs, codes, int(p["norm_files"]))
    gap = p["norm_gap"] / 100
    for d in data:
        for w in d["works"]:
            for x in w["res"]:
                b = base.get((w["shifr"], x["code"]))
                if not b or not w["qty"] or not x["price"]:
                    continue
                diff = (x["norma"] - b["norma"]) / b["norma"]
                if abs(diff) < gap:
                    continue
                money = abs(x["norma"] - b["norma"]) * w["qty"] * x["price"]
                row = {"shifr": w["shifr"], "work": w["name"], "name": x["name"], "unit": x["unit"],
                       "norma": x["norma"], "base": b["norma"], "files": b["files"], "pct": diff * 100, "money": money}
                (under if diff < 0 else over).append(row)
    under.sort(key=lambda x: -x["money"])
    over.sort(key=lambda x: -x["money"])
    under_money, over_money = sum(x["money"] for x in under), sum(x["money"] for x in over)
    if p["norm_in_cost"]:
        cost += under_money - over_money

    # ---- ресурслар: зарар ва фойда ----
    grouped = {}
    for it in items:
        if not it["ta"] or it["ra"] is None:
            continue
        k = (it["name"].upper(), it["unit"].upper())
        g = grouped.setdefault(k, {"name": it["name"], "unit": it["unit"], "cat": it["cat"], "qty": 0.0, "ta": 0.0,
                                   "ra": 0.0, "tp": it["tp"], "rp": it["rp"], "base": it["base"], "n": 0})
        g["qty"] += it["qty"]
        g["ta"] += it["ta"]
        g["ra"] += it["ra"]
        g["n"] += 1
    res_rows = list(grouped.values())
    for g in res_rows:
        g["diff"] = g["ta"] - g["ra"]
        g["pct"] = (g["ta"] / g["ra"] - 1) * 100 if g["ra"] else 0
    low = p["low_price"]
    loss_rows = sorted((g for g in res_rows if g["diff"] < 0), key=lambda g: g["diff"])
    gain_rows = sorted((g for g in res_rows if g["diff"] > 0), key=lambda g: -g["diff"])
    low_rows = [g for g in loss_rows if g["pct"] <= -low]

    # ---- хулоса ----
    # Нархни буюртмачи (давлат ташкилоти) белгилайди — қурувчи олади ёки олмайди. ``rise`` — қурилиш давомида
    # харажат неча фоиз ошиши (саҳифадаги «нархлар ошса» слайдери; ҳисоб — бугунги нархларда).
    revenue = tender_bez * rev_mult
    cost_now = cost
    cost = cost_now * (1 + rise / 100)
    profit = revenue - cost
    margin = profit / revenue * 100 if revenue else 0
    coverage = compared_t / total_t * 100 if total_t else 0
    # захира: харажат неча фоиз ошса фойда нолга (ёки минимал фойдага) тушади
    min_m = p["min_margin"] / 100
    reserve = (revenue / cost_now - 1) * 100 if cost_now else 0
    reserve_min = (revenue * (1 - min_m) / cost_now - 1) * 100 if cost_now else 0

    if margin >= p["good_margin"]:
        verdict = "go"
    elif margin >= p["min_margin"]:
        verdict = "careful"
    else:
        verdict = "no"
    weak = coverage < p["min_coverage"]

    cats = []
    for c in CATS:
        if cat_t[c] or cat_r[c]:
            cats.append({"key": c, "label": CAT_LABELS[c], "tender": cat_t[c], "real": cat_r[c],
                         "diff": cat_t[c] - cat_r[c], "pct": (cat_t[c] / cat_r[c] - 1) * 100 if cat_r[c] else 0})
    top = max([max(x["tender"], x["real"]) for x in cats] or [1])
    for x in cats:
        x["w_t"], x["w_r"] = x["tender"] / top * 100, x["real"] / top * 100

    good, bad, risks = [], [], []
    for x in cats:
        if x["pct"] >= 5:
            good.append({"title": f"{x['label']} сметада яхши баҳоланган",
                         "text": f"Бозор нархидан {x['pct']:.0f}% юқори — бу қисмда захира бор.", "amount": x["diff"]})
        elif x["pct"] <= -5:
            bad.append({"title": f"{x['label']} сметада арзон баҳоланган",
                        "text": f"Бозор нархидан {abs(x['pct']):.0f}% паст — бу қисм зарарга ишлайди.", "amount": x["diff"]})
    if low_rows:
        bad.append({"title": f"{len(low_rows)} та ресурс нархи паст ёзилган",
                    "text": f"Сметадаги нархи бозордан камида {low:.0f}% арзон.",
                    "amount": sum(g["diff"] for g in low_rows)})
    if under:
        bad.append({"title": f"{len(under)} та ресурс ҳажми нормадан кам ёзилган",
                    "text": "Базадаги одатий нормага нисбатан. Етишмаган ҳажмни қурувчи ўз ҳисобидан олади.",
                    "amount": -under_money})
    if over:
        good.append({"title": f"{len(over)} та ресурс ҳажми нормадан кўп ёзилган",
                     "text": "Тўлов кўпроқ ҳажм учун — тежалса, фойдага қолади.", "amount": over_money})
    if missing["n"]:
        bad.append({"title": f"{missing['n']} та ресурснинг сметада нархи йўқ",
                    "text": "Уларнинг харажати тўланмайди деб ҳисобланди — ҳужжатдан аниқлаштиринг.",
                    "amount": -missing["amount"]})
    if gain_rows and not good:
        good.append({"title": f"{len(gain_rows)} та ресурс бозордан қиммат ёзилган",
                     "text": "Бу ресурсларда фойда бор.", "amount": sum(g["diff"] for g in gain_rows)})
    if cost_k and revenue:
        drop = cost_k * 0.10
        m10 = (profit - drop) / revenue * 100
        risks.append({"title": "Материаллар 10% қимматласа",
                      "text": f"Фойда {drop:,.0f} сўмга камаяди, фойда улуши {m10:.1f}% бўлади.".replace(",", " ").replace(".", ","),
                      "amount": -drop})
    if res_rows:
        heavy = sorted(res_rows, key=lambda g: -g["ra"])[:5]
        share = sum(g["ra"] for g in heavy) / max(1.0, sum(g["ra"] for g in res_rows)) * 100
        if share >= 40:
            risks.append({"title": f"5 та ресурс — харажатнинг {share:.0f}%",
                          "text": "Нархи ўзгарса, фойдага кучли таъсир қилади: " +
                                  ", ".join(g["name"][:40] for g in heavy[:3]) + ".", "amount": None})
    base_share = sum(it["ra"] or 0 for it in items if it["base"]) / max(1.0, sum(it["ra"] or 0 for it in items)) * 100
    if base_share >= 10:
        risks.append({"title": f"Харажатнинг {base_share:.0f}% — ресурс базаси нархи",
                      "text": "Бу нархлар ўртача (медиана) — етказиб берувчилардан нарх сўраб аниқлаштириш тавсия этилади.", "amount": None})
    if suspicious:
        risks.append({"title": f"{len(suspicious)} та ресурс нархи шубҳали — ҳисобга олинмади",
                      "text": "Сметада 1 сўм ёки бозор нархидан бир неча баравар фарқ (кўпинча бирлик хатоси). "
                              "Пастдаги рўйхатдан текширинг.", "amount": None})
    if unpriced["n"]:
        risks.append({"title": f"{unpriced['n']} та ресурснинг бозор нархи топилмади",
                      "text": "Солиштириб бўлмади — харажати сметадагидек олинди.", "amount": None})

    if weak and verdict == "go":
        verdict = "careful"  # фойда кўринади, лекин ресурсларнинг катта қисми солиштирилмади — ишонч паст
    texts = dict(VERDICTS)
    if weak:
        texts["careful"] = ("Эҳтиёт бўлинг", "Фойда кўринади, лекин ресурсларнинг бир қисми бозор нархи билан солиштирилмади.")
    report.update(
        status="ok", verdict={"key": verdict, "title": texts[verdict][0], "text": texts[verdict][1]}, weak=weak,
        tender_bez=tender_bez, tender_snds=tender_snds, revenue=revenue, cost=cost, profit=profit, margin=margin,
        rise=rise, cost_now=cost_now, reserve=reserve, reserve_min=reserve_min, coverage=coverage,
        cats=cats, good=good, bad=bad, risks=risks, suspicious=suspicious[:20], n_susp=len(suspicious),
        loss_rows=loss_rows[:15], gain_rows=gain_rows[:10], n_loss=len(loss_rows), n_gain=len(gain_rows),
        missing={"n": missing["n"], "amount": missing["amount"], "rows": missing["rows"][:15]},
        unpriced=unpriced, under=under[:20], over=over[:10], n_under=len(under), n_over=len(over),
        norms_checked=bool(base), under_money=under_money, over_money=over_money,
        factors_visible=[f for f in factors if f["visible"] and f["user_text"]],
        factors_hidden=any(not f["visible"] for f in factors),
        thresholds={"good": p["good_margin"], "min": p["min_margin"]},
        modes=sorted({d["mode"] for d in data if d["mode"]}), periods=_periods(data),
        js={"rev": revenue, "cost": cost_now, "good": p["good_margin"], "min": p["min_margin"],
            "reserve": reserve, "reserve_min": reserve_min, "weak": weak, "verdicts": texts},
    )
    return report

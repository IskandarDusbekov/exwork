"""«Нарх базаси» → «Статистика»: вилоятлар ва йиллар бўйича умумий кўрсаткичлар, харита учун ранглар.

* Ресурслар — шу вилоятда (йилда) ҳисобга кирадиган нархи бор ресурслар сони.
* Нархлар — киритилган нархлар (саватчадагиларсиз).
* Нарх даражаси — вилоят мамлакатга нисбатан қиммат ёки арзонми: бир ресурс бир йилда камида 2 вилоятда нархланган
  бўлса, вилоят нархи ўша ресурснинг вилоятлар бўйича медианасига бўлинади; вилоят кўрсаткичи — шу нисбатларнинг
  геометрик ўртачаси (−5% — мамлакатдан 5% арзон)."""
import math
from statistics import median

from django.db.models import Count, Max

from resbase.regions import REGIONS

from .models import CATEGORIES, Batch, Price, Stat
from .uzmap import CENTERS, HEIGHT, PATHS, WIDTH

METRICS = [("items", "Ресурслар", "шу вилоятда нархи бор ресурслар сони"),
           ("prices", "Нархлар", "киритилган нархлар сони"),
           ("index", "Нарх даражаси", "мамлакат бўйича ўртачага нисбатан: қиммат ёки арзон")]

SEQ = ["#cde2fb", "#9ec5f4", "#5598e7", "#256abf", "#104281"]  # кўк: оч → тўқ (миқдор)
DIV_LO = ["#184f95", "#2a78d6", "#6da7ec", "#b7d3f6"]  # арзон (кўк, кучлидан кучсизга)
DIV_MID = "#f0efec"
DIV_HI = ["#f6c9c8", "#ee9a99", "#e34948", "#b8302f"]  # қиммат (қизил, кучсиздан кучлига)
DIV_STEPS = [2, 5, 10, 20]  # % чегаралари
EMPTY = "#eef1f5"
DARK_TEXT = {SEQ[3], SEQ[4], DIV_LO[0], DIV_LO[1], DIV_HI[2], DIV_HI[3]}  # шу рангларда ёзув — оқ
SHORT = {"toshkent_sh": "Тошкент ш.", "toshkent_v": "Тошкент в.", "qoraqalpogiston": "Қорақалпоғистон"}
SMALL = {"toshkent_sh", "andijon", "namangan", "fargona", "sirdaryo"}  # харитада ёзув сиғмайди


def short(code, label) -> str:
    return SHORT.get(code, label.replace(" вилояти", ""))


def _bins(vmax, n=5) -> list:
    """Миқдор учун «чиройли» чегаралар: 0 < b1 < … < vmax."""
    if vmax <= 0:
        return []
    raw = vmax / n
    mag = 10 ** math.floor(math.log10(raw)) if raw >= 1 else 1
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    step = max(1, int(step))
    return [step * i for i in range(1, n)]


def seq_color(v, bins) -> str:
    if not v:
        return EMPTY
    for i, b in enumerate(bins):
        if v <= b:
            return SEQ[i]
    return SEQ[-1]


def div_color(pct) -> str:
    if pct is None:
        return EMPTY
    a = abs(pct)
    if a < DIV_STEPS[0]:
        return DIV_MID
    k = sum(1 for s in DIV_STEPS if a >= s) - 1  # 0..3
    return DIV_HI[k] if pct > 0 else DIV_LO[3 - k]


def price_index(year=None) -> tuple:
    """({вилоят: {"pct": %, "n": ресурслар}}, {вилоят: [(нисбат, item_id, йил, вилоят нархи, медиана)]})."""
    qs = Stat.objects.filter(item__deleted_at=None)
    if year:
        qs = qs.filter(year=year)
    groups = {}
    for item_id, y, region, mid, low in qs.values_list("item_id", "year", "region", "mid", "low"):
        v = mid if mid is not None else low
        if v and v > 0:
            groups.setdefault((item_id, y), []).append((region, float(v)))
    ratios = {}
    for (item_id, y), vals in groups.items():
        if len(vals) < 2:
            continue
        med = median(v for _, v in vals)
        for region, v in vals:
            ratios.setdefault(region, []).append((v / med, item_id, y, v, med))
    out = {}
    for region, rs in ratios.items():
        g = math.exp(sum(math.log(r) for r, *_ in rs) / len(rs))
        out[region] = {"pct": round((g - 1) * 100, 1), "n": len(rs)}
    return out, ratios


def overview(year=None, metric="items", region="") -> dict:
    st = Stat.objects.filter(item__deleted_at=None)
    pr = Price.objects.all()
    bt = Batch.objects.all()
    if year:
        st, pr, bt = st.filter(year=year), pr.filter(year=year), bt.filter(date__year=year)
    items = dict(st.values_list("region").annotate(c=Count("item", distinct=True)))
    prices = {r: (c, last) for r, c, last in pr.values_list("region").annotate(c=Count("id"), last=Max("date"))
              .values_list("region", "c", "last")}
    batches = dict(bt.values_list("region").annotate(c=Count("id")))
    index, ratios = price_index(year)

    vals = {"items": items, "prices": {r: c for r, (c, _) in prices.items()},
            "index": {r: d["pct"] for r, d in index.items()}}[metric]
    bins = _bins(max(vals.values(), default=0)) if metric != "index" else []
    regions = []
    for code, label in REGIONS:
        if code not in PATHS:
            continue
        v = vals.get(code)
        color = div_color(v) if metric == "index" else seq_color(v, bins)
        regions.append({
            "code": code, "label": label, "short": short(code, label), "path": PATHS[code],
            "cx": CENTERS[code][0], "cy": CENTERS[code][1], "small": code in SMALL,
            "items": items.get(code, 0), "prices": prices.get(code, (0, None))[0], "last": prices.get(code, (0, None))[1],
            "batches": batches.get(code, 0), "index": index.get(code), "value": v, "color": color,
            "ink": "#ffffff" if color in DARK_TEXT else "#0f172a", "on": code == region})
    rank = sorted([r for r in regions if r["value"] is not None and (metric == "index" or r["value"])],
                  key=lambda r: -r["value"])
    if metric == "index":
        span = max((abs(r["value"]) for r in rank), default=0) or 1
        for r in rank:
            r["bar"] = round(abs(r["value"]) / span * 50, 1)  # ярим кенглик — 0 ўртада
    else:
        top = rank[0]["value"] if rank else 1
        for r in rank:
            r["bar"] = round(r["value"] / top * 100, 1)
    if metric == "index":
        s = DIV_STEPS
        legend = ([{"color": DIV_LO[0], "label": f"−{s[3]}%+"}]
                  + [{"color": DIV_LO[i], "label": f"−{s[3 - i]}…{s[4 - i]}"} for i in (1, 2, 3)]
                  + [{"color": DIV_MID, "label": f"±{s[0]}%"}]
                  + [{"color": DIV_HI[i], "label": f"+{s[i]}…{s[i + 1]}"} for i in (0, 1, 2)]
                  + [{"color": DIV_HI[3], "label": f"+{s[3]}%+"}])
    else:
        edges = [0] + bins
        legend = [{"color": SEQ[i], "label": (f"{edges[i] + 1}–{edges[i + 1]}" if i < len(bins) else f"> {edges[-1]}")}
                  for i in range(len(bins) + 1)] if bins else []
    return {"regions": regions, "rank": rank, "legend": legend, "width": WIDTH, "height": HEIGHT,
            "covered": sum(1 for r in regions if r["items"]), "ratios": ratios}


def years() -> list:
    return sorted(set(Stat.objects.filter(item__deleted_at=None).values_list("year", flat=True)), reverse=True)


def matrix() -> dict:
    """Вилоят × йил: ресурслар сони (иссиқлик жадвали)."""
    data = {(r, y): c for r, y, c in Stat.objects.filter(item__deleted_at=None).values_list("region", "year")
            .annotate(c=Count("item", distinct=True)).values_list("region", "year", "c")}
    ys = sorted({y for _, y in data})
    bins = _bins(max(data.values(), default=0))
    rows = []
    for code, label in REGIONS:
        if code not in PATHS:
            continue
        cells = []
        for y in ys:
            c = data.get((code, y), 0)
            col = seq_color(c, bins)
            cells.append({"year": y, "n": c, "color": col, "ink": "#ffffff" if col in DARK_TEXT else "#0f172a"})
        rows.append({"code": code, "label": short(code, label), "full": label, "cells": cells,
                     "total": sum(c["n"] for c in cells)})
    return {"years": ys, "rows": rows}


def by_year() -> list:
    """Йиллар бўйича: нархлар ва ресурслар (устунли график)."""
    rows = list(Price.objects.values("year").annotate(prices=Count("id"), items=Count("item", distinct=True),
                                                       regions=Count("region", distinct=True)).order_by("year"))
    top = max((r["prices"] for r in rows), default=0) or 1
    for r in rows:
        r["h"] = round(r["prices"] / top * 100, 1)
    return rows


def region_detail(code, year, ratios) -> dict:
    """Танланган вилоят: тоифалар бўйича ресурслар, мамлакатдан энг қиммат ва энг арзон ресурслари."""
    from .models import Item
    st = Stat.objects.filter(item__deleted_at=None, region=code)
    if year:
        st = st.filter(year=year)
    cats = dict(st.values_list("item__category").annotate(c=Count("item", distinct=True)))
    total = sum(cats.values())
    top = max(cats.values(), default=0) or 1
    rs = sorted(ratios.get(code, []))
    names = dict(Item.objects.filter(pk__in={i for _, i, *_ in rs[:5] + rs[-5:]}).values_list("pk", "name"))

    def pack(lst):
        return [{"pct": round((r - 1) * 100), "item": i, "name": names.get(i, "—"), "year": y, "v": v, "med": med}
                for r, i, y, v, med in lst]
    return {"cats": [{"k": k, "label": label, "n": cats.get(k, 0), "w": round(cats.get(k, 0) / top * 100, 1)}
                     for k, label in CATEGORIES if cats.get(k)],
            "total": total, "n": len(rs), "dear": pack([x for x in reversed(rs[-5:]) if x[0] > 1]),
            "cheap": pack([x for x in rs[:5] if x[0] < 1])}

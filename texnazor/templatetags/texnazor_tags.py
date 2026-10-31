import html as _html

from django import template
from django.utils.safestring import mark_safe

from jobs.templatetags.narx import icon, qty, readable

register = template.Library()


@register.filter
def ming(v):
    """Ф-3 dagidek ming so'mda: 18 390 330 726,71 -> «18 390 330,727»."""
    try:
        v = float(v or 0) / 1000
    except (TypeError, ValueError):
        return ""
    return f"{0.0 if abs(v) < 0.0005 else v:,.3f}".replace(",", " ").replace(".", ",")


@register.filter
def foiz(v):
    """0.8071 -> «80,72%»."""
    try:
        return f"{float(v or 0) * 100:.2f}".replace(".", ",") + "%"
    except (TypeError, ValueError):
        return ""


@register.filter
def pul(v):
    """Pul — har doim tiyingacha (Ф-2 / Ф-3 dagidek): 2 862 982,69; butun bo'lsa ham 1 000,00. Yaxlitlanmaydi."""
    if v is None or v == "":
        return ""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(v) < 0.005:
        v = 0.0  # -0,00 bo'lmasin
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")


# ===================== офёрта жадвали қаторлари =====================
# Бутун офёртада 18 мингдан ортиқ қатор бўлади: шаблон ``include``и қатор бошига ~0,4 мс — бу ерда ўн баробар тез.
# Ўзгармас изоҳлар (title) ҳар қаторда такрорланмайди — саҳифа уларни сичқонча келганда қўяди (_ofyorta_js.html).

def _e(v) -> str:
    return _html.escape(str(v)) if v is not None else ""


def xl(t) -> str:
    """Jadvalda nom ofyortadagidek (Excel'dagi katta harflar) — faqat ortiqcha bo'shliqlarsiz."""
    return " ".join(str(t or "").split())


def _q(v) -> str:
    return _e(qty(v))


def _res_row(x, it, closed: bool, flags: str) -> str:
    r = x["r"]
    return (f'<tr class="tn-r tn-c-{x["tone"]}{flags}" data-w="{it.pk}">{"" if closed else "<td class=tn-ck></td>"}'
            f'<td class="tn-g">{r.excel_row or ""}</td><td class="tn-n">{_e(r.num)}</td><td class="tn-code">{_e(r.code)}</td>'
            f'<td class="tn-name">{_e(xl(r.name))}</td><td class="tn-unit">{_e(r.unit)}</td>'
            f'<td class="num tn-norm">{_q(r.qty)}</td><td class="num">{_q(x["total"])}</td>'
            f'<td class="num">{_q(x["before"]) if x["before"] else ""}</td><td class="num tn-r-this">{_q(x["this"]) if x["this"] else ""}</td>'
            f'<td class="num">{_q(x["left"])}</td></tr>')


def item_row(r: dict, period, warn: dict, fixes: dict) -> str:
    """Иш (ёки мустақил ресурс) қатори ва остида унинг ресурслари (ёпиқ)."""
    it, closed = r["item"], period.closed
    wt, num, unit = warn.get(it.pk), _e(it.num), _e(it.unit)
    # is-prev — oldingi oylarda tugagan, has-this — shu oyda yozilgan (jadval filtri shular bo'yicha)
    flags = (" is-prev" if r["prev"] else "") + (" has-this" if r["this"] else "")
    cls = ("tn-w" + (" tn-free" if it.kind != "work" else "") + (" is-done" if r["pct"] >= 99.999 else "") + (" has-warn" if wt else "")
           + flags)
    out = [f'<tr class="{cls}" data-item="{it.pk}" data-s="{it.section_id or ""}" id="tn-i-{it.pk}" data-name="{num}. {_e(readable(it.name))}" '
           f'data-qty="{_q(it.qty)}" data-unit="{unit}" data-done="{_q(r["done"])}">']
    if not closed:
        out.append('<td class="tn-ck"></td>' if r["prev"] else
                   f'<td class="tn-ck"><input type="checkbox" class="tn-sel" aria-label="{num} — белгилаш"></td>')
    out.append(f'<td class="tn-g">{it.excel_row or ""}</td><td class="tn-n">')
    if r["res"]:
        out.append(f'<button type="button" class="tn-rt" title="Ресурслари ({len(r["res"])} та) — очиш / ёпиш" aria-label="{num} ресурслари">'
                   f'{icon("chevron-right", "h-3.5 w-3.5")}</button>')
    out.append(f'{num}</td><td class="tn-code">{_e(it.code)}</td><td class="tn-name">')
    if wt:
        out.append(f'<span class="tn-warn" title="{_e(wt)}">⚠ офёртада хато</span> ')
    if r["prev"]:
        out.append(f'<span class="tn-prev">✓ {_e(r["prev"])}да тугаган</span> ')
    out.append(_e(xl(it.name)))
    if it.kind == "free":
        out.append(f' <span class="tn-tag" title="Офёртада алоҳида қатор (ресурслари йўқ): ҳажм × нарх">{_e((r["cat"] or "материал").lower())}</span>')
        if not it.price:
            out.append(' <span class="tn-tag tn-tag-0" title="Офёртада нархи 0 — белгиланган ҳажм пулга айланмайди">нархи 0</span>')
    elif it.kind == "extra":
        out.append(' <span class="tn-tag" title="Сводкада алоҳида қўшилган қатор">сводкадан</span>')
    if it.qty < 0:
        base = f' — {_e(r["base"].num)}-ишдан' if r["base"] else ""
        out.append(' <span class="tn-tag tn-tag-minus" title="Офёртада ҳажми манфий: бу қатор асосий ишдан айирилади (масалан қалинлик '
                   'камайтирилган). Ҳажмни мусбат ёзсангиз ҳам манфий сақланади; асосий иш бажарилса, буни ҳам белгиланг.">'
                   f'айириш{base}</span>')
    if r.get("where"):
        b, s = r["where"]
        out.append(f'<div class="tn-where">{_e(readable(b)) + " › " if b else ""}{_e(readable(s))}</div>')
    out.append(f'</td><td class="tn-unit">{unit}</td><td class="num tn-qcell tn-norm tn-norm-w">')
    fx = fixes.get(it.pk)
    if fx:
        out.append(f'<span class="tn-fixed" title="Тузатилган: {_q(fx.old_qty)} → {_q(fx.new_qty)}. Асос: {_e(fx.basis)}">тузатилган</span>')
    out.append(f'{_q(it.qty)}<button type="button" class="tn-fix" aria-label="{num} — шартнома ҳажмини тузатиш">'
               f'{icon("pencil", "h-3.5 w-3.5")}</button></td><td></td>'
               f'<td class="num tn-dim">{_before(r)}</td>'
               f'<td class="tn-cin"><input type="text" inputmode="decimal" class="tn-in num" value="{_q(r["this"]) if r["this"] else ""}" '
               f'placeholder="{"тугаган" if r["prev"] else "ёзинг"}"{" disabled" if closed or r["prev"] else ""} '
               f'aria-label="{num} — {_e(period.title)} ҳажми"></td><td class="num">')
    if not it.qty:
        out.append('<span class="tn-dim" title="Шартномада ҳажми 0">—</span>')
    elif r["over"]:
        out.append(f'<span class="tn-bad">{_q(r["left"])}</span>')
    elif r["complete"]:
        out.append('<span class="tn-ok">✓ 0</span>')
    else:
        out.append(_q(r["left"]))
    out.append("</td></tr>")
    out.extend(_res_row(x, it, closed, flags) for x in r["res"])
    return "".join(out)


def _before(r) -> str:
    """«Аввал» катаги: олдинги ойлардаги ҳажм ва қисман бўлса — неча фоизи (60% — қолгани шу ойга)."""
    if not r["before"]:
        return ""
    p = r["before_pct"]
    if r["prev"] or p >= 99.95:
        return _q(r["before"])
    return _q(r["before"]) + (f'<small class="tn-bp">{p:.0f}%</small>' if p >= 1 else '<small class="tn-bp">&lt;1%</small>')


def head_row(kind: str, s, period, cols: int, st=None, block: str = "") -> str:
    """Бутун офёртада сарлавҳа қатори: блок («НА АС БЛОК-1»), оралиқ сарлавҳа ёки бўлим (рақамлари ва «100%» тугмаси)."""
    if kind == "block":
        return f'<tr class="tn-bh" data-s="{s.pk}"><td colspan="{cols}">{_e(xl(block))}</td></tr>'
    if kind == "group":
        return f'<tr class="tn-gh" data-s="{s.pk}"><td colspan="{cols}">{_e(xl(s.title))}</td></tr>'
    pct = st["pct"]
    fill = "" if period.closed else (f'<button type="button" class="tn-fill" data-sec="{s.pk}" title="Бўлимдаги ҳамма иш тўлиқ '
                                     f'бажарилди деб белгилаш (етмаган қисми {_e(period.title)}га ёзилади)">100%</button>')
    return (f'<tr class="tn-sh{" is-full" if st["contract"] and st["before"] >= st["contract"] * 0.9995 else ""}" id="tn-s-{s.pk}" data-section="{s.pk}" data-s="{s.pk}"><td colspan="{cols}"><div class="tn-sh-in">'
            f'<span class="tn-sh-t">{_e(xl(s.title))}</span><span class="tn-sh-n">{s.items_count} та</span>'
            f'<span class="tn-sh-r">{_e(period.title)}да: <b class="num tn-sec-this">{pul(st["this"])}</b>'
            f'<span class="tn-meter"><span class="tn-sec-bar" style="width:{min(100.0, pct):.3f}%;{"min-width:3px" if pct > 0 else ""}"></span></span>'
            f'<b class="num tn-sec-pct">{pct:.0f}%</b>{fill}</span></div></td></tr>')


# ---- «Ҳамма ойлар»: ҳар ой алоҳида устун, охирида жами ва қолдиқ (ўқиш учун; ҳажм «шу ой» кўринишида ёзилади) ----
def _som(v) -> str:
    return f"{v:,.0f}".replace(",", " ") if v and abs(v) >= 0.5 else ""


def _left(r) -> str:
    it = r["item"]
    if not it.qty:
        return '<span class="tn-dim">—</span>'
    if r["over"]:
        return f'<span class="tn-bad">{_q(r["left"])}</span>'
    if r["complete"]:
        return '<span class="tn-ok">✓ 0</span>'
    return _q(r["left"])


def _blk4(a: str, b: str, price: str, money: str, cls: str = "") -> str:
    """Офёрта блоки — Ф-2 dagidek 4 ustun: на единицу (ishda — hajm, resursda — me'yor), по проектным, цена, сумма."""
    return (f'<td class="num tn-mc{cls}">{a}</td><td class="num tn-mf">{b}</td>'
            f'<td class="num tn-mp">{price}</td><td class="num tn-ms">{money}</td>')


def _blk2(q: str, money: str, sel: bool = False, cls: str = "") -> str:
    """Oy / jami / qoldiq bloki — 2 ustun: miqdor (ishda — hajm, resursda — me'yor × hajm) va summa.
    Me'yor va narx har blokda bir xil — ular faqat «Офёрта» blokida (jadval yonga surilmasin)."""
    on = " is-sel" if sel else ""
    return f'<td class="num tn-mc{on}{cls}">{q}</td><td class="num tn-ms{on}">{money}</td>'


def item_row_months(r: dict, blocks, vals: dict, period) -> str:
    """«Ҳамма ойлар» qatori: ofyorta (4 ustun), har oy yoki oylar guruhi, qoldiq (2 ustundan) — Ф-2 dagidek.
    ``blocks`` — [(oylar id lari, tanlangan oymi)]: bitta oy yoki «Олдинги ойлар» (bir nechtasi jamlangan)."""
    it = r["item"]
    num, unit = _e(it.num), _e(it.unit)
    total, uc = sum(vals.values()), it.unit_cost
    free = it.kind != "work"
    flags = (" is-prev" if r["prev"] else "") + (" has-this" if r["this"] else "")
    cls = "tn-w" + (" tn-free" if free else "") + (" is-done" if r["pct"] >= 99.999 else "") + flags
    edit = not period.closed  # tanlangan ochiq oy — shu jadvalda ham yoziladi va belgilanadi (ish rejimidagidek)
    out = [f'<tr class="{cls}" data-item="{it.pk}" data-s="{it.section_id or ""}" id="tn-i-{it.pk}" data-qty="{_q(it.qty)}" data-unit="{unit}">'
           f'<td class="tn-g">{it.excel_row or ""}</td><td class="tn-n">']
    if edit and not r["prev"]:
        out.append(f'<input type="checkbox" class="tn-sel" aria-label="{num} — белгилаш">')
    if r["res"]:
        out.append(f'<button type="button" class="tn-rt" title="Ресурслари ({len(r["res"])} та) — очиш / ёпиш" aria-label="{num} ресурслари">'
                   f'{icon("chevron-right", "h-3.5 w-3.5")}</button>')
    out.append(f'{num}</td><td class="tn-code">{_e(it.code)}</td><td class="tn-name">')
    if r["prev"]:
        out.append(f'<span class="tn-prev">✓ {_e(r["prev"])}да тугаган</span> ')
    out.append(_e(xl(it.name)))
    if it.qty < 0:
        out.append(' <span class="tn-tag tn-tag-minus">айириш</span>')
    pct = 100 * total / it.qty if it.qty else 0.0
    tail = f'<small class="tn-bp">{pct:.0f}%</small>' if total and 0.5 <= pct < 99.95 else ""
    out.append(f'</td><td class="tn-unit">{unit}</td>')
    out.append(_blk4(_q(it.qty), "", _som(it.price) if free else "", _som(it.amount), cls=" tn-mq"))
    for pids, sel in blocks:
        v = sum(vals.get(pid, 0.0) for pid in pids)
        q = _q(v) if v else ""
        if sel and edit:  # yozish katagi — ish rejimidagi bilan bir xil (сон, фоиз, Enter, ↑ ↓)
            q = (f'<input type="text" inputmode="decimal" class="tn-in num" value="{q}" placeholder="{"тугаган" if r["prev"] else "ёзинг"}"'
                 f'{" disabled" if r["prev"] else ""} aria-label="{num} — {_e(period.title)} ҳажми">')
        out.append(_blk2(q, _som(v * uc) if v else "", sel, " tn-cin" if sel and edit else ""))
    open_ = it.qty and not r["complete"]
    out.append(_blk2(_left(r) + tail, _som(r["left"] * uc) if open_ else "", cls=" tn-ml"))  # qisman — necha % bajarilgan
    out.append("</tr>")
    for x in r["res"]:
        rr = x["r"]
        out.append(f'<tr class="tn-r tn-c-{x["tone"]}{flags}" data-w="{it.pk}"><td class="tn-g">{rr.excel_row or ""}</td>'
                   f'<td class="tn-n">{_e(rr.num)}</td><td class="tn-code">{_e(rr.code)}</td><td class="tn-name">{_e(xl(rr.name))}</td>'
                   f'<td class="tn-unit">{_e(rr.unit)}</td>')
        out.append(_blk4(_q(rr.qty), _q(x["total"]), _som(rr.price), _som(rr.price * x["total"])))

        def blk(v, sel=False):
            return _blk2(_q(rr.qty * v), _som(rr.price * rr.qty * v), sel) if v else _blk2("", "", sel)

        for pids, sel in blocks:
            out.append(blk(sum(vals.get(pid, 0.0) for pid in pids), sel))
        out.append(blk(r["left"] if open_ else 0))
        out.append("</tr>")
    return "".join(out)


def head_row_months(s, blocks, money: dict, st: dict) -> str:
    """Бўлим сарлавҳаси: ҳар блокнинг «Сумма» устунида — шартнома, ойлар ва қолдиқ (сўмда)."""
    cum = sum(money.values())
    full = " is-full" if st["contract"] and st["before"] >= st["contract"] * 0.9995 else ""

    def blk(v, sel=False):
        on = " is-sel" if sel else ""
        return f'<td class="tn-b0{on}"></td><td class="num tn-shm{on}">{_som(v)}</td>'

    return (f'<tr class="tn-sh tn-sh-m{full}" id="tn-s-{s.pk}" data-section="{s.pk}" data-s="{s.pk}"><td colspan="4"><div class="tn-sh-in">'
            f'<span class="tn-sh-t">{_e(xl(s.title))}</span><span class="tn-sh-n">{s.items_count} та</span></div></td><td></td>'
            f'<td class="tn-b0"></td><td></td><td></td><td class="num tn-shm">{_som(st["contract"])}</td>'
            + "".join(blk(sum(money.get(pid, 0.0) for pid in pids), sel) for pids, sel in blocks)
            + blk(st["contract"] - cum) + "</tr>")


# ---- «Ф-2» ko'rinishi: faqat shu oyda yozilgan ishlar, resurslari doim ochiq — Excel'dagi Ф-2 varag'idek ----
def item_row_f2(r: dict) -> str:
    it, v = r["item"], r["this"] or 0.0
    num, free = _e(it.num), it.kind != "work"
    out = [f'<tr class="tn-w tn-wf{" tn-free" if free else ""} has-this" data-item="{it.pk}" data-s="{it.section_id or ""}" id="tn-i-{it.pk}">'
           f'<td class="tn-g">{it.excel_row or ""}</td><td class="tn-n">{num}</td><td class="tn-code">{_e(it.code)}</td>'
           f'<td class="tn-name">{_e(xl(it.name))}</td><td class="tn-unit">{_e(it.unit)}</td><td class="num tn-mq">{_q(v)}</td><td></td>'
           f'<td class="num tn-mp">{_som(it.price) if free else ""}</td><td class="num tn-ms">{_som(v * it.price) if free else ""}</td></tr>']
    for x in r["res"]:
        rr = x["r"]
        q = rr.qty * v
        out.append(f'<tr class="tn-r is-shown tn-c-{x["tone"]} has-this" data-w="{it.pk}"><td class="tn-g">{rr.excel_row or ""}</td>'
                   f'<td class="tn-n">{_e(rr.num)}</td><td class="tn-code">{_e(rr.code)}</td><td class="tn-name">{_e(xl(rr.name))}</td>'
                   f'<td class="tn-unit">{_e(rr.unit)}</td><td class="num">{_q(rr.qty)}</td><td class="num">{_q(q)}</td>'
                   f'<td class="num tn-mp">{_som(rr.price)}</td><td class="num tn-ms">{_som(rr.price * q) if rr.price else ""}</td></tr>')
    return "".join(out)


def head_row_f2(s, cols: int, block: bool = False) -> str:
    if block:
        return f'<tr class="tn-bh" data-s="{s.pk}"><td colspan="{cols}">{_e(xl(block_title(s.title)))}</td></tr>'
    return (f'<tr class="tn-sh tn-sh-f" id="tn-s-{s.pk}" data-section="{s.pk}" data-s="{s.pk}"><td colspan="{cols}">'
            f'<div class="tn-sh-in"><span class="tn-sh-t">{_e(xl(s.title))}</span></div></td></tr>')


def block_title(t: str) -> str:
    from ..services import block_label
    return block_label(t)


def svodka_f2(steps, chain: dict, cats: dict, hours: dict, cols: int) -> str:
    """Ф-2 охири: «JAMI» (турлар бўйича, чел-ч / маш-ч соатлари билан) ва сводка — «Сумма» устунида."""
    out = [f'<tr class="tn-bh tn-svh"><td colspan="{cols}">JAMI ва сводка</td></tr>']
    for key, label, extra in (("J", "Иш ҳақи (чел-ч)", hours.get("J")), ("K", "Машина ва механизмлар (маш-ч)", hours.get("K"))):
        out.append(f'<tr class="tn-sv"><td colspan="5" class="tn-svl">{label}</td><td class="num">{_q(round(extra, 4)) if extra else ""}</td>'
                   f'<td></td><td></td><td class="num tn-svv">{_som(cats.get(key))}</td></tr>')
    for key, label in steps:
        b = " is-b" if key in SV_BOLD else ""
        out.append(f'<tr class="tn-sv{" is-c" if key == "contractor" else ""}{b}"><td colspan="{cols - 1}" class="tn-svl">{_e(label)}</td>'
                   f'<td class="num tn-svv">{_som(chain[key])}</td></tr>')
    return "".join(out)


SV_BOLD = {"direct", "direct_overhead", "base", "no_vat", "total", "contractor"}


def svodka_months(steps, cols_label: str, contract: dict, chains: list, sels: list, left: dict) -> str:
    """«Ҳамма ойлар» oxiri — Ф-2 dagidek «JAMI» va svodka: har blok «Сумма» ustunida (ofyorta, oylar, qoldiq)."""
    out = [f'<tr class="tn-bh tn-svh"><td colspan="{cols_label}">Жами ва сводка — ҳар блок бўйича (Ф-2 даги каби)</td></tr>']
    for key, label in steps:
        b = " is-b" if key in SV_BOLD else ""
        cells = [f'<td colspan="4" class="tn-svl">{_e(label)}</td><td></td>',
                 f'<td class="tn-b0"></td><td></td><td></td><td class="num tn-svv">{_som(contract.get(key, 0.0))}</td>']
        for ch, sel in zip(chains, sels):
            on = " is-sel" if sel else ""
            cells.append(f'<td class="tn-b0{on}"></td><td class="num tn-svv{on}">{_som(ch[key])}</td>')
        cells.append(f'<td class="tn-b0"></td><td class="num tn-svv">{_som(left[key])}</td>')
        out.append(f'<tr class="tn-sv{" is-c" if key == "contractor" else ""}{b}">{"".join(cells)}</tr>')
    return "".join(out)


@register.simple_tag
def tn_rows(rows, period, warn, fixes):
    """Қидирув натижаси жадвалининг қаторлари (``_panel.html``)."""
    return mark_safe("".join(item_row(r, period, warn or {}, fixes or {}) for r in rows))


@register.filter
def foiz_son(v):
    """Forma uchun: 0.0032 -> «0.32», 0.12 -> «12»."""
    try:
        return f"{round(float(v or 0) * 100, 4):g}"
    except (TypeError, ValueError):
        return ""

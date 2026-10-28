import re

from django import template

register = template.Library()

# Rus Excel'da xatolar shunday ko'rinadi
_EXCEL_ERRORS_RU = {
    "#VALUE!": "#ЗНАЧ!",
    "#DIV/0!": "#ДЕЛ/0!",
    "#REF!": "#ССЫЛКА!",
    "#NAME?": "#ИМЯ?",
    "#NUM!": "#ЧИСЛО!",
    "#N/A": "#Н/Д",
    "#NULL!": "#ПУСТО!",
}


_CAPS_WORD = re.compile(r"[A-ZА-ЯЁЎҚҒҲ]{4,}")
_CAPS_SHORT = re.compile(r"\b(?:НА|В|И|ПО|С|К|ОТ|ДО|ИЗ|ЗА|ДЛЯ|ПРИ|БЕЗ)\b")


@register.filter
def readable(v) -> str:
    """«ПОДАЮЩИЙ  ТРУБОПРОВОД» -> «Подающий трубопровод»: бош ҳарфли узун сўзлар кичик, ортиқча бўшлиқ йўқ
    (қисқартмалар — ЭО, ЛВС — ўзича қолади)."""
    s = " ".join(str(v or "").split())
    if _CAPS_WORD.search(s):
        s = _CAPS_WORD.sub(lambda m: m.group(0).lower(), s)
        s = _CAPS_SHORT.sub(lambda m: m.group(0).lower(), s)  # «НА», «ДЛЯ» каби боғловчилар ҳам
    i = next((n for n, ch in enumerate(s) if ch.isalpha()), None)
    return s if i is None else s[:i] + s[i].upper() + s[i + 1:]


@register.filter
def file_title(name) -> str:
    """Файл номидан ўқиладиган ном: «104192_ALL_03-08_ПОДАЮЩИЙ ТРУБОПРОВОД.xls» -> «03-08 Подающий трубопровод»."""
    stem = re.sub(r"\.(xlsx|xlsm|xls)$", "", str(name or ""), flags=re.I)
    stem = re.sub(r"^\d{4,}_(?:[A-Za-z]{2,4}_)*", "", stem)  # дастур коди «104192_ALL_»
    return readable(stem.replace("_", " ").strip(" .")) or str(name or "")


@register.filter
def is_error(v) -> bool:
    return isinstance(v, str) and v in _EXCEL_ERRORS_RU


@register.filter
def is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@register.filter
def som(v):
    """Excel'dagi ``#,##0`` kabi: 1 234 567. Xato -> #ЗНАЧ!, matn o'zicha."""
    if v is None or v == "":
        return ""
    if is_number(v):
        if round(v, 2) != round(v):  # 457,60 — Excel'даги #,##0.00 каби, бутун сон касрсиз
            return f"{v:,.2f}".replace(",", " ").replace(".", ",")
        return f"{round(v):,}".replace(",", " ")
    s = str(v)
    return _EXCEL_ERRORS_RU.get(s, s)


# Қатор фони — Excel'даги LOG ранглари билан бир хил маънода
_ROW_BG = {
    "FUZZY": "bg-amber-100",
    "AMBIGUOUS": "bg-orange-100",
    "NOT_FOUND": "bg-rose-100",
    "NO_PRICE": "bg-rose-100",
    "WORK_HEADER": "bg-slate-50",
    "MACHINIST": "bg-slate-50",
}


@register.filter
def row_bg(status) -> str:
    return _ROW_BG.get(status or "", "")


_STATUS_LABELS = {
    "FUZZY": "ўхшаш",
    "AMBIGUOUS": "ШАБҲАЛИ",
    "UNIT_MISMATCH": "бирлик бошқа",
    "NOT_FOUND": "ТОПИЛМАДИ",
    "NO_PRICE": "нархсиз",
}


@register.filter
def status_label(status) -> str:
    return _STATUS_LABELS.get(status or "", status or "")


@register.filter
def qty(v):
    """Miqdor: 39,7 / 0,025 / 1 588 (Excel'dagi kabi vergul bilan, ortiqcha nollarsiz)."""
    from core.svodka import parse_text_number
    if v is None or v == "":
        return ""
    n = v if is_number(v) else parse_text_number(str(v))
    if n is None:
        return str(v)
    text = f"{n:,.6f}".rstrip("0").rstrip(".")
    return text.replace(",", " ").replace(".", ",")


@register.filter
def pct(v):
    return f"{v * 100:.2f}%".replace(".", ",") if is_number(v) else ""


_CAT_CLASS = {
    "I": "bg-sky-100 text-sky-800",
    "J": "bg-violet-100 text-violet-800",
    "K": "bg-stone-200 text-stone-800",
    "L": "bg-cyan-100 text-cyan-800",
    "M": "bg-fuchsia-100 text-fuchsia-800",
}
_CAT_LABEL = {"I": "Иш ҳақи", "J": "Машина", "K": "Материал", "L": "Кабель", "M": "Ускуна"}


@register.filter
def cat_class(c) -> str:
    return _CAT_CLASS.get(c or "", "bg-slate-100 text-slate-700")


@register.filter
def cat_label(c) -> str:
    return _CAT_LABEL.get(c or "", "")


@register.filter
def pct_input(v):
    """0.1727 -> «17,27» (forma maydoni uchun)."""
    from core.svodka import percent_text
    return percent_text(v) if is_number(v) else ""


@register.filter
def score(v):
    return f"{v:.3f}" if is_number(v) and v != 1 else ""


# ===================== SVG ikonkalar (chiziqli, 24×24, rang — matn rangi) =====================
_ICONS = {
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h8"/>',
    "folder": '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "check-circle": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "alert": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    "alert-circle": '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
    "tag": '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L2 12V2h10l8.6 8.6a2 2 0 0 1 0 2.8z"/><path d="M7 7h.01"/>',
    "pencil": '<path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/>',
    "columns": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M15 3v18"/>',
    "sliders": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-3"/>',
    "calculator": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M16 14v4M8 18h.01M12 18h.01"/>',
    "table": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>',
    "arrow-left": '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    "arrow-right": '<path d="M5 12h14M12 5l7 7-7 7"/>',
    "swap": '<path d="m8 3-4 4 4 4M4 7h16M16 21l4-4-4-4M20 17H4"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "tree": '<rect x="9" y="2" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="16" y="16" width="6" height="6" rx="1"/><path d="M5 16v-3h14v3M12 8v5"/>',
    "layers": '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
    "building": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4M8 6h.01M12 6h.01M16 6h.01M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M16 14h.01"/>',
    "undo": '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-15-6.7L3 13"/>',
    "filter": '<path d="M22 3H2l8 9.5V19l4 2v-8.5z"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "loader": '<path d="M21 12a9 9 0 1 1-6.2-8.6"/>',
    "folder-open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.2 10H20a2 2 0 0 1 1.9 2.5l-1.5 6A2 2 0 0 1 18.5 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.7.9l.8 1.2a2 2 0 0 0 1.7.9H18a2 2 0 0 1 2 2v2"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "sheet": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h2M14 13h2M8 17h2M14 17h2"/>',
    "minus": '<path d="M5 12h14"/>',
    "external": '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "x-circle": '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    "play": '<path d="M7 5v14l11-7z"/>',
    "trash": '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
    "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5"/><path d="M3 12c0 1.7 4 3 9 3s9-1.3 9-3"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "logo": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 8h6M7 12h10M7 16h4M15 16h2"/>',
}
_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round" class="{cls}" aria-hidden="true">{body}</svg>')


@register.simple_tag
def icon(name, cls="h-4 w-4 shrink-0", width="2"):
    """``{% icon "download" "h-5 w-5" %}`` — чизиқли SVG белги (emoji ўрнига)."""
    from django.utils.safestring import mark_safe
    return mark_safe(_SVG.format(w=width, cls=cls, body=_ICONS[name]))


@register.filter
def get_item(d, key):
    return d.get(key) if hasattr(d, "get") else None


@register.filter
def hsize(n):
    """Файл ҳажми: 1 536 → «1,5 КБ»."""
    if n is None or n == "":
        return "—"
    n = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return (f"{n:.0f} {unit}" if unit == "Б" or n >= 100 else f"{n:.1f} {unit}").replace(".", ",")
        n /= 1024

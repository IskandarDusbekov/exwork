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

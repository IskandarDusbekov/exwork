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


@register.filter
def pct(v):
    return f"{v * 100:.2f}%".replace(".", ",") if is_number(v) else ""


@register.filter
def score(v):
    return f"{v:.3f}" if is_number(v) and v != 1 else ""

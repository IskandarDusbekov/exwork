from django import template

register = template.Library()


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

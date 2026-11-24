"""Ҳар саҳифада керак бўладиган смета­чи созламалари (5-босқич): ``<html>`` класслари шулардан."""


def ui(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    if not hasattr(request, "_ui_prefs"):
        from .services import ui_prefs_of
        request._ui_prefs = ui_prefs_of(user)
    from .site import side_menu
    from billing import services as bs
    on = bs.enabled()
    mine = on and not getattr(request, "viewing_owner", None)
    return {"ui": request._ui_prefs, "side": side_menu(), "billing_on": on,
            "my_balance": bs.account(user).total if mine else None,
            "my_plan": _my_plan(user) if mine and not bs.exempt(user) else None}


def _my_plan(user) -> dict:
    """Сарлавҳадаги тариф тугмаси учун: номи, шу ой қолган қатор, объектлар, баланс, муддат."""
    from django.utils import timezone
    from billing import services as bs
    p = bs.plan(user)
    end = p.sub.end if p.sub else p.end
    left_pct = round(100 * p.rows_left / p.rows) if p.rows else 0
    return {"name": p.tariff.name if p.tariff else "—", "paid": bool(p.sub), "end": end, "month_end": p.end,
            "days": max(0, (end - timezone.now()).days), "rows": p.rows, "rows_used": p.rows_used,
            "rows_left": p.rows_left, "left_pct": left_pct, "low": 0 < left_pct < 15, "empty": p.rows_left == 0,
            "objects": p.objects, "objects_used": p.objects_used, "balance": bs.account(user).total,
            "row_price": bs.row_price()}

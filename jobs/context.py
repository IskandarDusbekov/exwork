"""Ҳар саҳифада керак бўладиган смета­чи созламалари (5-босқич): ``<html>`` класслари шулардан."""


def ui(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    if not hasattr(request, "_ui_prefs"):
        from .services import ui_prefs_of
        request._ui_prefs = ui_prefs_of(user)
    return {"ui": request._ui_prefs}

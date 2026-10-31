from django.conf import settings


def signup(request):
    """«Рўйхатдан ўтиш» ҳаволаси фақат рўйхат очиқ бўлса кўринади (NARXLASH_SIGNUP)."""
    from panel import conf
    return {"signup_open": conf.get("signup") == "open"}

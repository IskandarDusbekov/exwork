from django.conf import settings


def signup(request):
    """«Рўйхатдан ўтиш» ҳаволаси фақат рўйхат очиқ бўлса кўринади (NARXLASH_SIGNUP)."""
    return {"signup_open": settings.NARXLASH_SIGNUP == "open"}

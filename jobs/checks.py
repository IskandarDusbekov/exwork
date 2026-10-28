"""``manage.py check --deploy``: production uchun yaroqsiz sozlamalar haqida ogohlantirish."""
from django.conf import settings
from django.core.checks import Warning, register


@register("narxlash", deploy=True)
def production_infra(app_configs, **kwargs):
    out = []
    if settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"):
        out.append(Warning("SQLite ishlatilyapti", hint="DATABASE_URL=postgres://… o'rnating", id="narxlash.W001"))
    if not getattr(settings, "REDIS_URL", ""):
        out.append(Warning("Umumiy kesh yo'q (har jarayonning o'z xotirasi): IP bloklar va onlayn holat "
                           "jarayonlar orasida bo'linmaydi", hint="REDIS_URL=redis://… o'rnating", id="narxlash.W002"))
    if getattr(settings, "NARXLASH_TASKS", "thread") != "worker":
        out.append(Warning("Fondagi ishlar sayt jarayonida (thread)", hint="NARXLASH_TASKS=worker va "
                           "alohida ``manage.py run_worker`` ishga tushiring", id="narxlash.W003"))
    if getattr(settings, "MAILERS", {}).get("default", {}).get("BACKEND", "").endswith("console.EmailBackend"):
        out.append(Warning("Email (SMTP) sozlanmagan: parolni tiklash xatlari yuborilmaydi (faqat konsolga chiqadi)",
                           hint="EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, DEFAULT_FROM_EMAIL o'rnating",
                           id="narxlash.W005"))
    if not getattr(settings, "NARXLASH_TRUST_PROXY", 0):
        out.append(Warning("NARXLASH_TRUST_PROXY=0: sayt proksi (nginx, Cloudflare) ortida bo'lsa, hamma foydalanuvchi "
                           "bitta IP bo'lib ko'rinadi — kirish cheklovi va IP bloklash hammaga ta'sir qiladi",
                           hint="nginx ortida — NARXLASH_TRUST_PROXY=1, Cloudflare + nginx — 2; proksisiz bo'lsa "
                                "bu ogohlantirishni e'tiborsiz qoldiring", id="narxlash.W004"))
    return out

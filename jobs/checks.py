# i18n: ok-file — admin / тизим текшируви
"""``manage.py check --deploy``: production uchun yaroqsiz sozlamalar haqida ogohlantirish."""
from django.conf import settings
from django.core.checks import Warning, register


def _mail_configured():
    try:
        from panel.mail import configured
        return configured()
    except Exception:  # noqa: BLE001 — база ҳали йўқ (migrate'дан олдин)
        return bool(getattr(settings, "NARXLASH_EMAIL_ON", False))


def _office_warnings() -> list:
    """Доимий LibreOffice (``NARXLASH_OFFICE``) ёзилган, лекин жавоб бермаяпти — сайт ишлайди (одатдаги усулда),
    лекин секинроқ: огоҳлантириш."""
    spec = getattr(settings, "NARXLASH_OFFICE", "")
    if not spec:
        return []
    import xmlrpc.client
    from core.convert import _TimeoutTransport, parse_office
    try:
        targets = parse_office(spec)
    except ValueError as e:
        return [Warning(str(e), hint="NARXLASH_OFFICE=127.0.0.1:2003,127.0.0.1:2004", id="narxlash.W008")]
    out = []
    for host, port in targets:
        try:
            xmlrpc.client.ServerProxy(f"http://{host}:{port}/RPC2", transport=_TimeoutTransport(3)).ping()
        except Exception as e:  # noqa: BLE001
            out.append(Warning(f"Doimiy LibreOffice {host}:{port} javob bermayapti ({e.__class__.__name__}) — "
                               ".xls eski usulda (sekinroq) o'giriladi",
                               hint=f"sudo systemctl status narxlash-office@{port}", id="narxlash.W008"))
    slots = int(getattr(settings, "NARXLASH_LO_SLOTS", 2))
    if len(targets) != slots:
        out.append(Warning(f"NARXLASH_OFFICE da {len(targets)} ta nusxa, NARXLASH_LO_SLOTS={slots}: bir nusxaga bir "
                           "vaqtda ikki fayl tushishi yoki nusxa bo'sh turishi mumkin",
                           hint="nusxalar soni NARXLASH_LO_SLOTS ga teng bo'lsin", id="narxlash.W008"))
    return out


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
    if not _mail_configured():
        out.append(Warning("Email (SMTP) sozlanmagan: parolni tiklash xatlari yuborilmaydi (faqat konsolga chiqadi)",
                           hint="Panel «Почта» bo'limida yoki .env da EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD",
                           id="narxlash.W005"))
    if not getattr(settings, "NARXLASH_TRUST_PROXY", 0):
        out.append(Warning("NARXLASH_TRUST_PROXY=0: sayt proksi (nginx, Cloudflare) ortida bo'lsa, hamma foydalanuvchi "
                           "bitta IP bo'lib ko'rinadi — kirish cheklovi va IP bloklash hammaga ta'sir qiladi",
                           hint="nginx ortida — NARXLASH_TRUST_PROXY=1, Cloudflare + nginx — 2; proksisiz bo'lsa "
                                "bu ogohlantirishni e'tiborsiz qoldiring", id="narxlash.W004"))
    from .archives import rar_support
    if reason := rar_support():
        out.append(Warning(f"RAR arxivlar ochilmaydi: {reason} — ZIP ichidagi RAR'lar tashlab ketiladi",
                           hint="sudo add-apt-repository -y multiverse && sudo apt update && sudo apt install -y unrar",
                           id="narxlash.W007"))
    out += _office_warnings()
    for name, url, default in (("NARXLASH_ADMIN_URL", settings.ADMIN_URL, "admin/"),
                               ("NARXLASH_PANEL_URL", settings.PANEL_URL, "panel/")):
        if url == default or len(url) < 16:
            out.append(Warning(f"{name}={url}: manzilni taxmin qilish oson — botlar parol tanlab ko'radi",
                               hint="kamida 16 belgili tasodifiy yo'l: python3 -c \"import secrets; "
                                    "print('boshqaruv-' + secrets.token_hex(8) + '/')\"", id="narxlash.W006"))
    return out

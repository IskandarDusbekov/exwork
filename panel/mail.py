"""Хат юбориш (SMTP): созламалар панелдан (``SiteSetting`` key="smtp") ёки сервердаги .env дан.

Панелда ёзилган бўлса — шу, бўлмаса — .env (EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD,
EMAIL_USE_TLS / EMAIL_USE_SSL, DEFAULT_FROM_EMAIL). Иккаласида ҳам сервер йўқ — хат консолга чиқади (ишлаб чиқиш).

Пароль базада очиқ сақланмайди: SECRET_KEY дан олинган калит билан шифрланади (HMAC-SHA256 оқими + HMAC
текшируви — фақат стандарт кутубхона). Панелда пароль кўрсатилмайди, фақат алмаштирилади."""
import base64
import hashlib
import hmac
import os
import secrets

from django.conf import settings
from django.core.cache import cache
from django.core.mail.backends.base import BaseEmailBackend

KEY = "smtp"
CACHE_KEY = "panel:smtp"
SECURITY = (("tls", "STARTTLS (порт 587)"), ("ssl", "SSL (порт 465)"), ("none", "Шифрсиз (тавсия этилмайди)"))
PRESETS = (  # панелдаги тугмалар
    ("Gmail", "smtp.gmail.com", 587, "tls"),
    ("Yandex", "smtp.yandex.ru", 465, "ssl"),
    ("Mail.ru", "smtp.mail.ru", 465, "ssl"),
    ("Outlook", "smtp.office365.com", 587, "tls"),
)


# ===================== паролни шифрлаш =====================
def _key(purpose: bytes) -> bytes:
    return hashlib.sha256(b"narxlash-smtp:" + purpose + b":" + settings.SECRET_KEY.encode()).digest()


def _stream(nonce: bytes, n: int) -> bytes:
    k, out, i = _key(b"enc"), b"", 0
    while len(out) < n:
        out += hmac.new(k, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
        i += 1
    return out[:n]


def encrypt(text: str) -> str:
    raw = text.encode()
    nonce = secrets.token_bytes(16)
    body = bytes(a ^ b for a, b in zip(raw, _stream(nonce, len(raw))))
    tag = hmac.new(_key(b"mac"), nonce + body, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(nonce + tag + body).decode()


def decrypt(token: str) -> str:
    """Бузилган ёки бошқа SECRET_KEY билан шифрланган — бўш сатр."""
    try:
        data = base64.urlsafe_b64decode(token.encode())
        nonce, tag, body = data[:16], data[16:32], data[32:]
        if not hmac.compare_digest(tag, hmac.new(_key(b"mac"), nonce + body, hashlib.sha256).digest()[:16]):
            return ""
        return bytes(a ^ b for a, b in zip(body, _stream(nonce, len(body)))).decode()
    except Exception:  # noqa: BLE001
        return ""


# ===================== созламалар =====================
def stored() -> dict:
    data = cache.get(CACHE_KEY)
    if data is None:
        from .models import SiteSetting
        row = SiteSetting.objects.filter(key=KEY).first()
        data = dict(row.value) if row and isinstance(row.value, dict) else {}
        if row:
            data["_updated"] = row.updated.isoformat()
            data["_by"] = row.updated_by.email if row.updated_by_id else ""
        cache.set(CACHE_KEY, data, 30)
    return data


def _env() -> dict:
    ssl_on = os.environ.get("EMAIL_USE_SSL", "0") == "1"
    return {
        "host": os.environ.get("EMAIL_HOST", ""),
        "port": int(os.environ.get("EMAIL_PORT", "465" if ssl_on else "587") or 587),
        "username": os.environ.get("EMAIL_HOST_USER", ""),
        "password": os.environ.get("EMAIL_HOST_PASSWORD", ""),
        "security": "ssl" if ssl_on else ("tls" if os.environ.get("EMAIL_USE_TLS", "1") == "1" else "none"),
        "from_email": os.environ.get("DEFAULT_FROM_EMAIL", ""),
        "from_name": "",
    }


def config() -> dict:
    """Ҳозир ишлайдиган созламалар + ``source``: panel | env | none."""
    s = stored()
    if s.get("host"):
        cfg = {k: s.get(k, "") for k in ("host", "username", "security", "from_email", "from_name")}
        cfg["port"] = int(s.get("port") or 587)
        cfg["password"] = decrypt(s["password"]) if s.get("password") else ""
        cfg["password_broken"] = bool(s.get("password")) and not cfg["password"]
        cfg["source"] = "panel"
    else:
        cfg = _env()
        cfg["password_broken"] = False
        cfg["source"] = "env" if cfg["host"] else "none"
    cfg["updated"], cfg["by"] = s.get("_updated", ""), s.get("_by", "")
    return cfg


def configured() -> bool:
    return config()["source"] != "none"


def from_address() -> str:
    """«mhisobot <noreply@…>» — панелдаги ном ва манзил, бўлмаса логин, бўлмаса .env даги DEFAULT_FROM_EMAIL."""
    cfg = config()
    addr = cfg.get("from_email") or cfg.get("username") or ""
    if not addr:
        return settings.DEFAULT_FROM_EMAIL
    if "<" in addr:
        return addr
    name = (cfg.get("from_name") or "mhisobot").replace('"', "")
    return f"{name} <{addr}>"


def save(data: dict, user, new_password=None, clear_password=False) -> None:
    from .models import SiteSetting
    old = stored()
    value = {k: data.get(k, "") for k in ("host", "port", "username", "security", "from_email", "from_name")}
    if clear_password:
        value["password"] = ""
    elif new_password:
        value["password"] = encrypt(new_password)
    else:
        value["password"] = old.get("password", "")
    SiteSetting.objects.update_or_create(key=KEY, defaults={"value": value, "updated_by": user})
    cache.delete(CACHE_KEY)


def reset_to_env() -> None:
    from .models import SiteSetting
    SiteSetting.objects.filter(key=KEY).delete()
    cache.delete(CACHE_KEY)


# ===================== Django почта backend'и =====================
class PanelMailBackend(BaseEmailBackend):
    """MAILERS["default"]: ҳар юборишда созламалар қайта ўқилади — панелда ўзгартирилгани сервер қайта
    ишга туширилмасдан ишлайди. Сервер созланмаган бўлса — консолга (ишлаб чиқиш)."""

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(**kwargs)  # alias; fail_silently — ўзимизда (Django бэкендлари каби)
        self.fail_silently = fail_silently

    def _inner(self):
        cfg = config()
        if cfg["source"] == "none":
            from django.core.mail.backends.console import EmailBackend
            return EmailBackend(fail_silently=self.fail_silently, alias=self.alias)
        from django.core.mail.backends.smtp import EmailBackend
        return EmailBackend(host=cfg["host"], port=cfg["port"], username=cfg["username"] or None,
                            password=cfg["password"] or None, use_tls=cfg["security"] == "tls",
                            use_ssl=cfg["security"] == "ssl", timeout=20, fail_silently=self.fail_silently,
                            alias=self.alias)

    def send_messages(self, email_messages):
        for m in email_messages:
            if m.from_email in ("", None, settings.DEFAULT_FROM_EMAIL):
                m.from_email = from_address()
        return self._inner().send_messages(email_messages)

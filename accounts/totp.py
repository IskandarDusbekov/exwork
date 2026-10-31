"""Икки босқичли кириш учун вақтга боғлиқ код (TOTP, RFC 6238): Google Authenticator, Microsoft Authenticator ва
ш.к. иловалар 30 сонияда бир марта 6 рақамли код беради. Ташқи кутубхонасиз — стандарт ``hmac``."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP = 30
DIGITS = 6
ISSUER = "Муҳандис лойиҳачи"


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    n = struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF
    return str(n % 10 ** DIGITS).zfill(DIGITS)


def verify(secret: str, code, last_step: int = 0, now: Optional[float] = None) -> Optional[int]:
    """Код тўғри бўлса — унинг вақт қадами (қайта ишлатилмаслиги учун сақланади), бўлмаса None.
    Телефон соати ±30 сония фарқ қилса ҳам ўтади; аввал ишлатилган қадам (``last_step``) қабул қилинмайди."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now_step = int((time.time() if now is None else now) // STEP)
    for step in (now_step - 1, now_step, now_step + 1):
        if step > last_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def pretty(secret: str) -> str:
    """Иловага қўлда киритиш учун: «ABCD EFGH …»."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def uri(secret: str, email: str) -> str:
    label = quote(f"{ISSUER}:{email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP}"

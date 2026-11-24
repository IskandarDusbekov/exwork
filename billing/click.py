"""Click (SHOP API): балансни тўлдириш.

1. Фойдаланувчи суммани танлайди → ``pay_url`` ``Payment`` (created) яратиб, my.click.uz тўлов саҳифасига юборади
   (``transaction_param`` = Payment.pk).
2. Click серверимизга икки марта мурожаат қилади (кириш шарт эмас, имзо билан текширилади):
   * **Prepare** (action=0) — тўлов бор-йўқлиги ва суммаси текширилади, ``prepared`` бўлади;
   * **Complete** (action=1) — пул ечилди: балансга ёзилади (``services.post``, ``ref=click:<pk>`` — икки марта
     ёзилмайди), ``paid`` бўлади. Click хато билан юборса (error < 0) — ``cancelled``.
3. Тўловдан кейин Click фойдаланувчини ``return_url`` га (профилга) қайтаради.

Маълумотлар фақат серверда, .env да: CLICK_SERVICE_ID, CLICK_MERCHANT_ID, CLICK_SECRET_KEY, CLICK_MERCHANT_USER_ID.
Click кабинетида Prepare / Complete манзиллари: https://<сайт>/balance/click/prepare/ ва .../complete/."""
import hashlib
import hmac
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from . import services as bs
from .models import Entry, Payment

log = logging.getLogger("billing.click")

PAY_URL = "https://my.click.uz/services/pay"

# Click хато кодлари (SHOP API ҳужжати)
OK = 0
SIGN_FAILED = -1
BAD_AMOUNT = -2
BAD_ACTION = -3
ALREADY_PAID = -4
NOT_FOUND = -5
NO_TRANSACTION = -6
UPDATE_FAILED = -7
BAD_REQUEST = -8
CANCELLED = -9

NOTES = {OK: "Success", SIGN_FAILED: "SIGN CHECK FAILED!", BAD_AMOUNT: "Incorrect parameter amount",
         BAD_ACTION: "Action not found", ALREADY_PAID: "Already paid", NOT_FOUND: "User does not exist",
         NO_TRANSACTION: "Transaction does not exist", UPDATE_FAILED: "Failed to update user",
         BAD_REQUEST: "Error in request from click", CANCELLED: "Transaction cancelled"}


def conf(key: str) -> str:
    return str(getattr(settings, f"CLICK_{key}", "") or "").strip()


def ready() -> bool:
    return all(conf(k) for k in ("SERVICE_ID", "MERCHANT_ID", "SECRET_KEY"))


def pay_url(request, amount: int) -> str:
    """Тўлов ёзувини яратиб, Click тўлов саҳифаси манзилини қайтаради."""
    p = Payment.objects.create(user=request.user, provider="click", amount=amount)
    back = request.build_absolute_uri(reverse("billing:profile")) + f"?paid={p.pk}"
    q = {"service_id": conf("SERVICE_ID"), "merchant_id": conf("MERCHANT_ID"), "amount": amount,
         "transaction_param": p.pk, "return_url": back}
    if conf("MERCHANT_USER_ID"):
        q["merchant_user_id"] = conf("MERCHANT_USER_ID")
    return f"{PAY_URL}?{urlencode(q)}"


# ---------------- Click → сервер ----------------
def _sign(d, prepare_id: str = "") -> str:
    s = (f"{d.get('click_trans_id', '')}{d.get('service_id', '')}{conf('SECRET_KEY')}{d.get('merchant_trans_id', '')}"
         f"{prepare_id}{d.get('amount', '')}{d.get('action', '')}{d.get('sign_time', '')}")
    return hashlib.md5(s.encode()).hexdigest()


def _amount(raw):
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _answer(d, code: int, **extra) -> dict:
    return {"click_trans_id": d.get("click_trans_id", ""), "merchant_trans_id": d.get("merchant_trans_id", ""),
            **extra, "error": code, "error_note": NOTES[code]}


REQUIRED = ("click_trans_id", "service_id", "merchant_trans_id", "amount", "action", "sign_time", "sign_string")


def _common(d, action: int, prepare_id: str = ""):
    """Иккала босқич учун умумий текширув: (хато коди ёки None, Payment ёки None)."""
    if any(d.get(k) in (None, "") for k in REQUIRED):
        return BAD_REQUEST, None
    if not ready():
        log.error("Click: .env да CLICK_* маълумотлари йўқ")
        return BAD_REQUEST, None
    if not hmac.compare_digest(_sign(d, prepare_id), str(d.get("sign_string", "")).lower()):
        log.warning("Click: имзо нотўғри (trans %s)", d.get("click_trans_id"))
        return SIGN_FAILED, None
    if str(d.get("service_id")) != conf("SERVICE_ID"):
        return BAD_REQUEST, None
    if str(d.get("action")) != str(action):
        return BAD_ACTION, None
    pk = str(d.get("merchant_trans_id"))
    p = Payment.objects.filter(pk=int(pk), provider="click").first() if pk.isdigit() else None
    if p is None:
        return NOT_FOUND, None
    amt = _amount(d.get("amount"))
    if amt is None or abs(amt - p.amount) >= Decimal("0.01"):
        return BAD_AMOUNT, p
    if p.status == Payment.Status.PAID:
        return ALREADY_PAID, p
    if p.status in (Payment.Status.CANCELLED, Payment.Status.FAILED):
        return CANCELLED, p
    return None, p


def prepare(d) -> dict:
    code, p = _common(d, 0)
    if code is not None:
        return _answer(d, code)
    tid = str(d["click_trans_id"])
    if p.provider_id and p.provider_id != tid and p.status == Payment.Status.PREPARED:
        # Бир буюртмага иккинчи Click тўлови — аввалгиси тугамаган. Битта ёзув — битта тўлов.
        return _answer(d, ALREADY_PAID)
    if Payment.objects.filter(provider="click", provider_id=tid).exclude(pk=p.pk).exists():
        return _answer(d, BAD_REQUEST)
    p.status, p.provider_id = Payment.Status.PREPARED, tid
    p.raw = {**p.raw, "prepare": dict(d)}
    p.save(update_fields=["status", "provider_id", "raw"])
    return _answer(d, OK, merchant_prepare_id=p.pk)


def complete(d) -> dict:
    prep = str(d.get("merchant_prepare_id") or "")
    if not prep:
        return _answer(d, BAD_REQUEST)
    code, p = _common(d, 1, prep)
    if code is not None:
        return _answer(d, code)
    if prep != str(p.pk) or p.status != Payment.Status.PREPARED or p.provider_id != str(d["click_trans_id"]):
        return _answer(d, NO_TRANSACTION)
    try:
        click_error = int(d.get("error") or 0)
    except (TypeError, ValueError):
        click_error = 0
    if click_error < 0:
        # Click томонда ўтмади (картада пул йўқ ва ҳ.к.) — бекор, балансга ҳеч нарса ёзилмайди
        Payment.objects.filter(pk=p.pk, status=Payment.Status.PREPARED).update(
            status=Payment.Status.CANCELLED, error=str(d.get("error_note") or click_error)[:255])
        return _answer(d, CANCELLED)
    try:
        with transaction.atomic():
            p = Payment.objects.select_for_update().get(pk=p.pk)
            if p.status == Payment.Status.PAID:
                return _answer(d, ALREADY_PAID)
            entry = bs.post(p.user, p.amount, Entry.Kind.TOPUP, "Click", ref=f"click:{p.pk}")
            p.status, p.paid_at = Payment.Status.PAID, timezone.now()
            p.raw = {**p.raw, "complete": dict(d)}
            p.save(update_fields=["status", "paid_at", "raw"])
    except Exception:
        log.exception("Click: балансга ёзиб бўлмади (payment %s)", p.pk)
        return _answer(d, UPDATE_FAILED)
    log.info("Click: %s сўм тўланди (payment %s, user %s)", p.amount, p.pk, p.user_id)
    return _answer(d, OK, merchant_confirm_id=entry.pk)

import logging
import secrets
import time
from hmac import compare_digest as hmac_equal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from panel.audit import log as audit
from panel.models import Event

from . import throttle, totp
from .emails import send_reset, send_verify
from .forms import LoginForm, RegisterForm, ResetRequestForm, SetPasswordForm
from .models import AuthAttempt, TwoFactor, User, normalize_email

BLOCKED = "Жуда кўп уриниш. 15 дақиқадан кейин қайта уриниб кўринг."
logger = logging.getLogger(__name__)


def _next_url(request) -> str:
    """Киргандан кейин қаерга: фақат ўз сайтимиздаги манзил (бошқа сайтга йўналтириш бўлмасин)."""
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return nxt
    return ""


@login_not_required
@sensitive_post_parameters("password")
@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_next_url(request) or "index")
    form = LoginForm(request.POST or None)
    error = None
    if request.method == "POST":
        if not form.is_valid():
            error = "Email ва паролни киритинг."
        elif throttle.login_blocked(request, form.cleaned_data["email"]):
            error = BLOCKED
            audit(request, Event.Kind.LOGIN_FAILED, email=form.cleaned_data["email"], detail="блокланган")
        else:
            email = form.cleaned_data["email"]
            user = authenticate(request, username=email, password=form.cleaned_data["password"])
            if user is None:
                throttle.record(request, AuthAttempt.Kind.LOGIN, email)
                error = "Email ёки пароль нотўғри."  # қайси бири нотўғрилигини айтмаймиз
            else:
                if TwoFactor.objects.filter(user=user).exists():  # иккинчи босқич: телефондаги код
                    _pending(request, user, _next_url(request))
                    return redirect("login_code")
                throttle.clear_login_failures(email)
                login(request, user)  # сессия калити янгиланади (session fixation'га қарши)
                return redirect(_next_url(request) or "index")
    return render(request, "accounts/login.html", {"form": form, "error": error, "next": _next_url(request)})


# ===================== икки босқичли кириш =====================
PENDING = "_2fa_pending"  # {"user", "at", "next", "tries"} — пароль тўғри, код кутиляпти
CODE_TTL = 300
CODE_TRIES = 5
VERIFIED = "_2fa_ok"  # шу сессияда код киритилган фойдаланувчи (панел / админ шуни текширади)
SETUP = "_2fa_setup"  # ўрнатишда кўрсатилган калит (тасдиқлангунча базага ёзилмайди)


def _pending(request, user, nxt=""):
    request.session[PENDING] = {"user": user.pk, "at": int(time.time()), "next": nxt or "", "tries": 0}


def verified(request) -> bool:
    return request.user.is_authenticated and request.session.get(VERIFIED) == request.user.pk


@login_not_required
@never_cache
@require_http_methods(["GET", "POST"])
def login_code_view(request):
    """Иккинчи босқич: илова коди. Пароль аввал текширилган; 5 дақиқа ва 5 уриниш — кейин қайтадан пароль."""
    p = request.session.get(PENDING)
    if not p or time.time() - p["at"] > CODE_TTL:
        request.session.pop(PENDING, None)
        messages.error(request, "Вақт тугади — қайтадан киринг.")
        return redirect("login")
    user = User.objects.filter(pk=p["user"], is_active=True).first()
    tf = TwoFactor.objects.filter(user=user).first() if user else None
    if tf is None:
        request.session.pop(PENDING, None)
        return redirect("login")
    error = None
    if request.method == "POST":
        step = totp.verify(tf.secret, request.POST.get("code"), tf.last_step)
        if step is None:
            p["tries"] += 1
            request.session[PENDING] = p
            throttle.record(request, AuthAttempt.Kind.LOGIN, user.email)  # email бўйича блок шу ҳисобга ҳам киради
            audit(request, Event.Kind.TWOFA_FAILED, actor=False, target=user, email=user.email, detail=f"{p['tries']}-уриниш")
            if p["tries"] >= CODE_TRIES:
                request.session.pop(PENDING, None)
                messages.error(request, "Код кўп марта нотўғри киритилди — қайтадан киринг.")
                return redirect("login")
            error = "Код нотўғри ёки эскирган — иловадаги янги кодни киритинг."
        elif TwoFactor.objects.filter(pk=tf.pk, last_step__lt=step).update(last_step=step):
            request.session.pop(PENDING, None)
            throttle.clear_login_failures(user.email)
            if request.user.is_authenticated and request.user.pk == user.pk:
                request.session.cycle_key()  # аввал кирган (код сўралмаган) сессия — фақат тасдиқланади
            else:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            request.session[VERIFIED] = user.pk
            return redirect(p["next"] or "index")
        else:
            error = "Бу код ишлатилган — иловадаги кейинги кодни кутинг."
    return render(request, "accounts/login_code.html", {"error": error, "email": user.email})


@never_cache
@require_http_methods(["GET", "POST"])
def twofa_setup_view(request):
    """Икки босқичли киришни ёқиш: калит (QR ўрнига қўлда ёки ҳавола) -> иловадаги код билан тасдиқлаш."""
    nxt = _next_url(request)
    tf = TwoFactor.objects.filter(user=request.user).first()
    if tf is not None:
        if not verified(request):  # ёқилган, лекин шу сессияда код киритилмаган
            _pending(request, request.user, nxt)
            return redirect("login_code")
        return render(request, "accounts/twofa_setup.html", {"done": True, "since": tf.confirmed, "next": nxt})
    secret = request.session.get(SETUP) or totp.new_secret()
    request.session[SETUP] = secret
    error = None
    if request.method == "POST":
        step = totp.verify(secret, request.POST.get("code"))
        if step is None:
            error = "Код нотўғри. Телефондаги соат тўғрилигини ва калит тўлиқ киритилганини текширинг."
        else:
            TwoFactor.objects.create(user=request.user, secret=secret, last_step=step)
            request.session.pop(SETUP, None)
            request.session.cycle_key()
            request.session[VERIFIED] = request.user.pk
            audit(request, Event.Kind.TWOFA_ON, detail=request.user.email)
            messages.success(request, "Икки босқичли кириш ёқилди: энди ҳар киришда иловадаги код сўралади.")
            return redirect(nxt or "twofa_setup")
    return render(request, "accounts/twofa_setup.html", {"secret": totp.pretty(secret), "uri": totp.uri(secret, request.user.email),
                                                         "error": error, "next": nxt,
                                                         "required": request.user.is_staff})


@login_not_required
@sensitive_post_parameters("password1", "password2")
@never_cache
@require_http_methods(["GET", "POST"])
def register_view(request):
    if request.user.is_authenticated:
        return redirect("index")
    from panel import conf
    if conf.get("signup") != "open":
        # ҳисобни фақат админ очади — форма ҳам, POST ҳам йўқ
        return render(request, "accounts/register.html", {"closed": True}, status=403 if request.method == "POST" else 200)
    form = RegisterForm(request.POST or None)
    error = None
    if request.method == "POST":
        if throttle.register_blocked(request):
            error = BLOCKED
        elif form.is_valid():
            throttle.record(request, AuthAttempt.Kind.REGISTER, form.cleaned_data["email"])
            user = form.save()
            audit(request, Event.Kind.REGISTER, actor=user, detail=user.get_full_name())
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            if not send_verify_code(request, user):
                messages.error(request, MAIL_FAILED)
            return redirect("verify_email")
    return render(request, "accounts/register.html", {"form": form, "error": error})


@require_POST
def logout_view(request):
    logout(request)  # сессия базадан ўчирилади
    return redirect("login")


# ===================== паролни тиклаш =====================
# Хат ҳар доим «юборилди» дейилади — шу email билан ҳисоб бор-йўқлиги ошкор бўлмайди.
# Ҳавола PASSWORD_RESET_TIMEOUT ичида ва фақат бир марта ишлайди (пароль ўзгаргач токен эскиради).
RESET_SESSION_TOKEN = "_reset_token"
RESET_URL_TOKEN = "set-password"
# Хатда ҳавола билан бирга 6 рақамли код: шу браузерда «Паролни тиклаш» саҳифасига киритилади. Код сессияда
# (фақат HMAC'и) сақланади; 5 марта хато — бекор. Ҳисоб йўқ бўлса ҳам сессияга сохта ёзув қўйилади —
# код саҳифаси бир хил ишлайди ва ҳисоб бор-йўқлиги билинмайди.
RESET_CODE = "_reset_code"
RESET_CODE_TRIES = 5


def _code_hash(code):
    import hashlib
    import hmac
    return hmac.new(settings.SECRET_KEY.encode(), ("reset-code:" + code).encode(), hashlib.sha256).hexdigest()


@login_not_required
@never_cache
@require_http_methods(["GET", "POST"])
def reset_request_view(request):
    form = ResetRequestForm(request.POST or None)
    error = None
    if request.method == "POST" and form.is_valid():
        email = normalize_email(form.cleaned_data["email"])
        if throttle.reset_blocked(request, email):
            error = BLOCKED
        else:
            throttle.record(request, AuthAttempt.Kind.RESET, email)
            user = User.objects.filter(email=email, is_active=True).first()
            entry = {"uid": "", "token": "", "hash": _code_hash(secrets.token_hex(8)), "tries": 0,
                     "exp": int(time.time()) + settings.PASSWORD_RESET_CODE_MINUTES * 60, "email": email}
            # тасдиқланмаган email — бошқа одам бировнинг почтаси билан ҳисоб очган бўлиши мумкин: хат юборилмайди
            if user is not None and user.has_usable_password() and user.email_verified:
                entry.update(_send_reset(request, user))
            request.session[RESET_CODE] = entry
            return redirect("reset_code")
    return render(request, "accounts/reset_request.html", {"form": form, "error": error})


def _send_reset(request, user) -> dict:
    """Хат юборади; қайтади: сессияга ёзиладиган {uid, token, hash}."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = request.build_absolute_uri(reverse("reset_confirm", args=[uid, token]))
    code = f"{secrets.randbelow(10 ** 6):06d}"
    send_reset(request.build_absolute_uri("/"), user, code, link)
    return {"uid": uid, "token": token, "hash": _code_hash(code)}


@login_not_required
@never_cache
@require_http_methods(["GET", "POST"])
def reset_code_view(request):
    """Хатдаги кодни киритиш. Тўғри бўлса — янги пароль саҳифаси (ҳаволадаги каби)."""
    entry = request.session.get(RESET_CODE)
    if not entry:
        return redirect("reset_request")
    expired = entry["exp"] < time.time() or entry["tries"] >= RESET_CODE_TRIES
    error = None
    if request.method == "POST" and not expired:
        code = "".join(ch for ch in request.POST.get("code", "") if ch.isdigit())
        if entry["uid"] and len(code) == 6 and hmac_equal(_code_hash(code), entry["hash"]):
            request.session.pop(RESET_CODE, None)
            request.session[RESET_SESSION_TOKEN] = entry["token"]
            return redirect("reset_confirm", uidb64=entry["uid"], token=RESET_URL_TOKEN)
        entry["tries"] += 1
        request.session[RESET_CODE] = entry
        left = RESET_CODE_TRIES - entry["tries"]
        expired = left <= 0
        error = f"Код нотўғри. Яна {left} марта уриниш мумкин." if left > 0 else None
    return render(request, "accounts/reset_request.html", {
        "sent": True, "email": entry["email"], "error": error, "expired": expired,
        "minutes": settings.PASSWORD_RESET_CODE_MINUTES})


def _reset_user(uidb64):
    try:
        return User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)), is_active=True)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None


@login_not_required
@sensitive_post_parameters("password1", "password2")
@never_cache
@require_http_methods(["GET", "POST"])
def reset_confirm_view(request, uidb64, token):
    user = _reset_user(uidb64)
    if user is not None and token != RESET_URL_TOKEN and default_token_generator.check_token(user, token):
        # токен манзилдан сессияга олинади: саҳифадаги ташқи ҳаволалар (Referer) орқали чиқиб кетмасин
        request.session[RESET_SESSION_TOKEN] = token
        return redirect("reset_confirm", uidb64=uidb64, token=RESET_URL_TOKEN)
    valid = (user is not None and token == RESET_URL_TOKEN
             and default_token_generator.check_token(user, request.session.get(RESET_SESSION_TOKEN, "")))
    if not valid:
        return render(request, "accounts/reset_confirm.html", {"invalid": True}, status=400 if request.method == "POST" else 200)
    form = SetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user.set_password(form.cleaned_data["password1"])
        user.save(update_fields=["password"])  # бошқа қурилмалардаги сессиялар эскиради (сессия хеши ўзгарди)
        request.session.pop(RESET_SESSION_TOKEN, None)
        throttle.clear_login_failures(user.email)
        audit(request, Event.Kind.PASSWORD_RESET, actor=user, detail=user.email)
        messages.success(request, "Пароль ўзгартирилди — янги парол билан киринг.")
        return redirect("login")
    return render(request, "accounts/reset_confirm.html", {"form": form, "email": user.email})


# ===================== email тасдиқлаш =====================
# Рўйхатдан ўтгач хатда 6 рақамли код ва ҳавола. Ҳисоб тасдиқланмаса ҳам ишлайди, лекин тепада огоҳлантириш
# туради ва паролни тиклаш хати юборилмайди. Код фақат HMAC'и билан сессияда (сервер базасида) сақланади:
# PASSWORD_RESET_CODE_MINUTES дақиқа, 5 хато — бекор. Хат: 60 сонияда 1 та, 15 дақиқада 3 та.
VERIFY_CODE = "_verify_code"  # {"hash", "exp", "tries", "email"}
VERIFY_TRIES = 5
VERIFY_SALT = "accounts.verify-email"
MAIL_FAILED = "Тасдиқлаш хати юборилмади — почта сервери ишламаяпти. Бироздан кейин «Кодни қайта юбориш»ни босинг."


def _verify_hash(code):
    import hashlib
    import hmac
    return hmac.new(settings.SECRET_KEY.encode(), ("verify-code:" + code).encode(), hashlib.sha256).hexdigest()


def send_verify_code(request, user) -> bool:
    """Код ва ҳаволали хат. False — хат кетмади (SMTP хатоси; ҳисоб барибир очилган)."""
    code = f"{secrets.randbelow(10 ** 6):06d}"
    token = signing.dumps({"u": user.pk, "e": user.email}, salt=VERIFY_SALT)
    link = request.build_absolute_uri(reverse("verify_link", args=[token]))
    throttle.record(request, AuthAttempt.Kind.VERIFY, user.email)
    request.session[VERIFY_CODE] = {"hash": _verify_hash(code), "tries": 0, "email": user.email,
                                    "exp": int(time.time()) + settings.PASSWORD_RESET_CODE_MINUTES * 60}
    try:
        send_verify(request.build_absolute_uri("/"), user, code, link)
    except Exception:  # noqa: BLE001 — почта ишламаса ҳам рўйхат тўхтамасин
        logger.exception("email тасдиқлаш хати кетмади: %s", user.email)
        return False
    return True


def _mark_verified(request, user, how):
    if not user.email_verified:
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        audit(request, Event.Kind.EMAIL_VERIFIED, actor=user, detail=f"{user.email} ({how})")
    request.session.pop(VERIFY_CODE, None)


def _wait_text(sec):
    return f"{sec} сония" if sec < 60 else f"{(sec + 59) // 60} дақиқа"


@never_cache
@require_http_methods(["GET", "POST"])
def verify_email_view(request):
    user = request.user
    if user.email_verified:
        return render(request, "accounts/verify_email.html", {"done": True, "email": user.email})
    entry = request.session.get(VERIFY_CODE)
    if entry and entry.get("email") != user.email:
        entry = None
    expired = entry is None or entry["exp"] < time.time() or entry["tries"] >= VERIFY_TRIES
    error = None
    if request.method == "POST" and request.POST.get("action") == "send":
        wait = throttle.verify_wait(user.email)
        if wait:
            messages.error(request, f"Янги хатни {_wait_text(wait)}дан кейин юбориш мумкин.")
        elif send_verify_code(request, user):
            messages.success(request, f"Янги код {user.email} га юборилди.")
        else:
            messages.error(request, MAIL_FAILED)
        return redirect("verify_email")
    if request.method == "POST":
        code = "".join(ch for ch in request.POST.get("code", "") if ch.isdigit())
        if expired:
            error = "Код эскирди ёки кўп марта хато киритилди — янги код сўранг."
        elif len(code) == 6 and hmac_equal(_verify_hash(code), entry["hash"]):
            _mark_verified(request, user, "код")
            return redirect("verify_email")  # «Email тасдиқланган» саҳифаси
        else:
            entry["tries"] += 1
            request.session[VERIFY_CODE] = entry
            left = VERIFY_TRIES - entry["tries"]
            expired = left <= 0
            error = (f"Код нотўғри. Яна {left} марта уриниш мумкин." if left > 0
                     else "Код кўп марта хато киритилди — янги код сўранг.")
    return render(request, "accounts/verify_email.html", {
        "email": user.email, "error": error, "expired": expired, "has_code": entry is not None,
        "minutes": settings.PASSWORD_RESET_CODE_MINUTES, "wait": throttle.verify_wait(user.email)})


@login_not_required
@never_cache
def verify_link_view(request, token):
    """Хатдаги тугма: бошқа қурилмадан ҳам ишлайди (тизимга кирмасдан). Email ўзгарса — ҳавола эскиради."""
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=settings.EMAIL_VERIFY_LINK_HOURS * 3600)
    except signing.BadSignature:
        data = None
    user = User.objects.filter(pk=data.get("u"), is_active=True).first() if isinstance(data, dict) else None
    if user is None or user.email != data.get("e"):
        return render(request, "accounts/verify_email.html", {"invalid": True}, status=400)
    _mark_verified(request, user, "ҳавола")
    if request.user.is_authenticated and request.user.pk == user.pk:
        return redirect("verify_email")
    return render(request, "accounts/verify_email.html", {"done": True, "email": user.email, "guest": True})

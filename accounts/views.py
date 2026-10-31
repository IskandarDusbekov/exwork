import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from panel.audit import log as audit
from panel.models import Event

from . import throttle, totp
from .forms import LoginForm, RegisterForm, ResetRequestForm, SetPasswordForm
from .models import AuthAttempt, TwoFactor, User, normalize_email

BLOCKED = "Жуда кўп уриниш. 15 дақиқадан кейин қайта уриниб кўринг."


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
            return redirect("index")
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
            if user is not None and user.has_usable_password():
                _send_reset(request, user)
            return render(request, "accounts/reset_request.html", {"sent": True, "email": email})
    return render(request, "accounts/reset_request.html", {"form": form, "error": error})


def _send_reset(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    link = request.build_absolute_uri(reverse("reset_confirm", args=[uid, default_token_generator.make_token(user)]))
    ctx = {"user": user, "link": link, "hours": settings.PASSWORD_RESET_TIMEOUT // 3600}
    send_mail("Муҳандис лойиҳачи: паролни тиклаш", render_to_string("accounts/reset_email.txt", ctx),
              settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)


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

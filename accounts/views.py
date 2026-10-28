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

from . import throttle
from .forms import LoginForm, RegisterForm, ResetRequestForm, SetPasswordForm
from .models import AuthAttempt, User, normalize_email

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
                throttle.clear_login_failures(email)
                login(request, user)  # сессия калити янгиланади (session fixation'га қарши)
                return redirect(_next_url(request) or "index")
    return render(request, "accounts/login.html", {"form": form, "error": error, "next": _next_url(request)})


@login_not_required
@sensitive_post_parameters("password1", "password2")
@never_cache
@require_http_methods(["GET", "POST"])
def register_view(request):
    if request.user.is_authenticated:
        return redirect("index")
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
    send_mail("Нархлаш: паролни тиклаш", render_to_string("accounts/reset_email.txt", ctx),
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

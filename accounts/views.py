from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_not_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from panel.audit import log as audit
from panel.models import Event

from . import throttle
from .forms import LoginForm, RegisterForm
from .models import AuthAttempt

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

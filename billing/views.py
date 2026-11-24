"""Фойдаланувчининг «Баланс ва тариф» саҳифаси: баланс, тариф, шу ойда ишлатилгани, тарифлар ва сотиб олиш,
балансни тўлдириш (Click — 4-босқич), дафтар, тўловлар ва очилган файллар тарихи."""
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services as bs
from .models import Account, Entry, Payment, Tariff

TOPUP_AMOUNTS = (10000, 50000, 100000, 500000, 1000000)
AVG_ROWS = 600  # ўртача локал смета қатори (базадан: ўртача ~611, медиана ~236) — «тахминан нечта смета» учун


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def kind_labels() -> dict:
    """Дафтар турлари — фойдаланувчи тилида (моделдаги номлар фақат панел учун)."""
    return {Entry.Kind.TOPUP: _("Баланс тўлдирилди"), Entry.Kind.BONUS: _("Бонус"), Entry.Kind.ADMIN: _("Тузатиш"),
            Entry.Kind.SUBSCRIPTION: _("Тариф"), Entry.Kind.ROWS: _("Смета юклаб олинди"),
            Entry.Kind.OBJECT: _("Техназор объекти"), Entry.Kind.REFUND: _("Қайтарилди")}


def status_labels() -> dict:
    return {Payment.Status.CREATED: _("Кутилмоқда"), Payment.Status.PREPARED: _("Кутилмоқда"),
            Payment.Status.PAID: _("Тўланди"), Payment.Status.CANCELLED: _("Бекор қилинди"),
            Payment.Status.FAILED: _("Ўтмади")}


def click_ready() -> bool:
    """Click маълумотлари серверда (.env) ёзилганми."""
    from . import click
    return click.ready()


def _paid_note(request, user):
    """Click'дан қайтгач (``?paid=<pk>``): тўлов ҳолатини бир марта айтиш."""
    pk = request.GET.get("paid", "")
    p = user.payments.filter(pk=int(pk)).first() if pk.isdigit() else None
    if p is None:
        return
    som = f"{p.amount:,}".replace(",", " ")
    if p.status == Payment.Status.PAID:
        messages.success(request, _("Тўлов ўтди: балансга {0} сўм тушди.").format(som))
    elif p.status in (Payment.Status.CANCELLED, Payment.Status.FAILED):
        messages.error(request, _("Тўлов ўтмади — балансдан ҳеч нарса ечилмади. Қайта уриниб кўринг."))
    else:
        messages.info(request, _("Тўлов текширилмоқда: Click тасдиқлаши билан {0} сўм балансга тушади — "
                                 "саҳифани бир оздан кейин янгиланг.").format(som))


def profile(request):
    user = request.user
    if request.GET.get("paid"):
        _paid_note(request, user)
        return redirect("billing:profile")
    acc = bs.account(user)
    plan = bs.plan(user)
    labels, st = kind_labels(), status_labels()
    tab = request.GET.get("t") if request.GET.get("t") in ("ledger", "payments", "files") else "ledger"
    page = Paginator(user.ledger.all(), 30).get_page(request.GET.get("page"))
    for e in page:
        e.label = labels.get(e.kind, e.kind)
    # Click'гача етмаган (саҳифа очилиб, тўланмаган) ёзувлар тарихни тўлдирмасин
    payments = list(user.payments.exclude(status=Payment.Status.CREATED)[:50])
    for p in payments:
        p.label = st.get(p.status, p.status)
    tariffs = list(Tariff.objects.filter(active=True))
    for t in tariffs:
        t.current = plan.tariff is not None and t.pk == plan.tariff.pk
        t.opts = []  # ҳар муддат учун: нарх, нима қилиш мумкин, пул етадими
        for m in (bs.PERIODS if not t.free else (1,)):
            o = bs.offer(user, t, m, p=plan)
            t.opts.append({"m": m, "o": o, "p": o.price, "afford": acc.total >= o.pay, "need": max(0, o.pay - acc.total)})
        t.smetas = t.rows // AVG_ROWS if t.rows else 0
    lo, hi = bs.topup_limits()
    periods = [{"m": m, "pct": bs.period_discount(m)} for m in bs.PERIODS]
    return render(request, "billing/profile.html", {
        "nav": "balance", "acc": acc, "plan": plan, "tariffs": tariffs, "on": bs.enabled(),
        "exempt": bs.exempt(user), "row_price": bs.row_price(), "object_price": bs.object_price(),
        "tab": tab, "page": page, "payments": payments, "unlocks": user.unlocks.filter(free=False)[:50],
        "amounts": [a for a in TOPUP_AMOUNTS if lo <= a <= hi], "topup_min": lo, "topup_max": hi,
        "click": click_ready(), "next_tariff": acc.next_tariff, "periods": periods, "avg_rows": AVG_ROWS,
        "sub_end": plan.sub.end if plan.sub else plan.end, "need_verify": need_verify(user),
        "days_left": max(0, ((plan.sub.end if plan.sub else plan.end) - timezone.now()).days)})


@require_POST
def buy(request, pk):
    """Тарифни танлаш: йўқ бўлса — сотиб олиш, қимматроғи — ҳозир (фарқи), арзонроғи — кейинги даврдан."""
    t = get_object_or_404(Tariff, pk=pk, active=True)
    if need_verify(request.user):
        return _verify_first(request)
    months = int(request.POST.get("months") or 1) if str(request.POST.get("months") or "1").isdigit() else 1
    if months not in bs.PERIODS:
        months = 1
    before = bs.offer(request.user, t, months)
    try:
        how, sub = bs.change(request.user, t, months)
    except bs.NotEnough as e:
        messages.error(request, _("Балансда пул етмайди: керак {0} сўм, балансда {1} сўм. Аввал балансни тўлдиринг.")
                       .format(_fmt(e.need), _fmt(e.balance)))
        return redirect(reverse("billing:profile") + "#tariffs")
    except bs.BillingError as e:
        messages.error(request, str(e))
    else:
        if how == "next":
            cur = bs.current_sub(request.user)
            end = cur.end if cur else timezone.now()
            messages.success(request, _("«{0}» тарифига {1:%d.%m.%Y} дан ўтасиз — ҳозирги тарифингиз шу кунгача ишлайди. "
                                        "Пул ўша куни балансдан ечилади.").format(t.name, timezone.localtime(end)))
        else:
            msg = _("«{0}» тарифи ёқилди — {1:%d.%m.%Y} гача.").format(t.name, timezone.localtime(sub.end))
            if before.kind == "upgrade" and before.back:
                msg += " " + _("Олдинги тарифдан ортиб қолган {0} сўм балансга қайтди.").format(_fmt(before.back))
            elif before.kind == "upgrade" and before.credit:
                msg += " " + _("Олдинги тарифдан {0} сўм ҳисобга олинди, балансдан {1} сўм ечилди.").format(
                    _fmt(before.credit), _fmt(before.pay))
            messages.success(request, msg)
    return redirect("billing:profile")


@require_POST
def cancel_next(request):
    bs.cancel_next(request.user)
    messages.success(request, _("Тариф алмашиши бекор қилинди — ҳозирги тарифингиз давом этади."))
    return redirect("billing:profile")


@require_POST
def renew(request):
    bs.account(request.user)
    on = request.POST.get("auto_renew") == "1"
    Account.objects.filter(user=request.user).update(auto_renew=on)
    messages.success(request, _("Тариф муддати тугаганда балансдан ўзи янгиланади.") if on else
                     _("Ўзи янгиланиш ўчирилди — тариф муддати тугагач, бепул тарифга ўтасиз."))
    return redirect("billing:profile")


def need_verify(user) -> bool:
    """Email тасдиқланмаган бўлса — пул тўлаш ва тариф сотиб олиш ёпиқ: парол унутилса ҳисобни (ва пулни)
    email орқали тиклаб бўлмайди. Ходимлар тўламайди — уларга тегишли эмас."""
    return not getattr(user, "email_verified", False) and not bs.exempt(user)


def _verify_first(request):
    messages.error(request, _("Аввал email манзилингизни тасдиқланг: парол эсдан чиқса, ҳисобингиз ва ундаги пул "
                              "фақат шу email орқали тикланади."))
    return redirect("billing:profile")


def _amount(raw) -> int:
    """«50 000», «50000» — бўлади; «50.5», «-100», «1e6» — йўқ (0)."""
    s = str(raw or "").replace(" ", "").replace(" ", "").replace(" ", "").replace(",", "")
    return int(s) if s.isdigit() and len(s) <= 12 else 0


@require_POST
def topup(request):
    """Балансни тўлдириш: суммани текшириб, тўлов тизимига юборади. Email тасдиқланмаган бўлса — йўқ."""
    if need_verify(request.user):
        return _verify_first(request)
    amount = _amount(request.POST.get("amount"))
    lo, hi = bs.topup_limits()
    if not lo <= amount <= hi:
        messages.error(request, _("Сумма {0} дан {1} сўмгача бўлсин.").format(_fmt(lo), _fmt(hi)))
        return redirect("billing:profile")
    if not click_ready():
        messages.error(request, _("Онлайн тўлов ҳали уланмаган. Балансни тўлдириш учун «Ёрдам хизмати»га ёзинг."))
        return redirect("billing:profile")
    from . import click
    return redirect(click.pay_url(request, amount))


def _click_view(fn):
    @csrf_exempt
    @login_not_required
    @require_POST
    def view(request):
        from . import click
        d = request.POST.dict()
        res = getattr(click, fn)(d)
        if res["error"] != click.OK:
            click.log.info("Click %s: %s (%s) — %s", fn, res["error"], res["error_note"], d.get("merchant_trans_id"))
        return JsonResponse(res)
    view.__name__ = f"click_{fn}"
    return view


click_prepare = _click_view("prepare")
click_complete = _click_view("complete")

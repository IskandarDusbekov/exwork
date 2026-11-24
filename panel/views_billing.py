"""Панел: «Тўлов ва тарифлар» — тарифлар (қўшиш, ўзгартириш, сотувдан олиш), умумий ҳолат, дафтар ва тўловлар;
фойдаланувчи саҳифасида — баланс, тариф, қўлда пул қўшиш / айириш, тариф совға қилиш.
Кўриш — ``is_staff``; ўзгартириш — суперфойдаланувчи. Ҳаммаси журналга ёзилади. Пул — бутун сўмда."""
import datetime

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing import services as bs
from billing.models import Account, Entry, Payment, Subscription, Tariff, Unlock

from . import conf
from .audit import log as audit
from .models import Event
from .views import _ctx, staff_required, superuser_required

User = get_user_model()


def _int(raw, lo=0, hi=10 ** 12):
    s = ("" if raw is None else str(raw)).replace(" ", "").replace(" ", "").strip()
    if not s.lstrip("-").isdigit():
        raise ValueError
    v = int(s)
    if not lo <= v <= hi:
        raise ValueError
    return v


TABS = (("overview", "Умумий"), ("tariffs", "Тарифлар"), ("settings", "Созламалар"), ("ledger", "Дафтар"),
        ("payments", "Тўловлар"), ("unlocks", "Юклаб олинганлар"), ("log", "Журнал"))
SPEND = (Entry.Kind.SUBSCRIPTION, Entry.Kind.ROWS, Entry.Kind.OBJECT)


def _sum(qs, field="amount") -> int:
    return qs.aggregate(s=Sum(field))["s"] or 0


def _money(ms) -> dict:
    """Ҳақиқий пул ва бонус алоҳида: тушум (Click), берилган бонус, сарфланган (ҳақиқий / бонус), баланслардаги қолдиқ."""
    paid = Payment.objects.filter(status=Payment.Status.PAID)
    spend_m = Entry.objects.filter(created__gte=ms, kind__in=SPEND)
    spend_all = Entry.objects.filter(kind__in=SPEND)
    bonus_in = Entry.objects.filter(kind=Entry.Kind.BONUS, amount__gt=0)
    admin = Entry.objects.filter(kind=Entry.Kind.ADMIN)
    now = timezone.now()
    gifts = Subscription.objects.filter(gift=True, start__lte=now, end__gt=now).select_related("tariff")
    return {
        "in_month": _sum(paid.filter(paid_at__gte=ms)), "in_month_n": paid.filter(paid_at__gte=ms).count(),
        "in_all": _sum(paid), "in_all_n": paid.count(),
        # сарфланган = (amount − bonus) — ҳақиқий қисми; bonus — бонус қисми (иккаласи ҳам манфий)
        "spent_real_month": -(_sum(spend_m) - _sum(spend_m, "bonus")), "spent_bonus_month": -_sum(spend_m, "bonus"),
        "spent_real_all": -(_sum(spend_all) - _sum(spend_all, "bonus")), "spent_bonus_all": -_sum(spend_all, "bonus"),
        "bonus_month": _sum(bonus_in.filter(created__gte=ms), "bonus"), "bonus_all": _sum(bonus_in, "bonus"),
        "admin_month": _sum(admin.filter(created__gte=ms)),
        "bal_real": _sum(Account.objects.filter(balance__gt=0), "balance"),
        "bal_bonus": _sum(Account.objects.all(), "bonus"),
        "accounts": Account.objects.filter(Q(balance__gt=0) | Q(bonus__gt=0)).count(),
        "subs_paid": Subscription.objects.filter(gift=False, start__lte=now, end__gt=now).count(),
        "subs_gift": gifts.count(), "gift_value": sum(g.tariff.price * g.months for g in gifts),
    }


def _page(request, qs, n=50):
    return Paginator(qs, n).get_page(request.GET.get("page"))


@staff_required
def billing_page(request):
    tab = request.GET.get("tab") if request.GET.get("tab") in dict(TABS) else "overview"
    now = timezone.now()
    ms = bs.month_start(now)
    from billing import click
    from django.urls import reverse
    ctx = {"btab": tab, "btabs": TABS, "on": bs.enabled(), "row_price": bs.row_price(), "object_price": bs.object_price(),
           "free": bs.free_tariff(), "click": {
               "ready": click.ready(), "service_id": click.conf("SERVICE_ID"),
               "prepare": request.build_absolute_uri(reverse("billing:click_prepare")),
               "complete": request.build_absolute_uri(reverse("billing:click_complete"))}}
    if tab == "overview":
        ctx.update(m=_money(ms), month_name=timezone.localtime(ms).strftime("%m.%Y"),
                   recent=Payment.objects.exclude(status=Payment.Status.CREATED).select_related("user")[:8])
    elif tab == "tariffs":
        tariffs = list(Tariff.objects.annotate(
            active_subs=Count("subscriptions", filter=Q(subscriptions__start__lte=now, subscriptions__end__gt=now)),
            all_subs=Count("subscriptions")))
        for t in tariffs:
            t.prices = [bs.price_for(t, m) for m in bs.PERIODS] if not t.free else []
        ctx.update(tariffs=tariffs, periods=[(m, bs.period_discount(m)) for m in bs.PERIODS])
    elif tab == "settings":
        rows = [{"f": f, "value": conf.shown(f.key), "default": conf.default(f.key),
                 "changed": conf.shown(f.key) != conf.default(f.key)} for f in conf.FIELDS if f.group == conf.BILLING_GROUP]
        ctx.update(rows=rows)
    elif tab == "ledger":
        qs = Entry.objects.select_related("user", "actor")
        kind, wallet, q = request.GET.get("kind", ""), request.GET.get("wallet", ""), (request.GET.get("q") or "").strip()
        if kind in dict(Entry.Kind.choices):
            qs = qs.filter(kind=kind)
        if wallet == "bonus":
            qs = qs.exclude(bonus=0)
        elif wallet == "real":
            qs = qs.exclude(amount=F("bonus"))
        if q:
            qs = qs.filter(Q(user__email__icontains=q) | Q(note__icontains=q))
        ctx.update(page=_page(request, qs), kinds=Entry.Kind.choices, f_kind=kind, f_wallet=wallet, f_q=q,
                   totals={"real": _sum(qs) - _sum(qs, "bonus"), "bonus": _sum(qs, "bonus")})
    elif tab == "payments":
        qs = Payment.objects.select_related("user")
        st = request.GET.get("status", "")
        qs = qs.filter(status=st) if st in dict(Payment.Status.choices) else qs.exclude(status=Payment.Status.CREATED)
        ctx.update(page=_page(request, qs), statuses=Payment.Status.choices, f_status=st,
                   total=_sum(qs.filter(status=Payment.Status.PAID)))
    elif tab == "unlocks":
        ctx.update(page=_page(request, Unlock.objects.filter(free=False).select_related("user")))
    else:
        qs = Event.objects.filter(Q(kind=Event.Kind.ADMIN_BILLING) |
                                  Q(kind=Event.Kind.ADMIN_SETTINGS, detail__startswith="Тўлов:")).select_related("actor", "target")
        ctx.update(page=_page(request, qs))
    return render(request, "panel/billing.html", _ctx(request, "billing", **ctx))


@require_POST
@superuser_required
def billing_settings_save(request):
    """Тўлов созламалари (пулли режим, нархлар, чегирмалар, тўлдириш чегаралари) — «Тўлов ва тарифлар» саҳифасидан."""
    fields = [f for f in conf.FIELDS if f.group == conf.BILLING_GROUP]
    back = reverse("panel:billing") + "?tab=settings"
    try:
        changes = {f.key: conf.parse(f, request.POST.get(f.key, "0" if f.kind == "bool" else "")) for f in fields}
    except ValueError as e:
        messages.error(request, str(e))
        return redirect(back)
    if changes.get("billing_topup_max", 0) < changes.get("billing_topup_min", 0):
        messages.error(request, "Энг кўп сумма энг камидан кичик бўлмасин")
        return redirect(back)
    done = conf.save(changes, request.user)
    for label, old, new in done:
        f = next(x for x in fields if x.label == label)
        fmt = (lambda v: "ҳа" if v else "йўқ") if f.kind == "bool" else (lambda v: f"{v} {f.unit}".strip())
        audit(request, Event.Kind.ADMIN_SETTINGS, detail=f"Тўлов: {label}: {fmt(old)} → {fmt(new)}")
    messages.success(request, f"Сақланди ({len(done)} та ўзгариш)" if done else "Ўзгариш йўқ")
    return redirect(back)


@require_POST
@superuser_required
def tariff_save(request, pk=None):
    t = get_object_or_404(Tariff, pk=pk) if pk else Tariff()
    back = reverse("panel:billing") + "?tab=tariffs"
    old = (t.name, t.price, t.rows, t.objects_limit, t.active, t.discount, t.discount_until) if pk else None
    try:
        t.name = (request.POST.get("name") or "").strip()[:60]
        if not t.name:
            raise ValueError
        t.price = _int(request.POST.get("price"), 0, 10 ** 9)
        t.rows = _int(request.POST.get("rows"), 0, 10 ** 9)
        t.objects_limit = _int(request.POST.get("objects_limit"), 0, 10 ** 6)
        t.order = _int(request.POST.get("order") or 0, 0, 999)
    except ValueError:
        messages.error(request, "Номи бўлсин; нарх, қатор, объект ва тартиб — бутун сон")
        return redirect(back)
    t.note = (request.POST.get("note") or "").strip()[:200]
    t.details = (request.POST.get("details") or "").replace("\r", "").strip()[:4000]
    t.active = request.POST.get("active") == "1"
    try:
        t.discount = _int(request.POST.get("discount") or 0, 0, 90)
        raw = (request.POST.get("discount_until") or "").strip()
        t.discount_until = datetime.date.fromisoformat(raw) if raw else None
    except ValueError:
        messages.error(request, "Чегирма — 0 дан 90 гача фоиз; муддати — сана ёки бўш")
        return redirect(back)
    if t.price == 0 and Tariff.objects.filter(price=0).exclude(pk=t.pk).exists():
        messages.warning(request, "Бепул тариф иккита бўлди — обунаси йўқлар биринчисида («Тартиб» кичиги)")
    t.save()
    new = (t.name, t.price, t.rows, t.objects_limit, t.active, t.discount, t.discount_until)
    audit(request, Event.Kind.ADMIN_BILLING, detail=(
        f"тариф «{t.name}»: {old} → {new}" if old else f"янги тариф «{t.name}»: {t.price} сўм, {t.rows} қатор, {t.objects_limit} объект"))
    messages.success(request, "Тариф сақланди. Аввал сотиб олинган обуналар тугагунча эски шартида қолади.")
    return redirect(back)


@require_POST
@superuser_required
def tariff_delete(request, pk):
    t = get_object_or_404(Tariff, pk=pk)
    if t.subscriptions.exists():
        t.active = False
        t.save(update_fields=["active"])
        messages.warning(request, f"«{t.name}» тарифида обуналар бор — ўчирилмади, сотувдан олинди")
        audit(request, Event.Kind.ADMIN_BILLING, detail=f"тариф «{t.name}» сотувдан олинди")
    else:
        audit(request, Event.Kind.ADMIN_BILLING, detail=f"тариф «{t.name}» ўчирилди")
        t.delete()
        messages.success(request, "Тариф ўчирилди")
    return redirect(reverse("panel:billing") + "?tab=tariffs")


def user_billing(u) -> dict:
    """Фойдаланувчи саҳифасидаги «Баланс ва тариф» бўлими учун."""
    return {"acc": bs.account(u), "plan": bs.plan(u), "entries": u.ledger.select_related("actor")[:30],
            "subs": u.subscriptions.select_related("tariff")[:10], "payments": u.payments.all()[:10],
            "tariffs": Tariff.objects.exclude(price=0), "unlocks": u.unlocks.filter(free=False)[:20]}


@require_POST
@superuser_required
def user_balance(request, pk):
    """Қўлда пул қўшиш (+) ёки айириш (−), сабаби билан. «Бонус» — қайтарилмайдиган пул."""
    u = get_object_or_404(User, pk=pk)
    back = f"{redirect('panel:user', pk=u.pk).url}?tab=billing"
    try:
        amount = _int(request.POST.get("amount"), -10 ** 9, 10 ** 9)
        if not amount:
            raise ValueError
    except ValueError:
        messages.error(request, "Сумма — нолдан фарқли бутун сон (айириш учун минус билан)")
        return redirect(back)
    note = (request.POST.get("note") or "").strip()
    if not note:
        messages.error(request, "Сабабини ёзинг — дафтарда ва журналда қолади")
        return redirect(back)
    is_bonus = request.POST.get("bonus") == "1"
    kind = Entry.Kind.BONUS if is_bonus else Entry.Kind.ADMIN
    # Бонус белгиси — фақат бонус ҳамёни (қўшиш ҳам, айириш ҳам); белгисиз — ҳақиқий пул (нақд / банк орқали тўлов)
    try:
        e = bs.post(u, amount, kind, note, actor=request.user, bonus=amount if is_bonus else 0)
    except bs.NotEnough as ex:
        messages.error(request, f"{'Бонус' if is_bonus else 'Ҳақиқий пул'} манфий бўлиб қолади: бор {ex.balance} сўм")
        return redirect(back)
    audit(request, Event.Kind.ADMIN_BILLING, target=u, detail=f"{u.email}: баланс {amount:+} сўм ({e.get_kind_display()}) — {note}")
    messages.success(request, f"Ҳақиқий пул: {e.balance_after} сўм · бонус: {e.bonus_after} сўм")
    return redirect(back)


@require_POST
@superuser_required
def user_gift(request, pk):
    """Тарифни бир ойга бепул бериш (балансдан ечилмайди, ўзи янгиланмайди)."""
    u = get_object_or_404(User, pk=pk)
    t = get_object_or_404(Tariff, pk=request.POST.get("tariff"))
    try:
        sub = bs.buy(u, t, gift=True, actor=request.user)
    except bs.BillingError as e:
        messages.error(request, str(e))
    else:
        audit(request, Event.Kind.ADMIN_BILLING, target=u, detail=f"{u.email}: «{t.name}» тарифи совға, {timezone.localtime(sub.end):%d.%m.%Y} гача")
        messages.success(request, f"«{t.name}» тарифи {timezone.localtime(sub.end):%d.%m.%Y} гача берилди")
    return redirect(f"{redirect('panel:user', pk=u.pk).url}?tab=billing")


@require_POST
@superuser_required
def user_sub_end(request, pk, sid):
    """Ҳозирги обунани шу ондан тугатиш (пул қайтарилмайди — керак бўлса «Баланс»дан қўлда)."""
    u = get_object_or_404(User, pk=pk)
    sub = get_object_or_404(Subscription, pk=sid, user=u)
    now = timezone.now()
    if sub.end > now:
        sub.end = max(now, sub.start)
        sub.save(update_fields=["end"])
        bs.account(u)
        Account.objects.filter(user=u).update(auto_renew=False)  # тугатилгани эртага ўзи янгиланиб қолмасин
        audit(request, Event.Kind.ADMIN_BILLING, target=u, detail=f"{u.email}: «{sub.tariff.name}» обунаси тугатилди")
        messages.success(request, "Обуна тугатилди, ўзи янгиланиш ўчирилди")
    return redirect(f"{redirect('panel:user', pk=u.pk).url}?tab=billing")

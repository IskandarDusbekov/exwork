"""Тўлов қоидалари — бир жойда.

* Пулли режим ўчиқ (панел «Созламалар» → «Тўлов») — ҳамма нарса бепул, ҳеч нарса ечилмайди.
* Ёқиқ: файл юклаб олинганда (``charge``) — аввал тариф ҳисобидан (қатор / объект), етмаса — балансдан
  (қатор нархи, объект-ой нархи). Балансда ҳам етмаса — ``NotEnough`` (юклаб олиш ёпилади).
* Бир файл бир марта: ``Unlock(user, key)`` бор бўлса — қайта юклаб олиш бепул.
* Обуна — 1, 3, 6 ёки 12 ой (узоқ муддатга чегирма, панелда; тарифнинг ўз чегирмаси ҳам). Қатор ва объектлар
  ҳар ойга алоҳида, ортиб қолгани кейинги ойга ўтмайди. Тугаганда (``plan`` чақирилганда) ўзи янгиланади
  (``Account.auto_renew``), пул етмаса — бепул тарифга тушади.
* Икки ҳамён: ҳақиқий пул (Click) ва бонус (совға) аралашмайди; сарфлашда аввал бонус.
* Баланс фақат ``post`` орқали ўзгаради: ҳисоб қатори қулфланади (``select_for_update``), дафтарга
  қолдиғи билан ёзилади, ``ref`` — бир амал икки марта ёзилмайди."""
import calendar
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Account, Entry, Subscription, Tariff, Unlock


class BillingError(Exception):
    pass


class NotEnough(BillingError):
    """Балансда пул етмайди. ``need`` — қанча керак, ``balance`` — қанча бор."""

    def __init__(self, need: int, balance: int):
        self.need, self.balance = need, balance
        super().__init__(_("Балансда пул етмайди: керак {0} сўм, бор {1} сўм").format(need, balance))


# ---------------- созламалар ----------------
def enabled() -> bool:
    from panel import conf
    return bool(conf.get("billing_on"))


def row_price() -> int:
    from panel import conf
    return int(conf.get("billing_row_price"))


def object_price() -> int:
    from panel import conf
    return int(conf.get("billing_object_price"))


def topup_limits() -> Tuple[int, int]:
    """Бир тўловда энг кам ва энг кўп сумма (панелда). Нотўғри ёзилган бўлса (энг кўпи кичик) — энг ками ҳам чегара."""
    from panel import conf
    lo, hi = int(conf.get("billing_topup_min")), int(conf.get("billing_topup_max"))
    return lo, max(lo, hi)


def exempt(user) -> bool:
    """Ходимлар (панелга кирадиганлар) тўламайди."""
    return bool(user.is_staff)


# ---------------- ҳисоб ва дафтар ----------------
def account(user) -> Account:
    acc, _c = Account.objects.get_or_create(user=user)
    return acc


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def post(user, amount: int, kind: str, note: str = "", ref: Optional[str] = None, actor=None,
         allow_negative: bool = False, bonus: Optional[int] = None) -> Entry:
    """Ҳисобга ёзиш (+ кирим, − чиқим). Икки ҳамён (``Account.balance`` — ҳақиқий, ``bonus`` — совға):
    * кирим: ``kind == BONUS`` — бонусга, қолгани — ҳақиқий пулга (``bonus`` билан аниқ улуш бериш мумкин);
    * чиқим: аввал бонусдан, етмагани — ҳақиқий пулдан (``bonus`` берилса — шунча бонусдан).
    ``ref`` бор бўлса ва аввал ёзилган бўлса — ўша ёзув қайтади (иккинчи марта ёзилмайди).
    Жами пулдан катта чиқим — ``NotEnough`` (``allow_negative`` — админ тузатиши, ҳақиқий пул манфий бўлади)."""
    amount = int(amount)
    with transaction.atomic():
        if ref:
            old = Entry.objects.filter(ref=ref).first()
            if old:
                return old
        account(user)
        acc = Account.objects.select_for_update().get(user=user)
        if bonus is None:
            bonus = (amount if kind == Entry.Kind.BONUS else 0) if amount >= 0 else -min(max(0, acc.bonus), -amount)
        bonus = int(bonus)
        real = amount - bonus
        if real < 0 and acc.balance + real < 0 and not allow_negative:  # ҳамёнлар алоҳида: бонус ҳақиқийни ёпмайди
            raise NotEnough(-amount, acc.total) if bonus == -min(max(0, acc.bonus), -amount) else NotEnough(-real, acc.balance)
        if acc.bonus + bonus < 0:
            raise NotEnough(-bonus, acc.bonus)
        acc.balance += real
        acc.bonus += bonus
        acc.save(update_fields=["balance", "bonus", "updated"])
        try:
            with transaction.atomic():
                return Entry.objects.create(user=user, kind=kind, amount=amount, bonus=bonus, balance_after=acc.balance,
                                            bonus_after=acc.bonus, note=note[:255], ref=ref, actor=actor)
        except IntegrityError:  # бир вақтда иккита бир хил ``ref`` — иккинчиси бекор
            raise BillingError(_("Бу амал аввал ёзилган"))


# ---------------- тариф ва давр ----------------
def add_month(d: datetime, n: int = 1) -> datetime:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def free_tariff() -> Optional[Tariff]:
    return Tariff.objects.filter(price=0).order_by("order", "id").first()


def month_start(now=None) -> datetime:
    now = timezone.localtime(now or timezone.now())
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass
class Plan:
    tariff: Optional[Tariff]
    sub: Optional[Subscription]
    start: datetime
    end: datetime
    rows: int
    objects: int
    rows_used: int
    objects_used: int

    @property
    def rows_left(self) -> int:
        return max(0, self.rows - self.rows_used)

    @property
    def objects_left(self) -> int:
        return max(0, self.objects - self.objects_used)

    @property
    def rows_pct(self) -> float:
        return min(100.0, 100 * self.rows_used / self.rows) if self.rows else 0.0

    @property
    def objects_pct(self) -> float:
        return min(100.0, 100 * self.objects_used / self.objects) if self.objects else 0.0


def current_sub(user, now=None) -> Optional[Subscription]:
    now = now or timezone.now()
    return Subscription.objects.filter(user=user, start__lte=now, end__gt=now).select_related("tariff").first()


def plan(user, now=None) -> Plan:
    """Ҳозирги давр: пулли обуна (бўлса) ёки бепул тариф (календарь ой). Тугаган обуна — ўзи янгиланади."""
    now = now or timezone.now()
    sub = current_sub(user, now) or renew(user, now)
    if sub:
        (start, end), tariff, rows, objs = _window(sub, now), sub.tariff, sub.rows, sub.objects_limit
    else:
        tariff = free_tariff()
        start = month_start(now)
        end = add_month(start)
        rows, objs = (tariff.rows, tariff.objects_limit) if tariff else (0, 0)
    used = Unlock.objects.filter(user=user, created__gte=start, created__lt=end, free=False)
    agg = used.aggregate(r=Sum("quota_rows"))
    return Plan(tariff=tariff, sub=sub, start=start, end=end, rows=rows, objects=objs,
                rows_used=agg["r"] or 0, objects_used=used.filter(quota_object=True).count())


PERIODS = (1, 3, 6, 12)


def period_discount(months: int) -> int:
    """Узоқ муддат чегирмаси, % (панелда): 3, 6, 12 ой."""
    if months not in PERIODS or months == 1:
        return 0
    from panel import conf
    return max(0, min(90, int(conf.get(f"billing_disc_{months}"))))


@dataclass
class Price:
    """Тариф нархи шу муддатга: ``base`` — чегирмасиз, ``total`` — тўланадиган, ``per_month`` — ойига."""
    months: int
    base: int
    total: int
    period_pct: int
    tariff_pct: int

    @property
    def per_month(self) -> int:
        return self.total // self.months if self.months else self.total

    @property
    def saved(self) -> int:
        return self.base - self.total

    @property
    def pct(self) -> int:
        """Жами чегирма фоизи (иккаласи бирга)."""
        return round(100 * self.saved / self.base) if self.base else 0


def price_for(tariff: Tariff, months: int = 1) -> Price:
    """Нарх = ойлик нарх × ой × (1 − муддат чегирмаси) × (1 − тариф чегирмаси), 100 сўмгача яхлитланади."""
    months = months if months in PERIODS else 1
    base = tariff.price * months
    pp, tp = period_discount(months), tariff.discount_now
    total = base * (100 - pp) * (100 - tp) / 10000
    total = int(total // 100 * 100 + (100 if total % 100 >= 50 else 0))
    return Price(months=months, base=base, total=min(base, max(0, total)), period_pct=pp, tariff_pct=tp)


def _window(sub: Subscription, now) -> Tuple[datetime, datetime]:
    """Кўп ойлик обунанинг ҳозирги ойи: қатор ва объектлар ҳар ойга алоҳида."""
    k = 0
    while k < sub.months - 1 and add_month(sub.start, k + 1) <= now:
        k += 1
    return add_month(sub.start, k), min(add_month(sub.start, k + 1), sub.end)


def renew(user, now=None) -> Optional[Subscription]:
    """Охирги пулли обуна тугаган бўлса — балансдан яна шунча муддатга (ҳозирдан):
    * «кейинги даврдан» тариф танланган бўлса — шуниси (ўзи янгиланиш ўчиқ бўлса ҳам — фойдаланувчи ўзи танлаган);
    * акс ҳолда «ўзи янгилансин» ёқиқ бўлса — ўша тариф, ўша муддат.
    Пул етмаса, тариф сотувдан олинган ёки совға бўлса — янгиланмайди (бепул тарифга тушади)."""
    now = now or timezone.now()
    last = Subscription.objects.filter(user=user, end__lte=now).select_related("tariff").first()
    if not last:
        return None
    acc = account(user)
    nxt = acc.next_tariff if acc.next_tariff_id and acc.next_tariff.active and not acc.next_tariff.free else None
    if nxt is not None:
        target, months = nxt, acc.next_months
    elif acc.auto_renew and not last.gift and last.tariff.active and not last.tariff.free:
        target, months = last.tariff, last.months
    else:
        return None
    if acc.total < price_for(target, months).total:
        return None
    try:
        sub = buy(user, target, months=months, now=now, renewal_of=last)
    except BillingError:
        return None
    if nxt is not None:
        Account.objects.filter(user=user, next_tariff=nxt).update(next_tariff=None, next_months=1)
    return sub


def credit(user, now=None) -> int:
    """Ҳозирги пулли тарифнинг ишлатилмаган қисми (қимматроқ тарифга ўтганда ҳисобга олинади).
    Бошланмаган ойлар — тўлиқ; ҳозирги ой — қолган кунлар ва қолган қаторлардан КИЧИГИ (2 кунда ҳамма
    қаторни ишлатган одам кўп қайтиб олмасин, қатор ишлатмай 29 кун ўтказган ҳам). Совға — 0."""
    now = now or timezone.now()
    sub = current_sub(user, now)
    if not sub or sub.gift or not sub.price:
        return 0
    per_month = sub.price / max(1, sub.months)
    ws, we = _window(sub, now)
    months_after = 0
    while add_month(we, months_after) < sub.end and months_after < sub.months:
        months_after += 1
    total = (we - ws).total_seconds()
    f_time = max(0.0, (we - now).total_seconds() / total) if total > 0 else 0.0
    if sub.rows:
        used = Unlock.objects.filter(user=user, created__gte=ws, created__lt=we, free=False) \
            .aggregate(r=Sum("quota_rows"))["r"] or 0
        f_rows = max(0.0, (sub.rows - used) / sub.rows)
    else:
        f_rows = 1.0
    return min(sub.price, int(per_month * (months_after + min(f_time, f_rows, 1.0))))


@dataclass
class Offer:
    """Тариф картаси учун: нима қилиш мумкин ва қанча ечилади.
    kind: current | scheduled | next (арзонроқ ёки бошқа муддат — кейинги даврдан) | upgrade (қимматроқ — ҳозир,
    фарқи) | buy | free. ``back`` — ҳозиргисидан ортиб қолгани (балансга қайтади)."""
    kind: str
    price: Optional[Price] = None
    pay: int = 0
    credit: int = 0
    back: int = 0


def offer(user, tariff: Tariff, months: int = 1, now=None, p: Optional[Plan] = None) -> Offer:
    now = now or timezone.now()
    p = p or plan(user, now)
    if tariff.free:
        return Offer("current" if not p.sub else "free")
    pr = price_for(tariff, months)
    sub = p.sub
    if sub is None:
        return Offer("buy", price=pr, pay=pr.total)
    if sub.tariff_id == tariff.pk and sub.months == pr.months:
        return Offer("current", price=pr)
    if tariff.price > sub.tariff.price:
        c = credit(user, now)
        return Offer("upgrade", price=pr, pay=max(0, pr.total - c), credit=min(c, pr.total), back=max(0, c - pr.total))
    acc = account(user)
    sched = acc.next_tariff_id == tariff.pk and acc.next_months == pr.months
    return Offer("scheduled" if sched else "next", price=pr, pay=pr.total)


def change(user, tariff: Tariff, months: int = 1, now=None) -> Tuple[str, Optional[Subscription]]:
    """Фойдаланувчи тарифни танлади. Қайтаради: ("now", обуна) ёки ("next", None).
    * пулли тариф йўқ — балансдан, бугундан ``months`` ой;
    * қимматроқ — ҳозир ўтади, ҳозиргисининг ишлатилмаган қисми айрилади (``credit``), янги давр бугундан;
    * арзонроқ (ёки шу тариф, бошқа муддат) — ҳозиргиси охиригача ишлайди, кейин шуниси (пул ўшанда ечилади);
    * худди шу тариф ва муддат — ўзгармайди (тугагач ўзи янгиланади)."""
    now = now or timezone.now()
    o = offer(user, tariff, months, now)
    if o.kind in ("current", "free"):
        raise BillingError(_("Бу тариф ҳозир ёқиқ — муддати тугагач ўзи янгиланади."))
    if o.kind in ("next", "scheduled"):
        account(user)
        Account.objects.filter(user=user).update(next_tariff=tariff, next_months=o.price.months)
        return "next", None
    return "now", buy(user, tariff, months=months, now=now)


def cancel_next(user):
    account(user)
    Account.objects.filter(user=user).update(next_tariff=None, next_months=1)


def buy(user, tariff: Tariff, months: int = 1, now=None, renewal_of: Optional[Subscription] = None,
        gift: bool = False, actor=None) -> Subscription:
    """Тариф сотиб олиш: балансдан (аввал бонус) ечилади, бугундан ``months`` ой. Ҳозирги пулли обуна бўлса —
    у шу ондан тугайди, ишлатилмаган қисми (``credit``) нархдан айрилади; нархдан ортиб қолса — балансга қайтади
    (ҳақиқий / бонус улушича). ``gift`` — админ бепул беради."""
    now = now or timezone.now()
    if tariff.free:
        raise BillingError(_("Бепул тарифни сотиб олиш шарт эмас"))
    if not tariff.active and not gift:
        raise BillingError(_("Бу тариф ҳозир сотувда йўқ"))
    pr = price_for(tariff, months)
    with transaction.atomic():
        account(user)
        Account.objects.select_for_update().get(user=user)  # бир вақтда иккита сотиб олиш — навбат билан
        if renewal_of is not None and Subscription.objects.filter(user=user, start__gte=renewal_of.end).exists():
            return Subscription.objects.filter(user=user, start__gte=renewal_of.end).first()
        old = current_sub(user, now)
        value = 0 if gift else pr.total
        c = 0 if gift or renewal_of is not None else credit(user, now)
        old_bonus_share = (old.paid_bonus / old.price) if old and old.price else 0.0
        sub = Subscription.objects.create(user=user, tariff=tariff, price=value, months=pr.months, rows=tariff.rows,
                                          objects_limit=tariff.objects_limit, start=now,
                                          end=add_month(now, pr.months), gift=gift)
        pay = max(0, value - c)
        paid_bonus = int(min(c, value) * old_bonus_share)
        if pay:
            note = _("{0} тарифи, {1} ой, {2:%d.%m.%Y} гача").format(tariff.name, pr.months, timezone.localtime(sub.end))
            if pr.saved:
                note += " · " + _("чегирма {0}%").format(pr.pct)
            if c:
                note += " · " + _("олдинги тарифнинг ишлатилмаган {0} сўми ҳисобга олинди").format(_fmt(c))
            e = post(user, -pay, Entry.Kind.SUBSCRIPTION, note, ref=f"sub:{sub.pk}", actor=actor)
            paid_bonus += -e.bonus
        back = max(0, c - value)
        if back:  # олдинги тариф янгисидан қиммат эди — ортгани балансга (қайси ҳамёндан келган бўлса, ўшанга)
            b = int(back * old_bonus_share)
            post(user, back, Entry.Kind.REFUND, _("олдинги тарифдан ортиб қолгани"), ref=f"sub:{sub.pk}:back", bonus=b)
        if paid_bonus:
            Subscription.objects.filter(pk=sub.pk).update(paid_bonus=min(paid_bonus, value))
            sub.paid_bonus = min(paid_bonus, value)
        Subscription.objects.filter(user=user, start__lt=now, end__gt=now).exclude(pk=sub.pk).update(end=now)
        Account.objects.filter(user=user).update(next_tariff=None, next_months=1)
    return sub


# ---------------- файл очиш (юклаб олиш) ----------------
@dataclass
class Quote:
    """Юклаб олишдан олдин: бепулми, қанчаси тарифдан, қанчаси балансдан, етадими."""
    paid: bool          # аввал очилган — бепул
    free: bool          # пулли режим ўчиқ ёки ходим
    rows: int = 0
    quota_rows: int = 0
    quota_object: bool = False
    amount: int = 0
    balance: int = 0

    @property
    def ok(self) -> bool:
        return self.paid or self.free or self.amount <= self.balance

    @property
    def costs(self) -> bool:
        return not (self.paid or self.free) and self.amount > 0


def quote(user, key: str, kind: str, rows: int = 0, now=None) -> Quote:
    if not enabled() or exempt(user):
        return Quote(paid=False, free=True, rows=rows)
    if Unlock.objects.filter(user=user, key=key).exists():
        return Quote(paid=True, free=False, rows=rows)
    p = plan(user, now)
    bal = account(user).total
    if kind == Unlock.Kind.OBJECT:
        q = p.objects_left > 0
        return Quote(paid=False, free=False, quota_object=q, amount=0 if q else object_price(), balance=bal)
    from_quota = min(rows, p.rows_left)
    return Quote(paid=False, free=False, rows=rows, quota_rows=from_quota,
                 amount=(rows - from_quota) * row_price(), balance=bal)


def quote_many(user, items, now=None) -> Quote:
    """Бир нечта смета бирданига (папка / объект ZIP): ``items`` — [(key, rows), …]. Аввал очилганлари саналмайди."""
    if not enabled() or exempt(user):
        return Quote(paid=False, free=True, rows=sum(r for _k, r in items))
    done = set(Unlock.objects.filter(user=user, key__in=[k for k, _r in items]).values_list("key", flat=True))
    rows = sum(r for k, r in items if k not in done)
    if not rows and len(done) == len(items):
        return Quote(paid=True, free=False)
    p = plan(user, now)
    from_quota = min(rows, p.rows_left)
    return Quote(paid=False, free=False, rows=rows, quota_rows=from_quota,
                 amount=(rows - from_quota) * row_price(), balance=account(user).total)


def charge(user, key: str, kind: str, title: str = "", rows: int = 0, now=None) -> Unlock:
    """Файлни очиш: тариф ҳисобидан, етмаганини балансдан. Аввал очилган бўлса — ўшаниси (ечилмайди).
    Пул етмаса — ``NotEnough``, ҳеч нарса ёзилмайди."""
    old = Unlock.objects.filter(user=user, key=key).first()
    if old:
        return old
    if not enabled() or exempt(user):
        u, _c = Unlock.objects.get_or_create(user=user, key=key, defaults=dict(kind=kind, title=title[:255],
                                                                                rows=rows, free=True))
        return u
    with transaction.atomic():
        account(user)
        Account.objects.select_for_update().get(user=user)  # шу фойдаланувчининг ечимлари навбат билан
        old = Unlock.objects.filter(user=user, key=key).first()
        if old:
            return old
        q = quote(user, key, kind, rows, now)
        u = Unlock.objects.create(user=user, key=key, kind=kind, title=title[:255], rows=rows,
                                  quota_rows=q.quota_rows, quota_object=q.quota_object, amount=q.amount)
        if q.amount:
            if kind == Unlock.Kind.OBJECT:
                note = _("{0}: шу ой учун").format(title)
                ek = Entry.Kind.OBJECT
            else:
                note = _("{0}: {1} қатор (тарифдан {2})").format(title, rows, q.quota_rows)
                ek = Entry.Kind.ROWS
            post(user, -q.amount, ek, note, ref=f"unlock:{u.pk}")  # етмаса — NotEnough, ҳаммаси бекор
    return u


def quote_items(user, items, now=None) -> Quote:
    """Бир нечта файл бирданига (смета, папка / объект ZIP, офёрта, техназор): ``items`` — [(key, kind, title, rows)].
    Аввал очилганлари саналмайди; қатор ва объектлар тариф ҳисобидан, ошгани — балансдан."""
    if not enabled() or exempt(user):
        return Quote(paid=False, free=True, rows=sum(i[3] for i in items))
    done = set(Unlock.objects.filter(user=user, key__in=[i[0] for i in items]).values_list("key", flat=True))
    todo = [i for i in items if i[0] not in done]
    if not todo:
        return Quote(paid=True, free=False)
    p = plan(user, now)
    rows = sum(i[3] for i in todo if i[1] == Unlock.Kind.ROWS)
    objs = sum(1 for i in todo if i[1] == Unlock.Kind.OBJECT)
    q_rows, q_objs = min(rows, p.rows_left), min(objs, p.objects_left)
    return Quote(paid=False, free=False, rows=rows, quota_rows=q_rows, quota_object=objs > 0 and q_objs == objs,
                 amount=(rows - q_rows) * row_price() + (objs - q_objs) * object_price(), balance=account(user).total)


def charge_items(user, items, now=None) -> int:
    """``quote_items`` даги файлларни очиш — ҳаммаси ёки ҳеч бири (пул етмаса ``NotEnough``, ҳеч нарса ёзилмайди).
    Қайтаради: балансдан ечилган сумма."""
    with transaction.atomic():
        if enabled() and not exempt(user):
            account(user)
            Account.objects.select_for_update().get(user=user)
            q = quote_items(user, items, now)
            if not q.ok:
                raise NotEnough(q.amount, q.balance)
        spent = 0
        for key, kind, title, rows in items:
            spent += charge(user, key, kind, title, rows, now).amount if not Unlock.objects.filter(user=user, key=key).exists() else 0
        return spent


def object_key(obj_id, now=None) -> str:
    return f"obj:{obj_id}:{timezone.localtime(now or timezone.now()):%Y-%m}"


def job_key(job_id) -> str:
    return f"job:{job_id}"

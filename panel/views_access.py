"""Панел «Кириш уринишлари»: ким, қайси саҳифага, неча марта; сканерлар, ботлар, хато киришлар (panel.access)."""
from datetime import timedelta

from django.core.paginator import Paginator
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDay, TruncHour
from django.shortcuts import render
from django.utils import timezone

from accounts.throttle import client_ip

from . import access, sessions
from .models import AccessStat
from .views import _ctx, staff_required

K = AccessStat.Kind
PERIODS = [("24h", "24 соат", timedelta(hours=24)), ("7d", "7 кун", timedelta(days=7)),
           ("30d", "30 кун", timedelta(days=30))]
BAD = [K.SCANNER, K.BAD_HOST]  # заифлик қидирувчилар
TONE = {K.USER: "emerald", K.GUEST: "slate", K.BOT: "sky", K.SCANNER: "rose", K.BAD_HOST: "rose",
        K.LOGIN_FAIL: "amber", K.CSRF: "amber", K.BLOCKED: "violet"}
HINT = {
    K.USER: "Тизимга кирган фойдаланувчилар очган саҳифалар.",
    K.GUEST: "Кирмаган, оддий браузердан келган сўровлар (кириш, рўйхатдан ўтиш саҳифалари).",
    K.BOT: "User-Agent'и бот ёки дастур (curl, python, қидирув тизимлари) ёки умуман йўқ.",
    K.SCANNER: "Сайтда йўқ заиф жойларни қидириш: .php, .env, wp-admin, phpmyadmin, .git…",
    K.BAD_HOST: "Сайт номи эмас, IP ёки бошқа домен билан келган сўров — интернетни сканерлайдиган ботлар.",
    K.LOGIN_FAIL: "Нотўғри пароль ёки икки босқичли код киритилди.",
    K.CSRF: "Ҳимоя белгисисиз юборилган форма (бошқа сайтдан ёки бот) — рад этилди.",
    K.BLOCKED: "Панелда блокланган IP'дан келган сўровлар — ҳеч нарса очилмаган.",
}


def _sum(kind_q):
    return Sum("count", filter=kind_q, default=0)


def _threat(r):
    if r["scan"] >= 10 or r["fail"] >= 10 or r["n"] >= 2000:
        return "attack"
    if r["scan"] or r["fail"] or r["csrf"] or r["nf"] >= 20 or r["blocked"]:
        return "suspect"
    return ""


@staff_required
def access_page(request):
    flush_n = access.flush()  # шу жараённинг хотирасидагиси ҳам кўринсин
    pkey = request.GET.get("p", "24h")
    plabel, delta = next(((label, d) for k, label, d in PERIODS if k == pkey), (None, None))
    if plabel is None:
        pkey, plabel, delta = PERIODS[0]
    since = (timezone.now() - delta).replace(minute=0, second=0, microsecond=0)
    base = AccessStat.objects.filter(hour__gte=since)

    kinds = {r["kind"]: r for r in base.values("kind").annotate(n=Sum("count"), ips=Count("ip", distinct=True))}
    cards = [{"key": k, "label": label, "n": kinds.get(k, {}).get("n", 0), "ips": kinds.get(k, {}).get("ips", 0),
              "tone": TONE[k], "hint": HINT[k]} for k, label in K.choices]
    total = sum(c["n"] for c in cards)
    unique_ips = base.values("ip").distinct().count()
    bad_n = sum(c["n"] for c in cards if c["key"] in BAD)

    # шубҳали IP'лар: сканер, хато кириш, CSRF, кўп 404 ёки жуда кўп сўров
    per_ip = (base.exclude(ip=None).values("ip").annotate(
        n=Sum("count"), scan=_sum(Q(kind__in=BAD)), fail=_sum(Q(kind=K.LOGIN_FAIL)), csrf=_sum(Q(kind=K.CSRF)),
        blocked=_sum(Q(kind=K.BLOCKED)), nf=_sum(Q(status=404)), paths=Count("path", distinct=True),
        users=Count("user", distinct=True), last=Max("last_seen"))
        .filter(Q(scan__gt=0) | Q(fail__gt=0) | Q(csrf__gt=0) | Q(blocked__gt=0) | Q(nf__gte=20) | Q(n__gte=500))
        .order_by("-scan", "-fail", "-n")[:40])
    suspects = [dict(r, threat=_threat(r)) for r in per_ip]
    suspects.sort(key=lambda r: (r["threat"] != "attack", r["threat"] != "suspect", -r["n"]))
    uas = {}
    for row in base.filter(ip__in=[r["ip"] for r in suspects]).order_by("ip", "-last_seen").values("ip", "user_agent"):
        uas.setdefault(row["ip"], row["user_agent"])
    for r in suspects:
        r["ua"] = uas.get(r["ip"], "")

    pages = (base.exclude(kind__in=BAD).values("path").annotate(n=Sum("count"), ips=Count("ip", distinct=True),
             nf=_sum(Q(status=404))).order_by("-n")[:15])
    scan_paths = (base.filter(kind=K.SCANNER).values("path").annotate(n=Sum("count"), ips=Count("ip", distinct=True))
                  .order_by("-n")[:20])
    bad_hosts = (base.filter(kind=K.BAD_HOST).values("host").annotate(n=Sum("count"), ips=Count("ip", distinct=True))
                 .order_by("-n")[:10])
    top_users = (base.filter(kind=K.USER).exclude(user=None).values("user_id", "user__email")
                 .annotate(n=Sum("count"), ips=Count("ip", distinct=True), last=Max("last_seen")).order_by("-n")[:10])

    trunc = TruncHour("hour") if pkey == "24h" else TruncDay("hour")
    got = {s["t"]: s for s in base.annotate(t=trunc).values("t").annotate(
        n=Sum("count"), bad=_sum(Q(kind__in=BAD + [K.LOGIN_FAIL, K.CSRF, K.BLOCKED])))}
    if pkey == "24h":  # бўш соат/кунлар ҳам — устунлар вақт бўйича текис турсин
        slots = [since + timedelta(hours=i) for i in range(25)]
    else:
        day0 = timezone.localtime(since).replace(hour=0)
        slots = [day0 + timedelta(days=i) for i in range(delta.days + 1)]
    series = [got.get(t) or {"t": t, "n": 0, "bad": 0} for t in slots]
    top = max((s["n"] for s in series), default=0) or 1
    for s in series:
        s["h"] = max(1 if s["n"] else 0, round(s["n"] * 100 / top))
        s["bh"] = round(s["bad"] * 100 / top)

    # пастдаги батафсил рўйхат (фильтр билан)
    ip = request.GET.get("ip", "").strip()
    kind = request.GET.get("kind", "")
    q = request.GET.get("q", "").strip()
    rows = base.select_related("user")
    if ip:
        rows = rows.filter(ip=ip) if ip != "-" else rows.filter(ip=None)
    if kind in K.values:
        rows = rows.filter(kind=kind)
    elif kind == "bad":
        rows = rows.exclude(kind__in=[K.USER, K.GUEST])
    if q:
        rows = rows.filter(Q(path__icontains=q) | Q(user_agent__icontains=q) | Q(host__icontains=q)
                           | Q(user__email__icontains=q))
    ip_info = None
    if ip and ip != "-":
        agg = rows.aggregate(n=Sum("count"), last=Max("last_seen"),
                             paths=Count("path", distinct=True))
        first = base.filter(ip=ip).order_by("first_seen").values_list("first_seen", flat=True).first()
        ip_info = dict(agg, first=first, ip=ip,
                       users=list(base.filter(ip=ip).exclude(user=None).values_list("user__email", flat=True).distinct()))
    return render(request, "panel/access.html", _ctx(
        request, "access", period=pkey, periods=[(k, label) for k, label, _ in PERIODS], period_label=plabel,
        cards=cards, total=total, unique_ips=unique_ips, bad_n=bad_n, suspects=suspects, pages=pages,
        scan_paths=scan_paths, bad_hosts=bad_hosts, top_users=top_users, series=series, by_hour=pkey == "24h",
        page=Paginator(rows.order_by("-last_seen"), 50).get_page(request.GET.get("page")),
        ip=ip, kind=kind, q=q, kinds=K.choices, ip_info=ip_info, tones=TONE,
        blocked_ips=sessions.blocked_ips(), my_ip=client_ip(request), here=request.get_full_path(),
        flush_seconds=access.FLUSH_SECONDS, keep_days=access.KEEP_DAYS, flushed=flush_n))

"""Юклаб олиш дарвозаси: тайёр файл берилишидан олдин ``check(request, items, back)`` чақирилади.

* Пулли режим ўчиқ, ходим, аввал очилган ёки тариф ҳисобидан етади — файл дарров берилади (ҳисобга ёзилади).
* Балансдан пул ечилиши керак — аввал тасдиқлаш саҳифаси (402): «X сўм ечилади». «Тасдиқлаш» шу манзилга
  имзоланган ``?pay=`` билан қайтади (бегона ҳавола фойдаланувчи пулини ечолмайди; 15 дақиқа амал қилади).
* Балансда етмайди — «Балансни тўлдиринг» саҳифаси (402), қанча кераклиги билан.
402 — техназордаги fetch юклаш шу кодни кўриб, саҳифанинг ўзини очади (файл ўрнига HTML сақланмасин)."""
from typing import Iterable, List, Optional, Tuple

from django.core import signing
from django.shortcuts import render
from django.utils.translation import gettext as _

from . import services as bs
from .models import Unlock

Item = Tuple[str, str, str, int]  # (key, kind, title, rows)
SALT = "billing-pay"
MAX_AGE = 15 * 60


# ---------------- нима очилади ----------------
def job_items(jobs: Iterable) -> List[Item]:
    """Нархланган сметалар: калити — смета, қатори — ишлов берилган ресурс қаторлари."""
    out = []
    for j in jobs:
        if j.status == "done":
            out.append((bs.job_key(j.id), Unlock.Kind.ROWS, (j.batch_label or j.local_name or "")[:255],
                        int(j.rows_done or 0)))
    return out


def work_jobs(work):
    """Смета / объект (Batch) / папка (Project) — ичидаги нархланган сметалар."""
    from jobs.models import Batch, Job, Project
    if isinstance(work, Job):
        return [work]
    if isinstance(work, Batch):
        return list(work.jobs.filter(status=Job.Status.DONE).only("id", "status", "batch_label", "local_name", "rows_done"))
    if isinstance(work, Project):
        return list(Job.objects.filter(batch__project=work, status=Job.Status.DONE)
                    .only("id", "status", "batch_label", "local_name", "rows_done"))
    return []


def object_items(obj) -> List[Item]:
    """Техназор объекти — шу ой учун бир марта."""
    return [(bs.object_key(obj.pk), Unlock.Kind.OBJECT, obj.name[:255], 0)]


def items_for(work) -> List[Item]:
    from texnazor.models import SiteObject
    if isinstance(work, SiteObject):
        return object_items(work)
    return job_items(work_jobs(work))


# ---------------- дарвоза ----------------
def _token(user, items, amount: int) -> str:
    return signing.dumps({"u": user.pk, "k": sorted(i[0] for i in items), "a": amount}, salt=SALT, compress=True)


def _valid(token: str, user, items, amount: int) -> bool:
    try:
        d = signing.loads(token, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:
        return False
    return d.get("u") == user.pk and d.get("k") == sorted(i[0] for i in items) and d.get("a") == amount


def _page(request, tpl: str, q, items, back: str, **kw):
    return render(request, tpl, {"q": q, "n": len(items), "back": back, "row_price": bs.row_price(),
                                 "object_price": bs.object_price(), "plan": bs.plan(request.user),
                                 "need": max(0, q.amount - q.balance), "paid_rows": max(0, q.rows - q.quota_rows),
                                 "left": q.balance - q.amount, **kw}, status=402)


def check(request, items: List[Item], back: str) -> Optional[object]:
    """``None`` — файлни беринг (пули тўланди ёки бепул); акс ҳолда — кўрсатиладиган саҳифа (402)."""
    if not items:
        return None
    user = request.user
    q = bs.quote_items(user, items)
    if q.paid:
        return None
    if q.free or q.amount == 0:
        bs.charge_items(user, items)
        return None
    if not q.ok:
        return _page(request, "billing/blocked.html", q, items, back)
    token = request.GET.get("pay", "")
    if token and _valid(token, user, items, q.amount):
        try:
            bs.charge_items(user, items)
        except bs.NotEnough:
            return _page(request, "billing/blocked.html", bs.quote_items(user, items), items, back)
        return None
    params = request.GET.copy()
    params["pay"] = _token(user, items, q.amount)
    url = f"{request.path}?{params.urlencode()}"
    return _page(request, "billing/confirm.html", q, items, back, pay_url=url)


def hint(user, work) -> dict:
    """Юклаб олиш тугмаси ёнидаги ёзув учун: {"kind": free|paid|quota|cost|short, "amount": …}."""
    items = items_for(work)
    if not items:
        return {"kind": "free"}
    q = bs.quote_items(user, items)
    if q.free:
        return {"kind": "free"}
    if q.paid:
        return {"kind": "paid"}
    # папка карточкаси учун: аввал тўланган қатор, ҳозир тарифдан, балансдан ва тарифда қоладигани
    done = set(Unlock.objects.filter(user=user, key__in=[i[0] for i in items]).values_list("key", flat=True))
    info = {"rows": q.rows, "quota_rows": q.quota_rows, "paid_rows": q.rows - q.quota_rows,
            "done_rows": sum(i[3] for i in items if i[0] in done), "left_after": bs.plan(user).rows_left - q.quota_rows}
    if q.amount == 0:
        return {"kind": "quota", **info}
    return {"kind": "cost" if q.ok else "short", "amount": q.amount, "need": max(0, q.amount - q.balance), **info}


def label(h: dict) -> str:
    k = h.get("kind")
    if k == "paid":
        return _("тўланган")
    if k == "quota":
        return _("тарифдан")
    if k == "cost":
        from jobs.templatetags.narx import som
        return _("{0} сўм").format(som(h["amount"]))
    if k == "short":
        return _("пул етмайди")
    return ""

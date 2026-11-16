"""Фойдаланувчи йўли (2-босқич): смета юклашдан натижагача ким қайси қадамга етди, қаерда тўхтади, қайтиб келдими.

Ўзимизнинг журнал (``panel.Track``) — ташқи хизматсиз. Ҳар сўров эмас, фақат маъноли қадамлар:
* сервер томони — URL номи ва усулга қараб (``TrackMiddleware``, кўринишларга деярли тегмайди);
* саҳифадаги босишлар (масалан белгилаш қадамлари) — ``beacon`` орқали, фақат рўйхатдаги қадамлар.
Кузатув хатоси асосий ишни ҳеч қачон бузмайди."""
import logging
from datetime import timedelta

from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Track

log_ = logging.getLogger(__name__)

# калит → (номи, изоҳ). Тартиб — панелдаги рўйхат тартиби
STEPS = {
    "upload_page": ("Юклаш саҳифаси", "Смета юклаш саҳифасини очди"),
    "uploaded": ("Файл юклади", "Файл(лар) қабул қилинди — объект ёки папка яратилди"),
    "upload_error": ("Юклашда хато", "Файл қабул қилинмади (формат, ҳажм, чегара)"),
    "review": ("Текширишга ўтди", "Листлар ва жуфтликлар саҳифаси очилди"),
    "pick": ("Тур / жуфтлик танлади", "Лист турини ёки локалнинг ресурсини ўзгартирди"),
    "replan": ("Устунларни тузатди", "«Қайта жуфтлаш» — қўлда устун ёки тур"),
    "mark_open": ("Белгилашни очди", "Листни белгилаш саҳифаси"),
    "mark_role": ("Белгилаш: тури", "Лист турини танлади"),
    "mark_start": ("Белгилаш: биринчи қатор", "Биринчи қаторни босди"),
    "mark_col": ("Белгилаш: устун", "Устунни босди ёки «йўқ» деди"),
    "mark_end": ("Белгилаш: охирги қатор", "Охирги қаторни босди ёки «Охиригача»"),
    "mark_save": ("Белгилашни сақлади", "Тизим эслаб қолди"),
    "memo_forget": ("Эслатмани унутди", "Аввалги созлама нотўғри деб топилди"),
    "price_start": ("Нархлашни бошлади", "«Бошлаш» / «Ҳисоблаш» босилди"),
    "result": ("Натижани кўрди", "Нархланган смета саҳифаси"),
    "download": ("Юклаб олди", "Натижа Excel файли юклаб олинди"),
    "help": ("Ёрдамни очди", "Ёрдам саҳифаси"),
    "settings_open": ("Созламаларни очди", "«Созламалар» саҳифаси"),
    "settings_save": ("Созламани ўзгартирди", "Изоҳда — нима ўзгарди: nf_mode, source, tips, dense, guide"),
}
# асосий йўл: шу тартибда ўтади
FUNNEL = ["upload_page", "uploaded", "review", "price_start", "result", "download"]
MARK_FUNNEL = ["mark_open", "mark_role", "mark_start", "mark_col", "mark_save"]
CLIENT_STEPS = {"mark_role", "mark_start", "mark_col", "mark_end"}  # саҳифадан келадиганлар

# (URL номи, усул) → қадам
ROUTES = {
    ("index", "GET"): "upload_page", ("index", "POST"): "uploaded",
    ("project", "GET"): "review", ("batch", "GET"): "review",
    ("batch_pick", "POST"): "pick", ("batch_replan", "POST"): "replan",
    ("batch_mark", "GET"): "mark_open", ("batch_mark", "POST"): "mark_save",
    ("batch_forget", "POST"): "memo_forget",
    ("batch_confirm", "POST"): "price_start", ("project_price", "POST"): "price_start",
    ("job", "GET"): "result",
    ("job_download", "GET"): "download", ("batch_download", "GET"): "download", ("project_download", "GET"): "download",
    ("help", "GET"): "help",
    ("user_settings", "GET"): "settings_open", ("user_settings", "POST"): "settings_save",
}
WORK_KWARGS = ("batch_id", "project_id", "job_id")
DEDUP_SECONDS = 120  # саҳифани янгилаш ёки тез-тез сўров — бир қадам бир марта
BEACON_LIMIT = 120  # бир фойдаланувчидан дақиқасига


def record(user, step, work_id=None, detail=""):
    if step not in STEPS or user is None or not user.is_authenticated:
        return
    import hashlib
    key = f"track:{user.pk}:{step}:{work_id}:{hashlib.md5((detail or '').encode()).hexdigest()[:10]}"
    try:
        if not cache.add(key, 1, DEDUP_SECONDS):
            return
        Track.objects.create(user=user, step=step, work_id=work_id, detail=(detail or "")[:200])
    except Exception:  # noqa: BLE001
        log_.exception("track yozilmadi: %s", step)


class TrackMiddleware:
    """Сўров тугагач: URL номи бўйича қадам. Кўриниш ``request.track_work`` / ``request.track_detail`` қўйиши мумкин
    (масалан юклашда — яратилган объект, хатода — сабаби)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._track(request, response)
        except Exception:  # noqa: BLE001 — кузатув саҳифани бузмасин
            log_.exception("track middleware")
        return response

    @staticmethod
    def _track(request, response):
        user = getattr(request, "user", None)
        match = getattr(request, "resolver_match", None)
        if user is None or not user.is_authenticated or match is None or user.is_staff and match.namespace == "panel":
            return
        step = ROUTES.get((match.url_name, request.method))
        if not step:
            return
        status = response.status_code
        if step == "uploaded":
            if getattr(request, "track_work", None):
                pass
            elif request.FILES and (status >= 400 or getattr(request, "track_detail", "")):
                step = "upload_error"
            else:
                return
        elif status >= 400 or (request.method == "GET" and status != 200):
            return  # 404, рухсат йўқ ёки бошқа саҳифага йўналтирилди (масалан бош саҳифа → иш столи)
        work = getattr(request, "track_work", None) or next(
            (match.kwargs[k] for k in WORK_KWARGS if k in match.kwargs), None)
        detail = getattr(request, "track_detail", "") or (request.GET.get("k", "") if step == "mark_open" else "")
        record(user, step, work, detail)


@require_POST
def beacon(request):
    """Саҳифадан келган қадам (``navigator.sendBeacon``): ``s`` — қадам, ``w`` — иш, ``d`` — тафсилот."""
    step = request.POST.get("s", "")
    if step in CLIENT_STEPS and cache.get_or_set(f"track:b:{request.user.pk}", 0, 60) < BEACON_LIMIT:
        try:
            cache.incr(f"track:b:{request.user.pk}")
        except ValueError:
            pass
        import uuid
        try:
            work = uuid.UUID(request.POST.get("w", ""))
        except ValueError:
            work = None
        record(request.user, step, work, request.POST.get("d", "")[:200])
    return HttpResponse(status=204)


# ===================== панел ҳисоботи =====================
def report(days: int, staff: bool = False) -> dict:
    """«Фойдаланувчи йўли»: воронка (нечта одам қайси қадамга етди), қаерда тўхтаганлар, қайтиб келганлар."""
    since = timezone.now() - timedelta(days=days)
    qs = Track.objects.filter(created__gte=since)
    if not staff:
        qs = qs.exclude(user__is_staff=True)
    rows = list(qs.values_list("user_id", "step", "work_id", "created", "detail"))

    by_step_users: dict = {}
    by_step_count: dict = {}
    for uid, step, _w, _c, _d in rows:
        by_step_users.setdefault(step, set()).add(uid)
        by_step_count[step] = by_step_count.get(step, 0) + 1

    def funnel(steps):
        out, first, prev = [], None, None
        for s in steps:
            n = len(by_step_users.get(s, ()))
            first = n if first is None else first
            out.append({"key": s, "label": STEPS[s][0], "hint": STEPS[s][1], "users": n,
                        "events": by_step_count.get(s, 0),
                        "pct": round(100 * n / first) if first else 0,
                        "drop": (prev - n) if prev is not None and prev > n else 0,
                        "drop_pct": round(100 * (prev - n) / prev) if prev and prev > n else 0})
            prev = n
        return out

    # қаерда тўхтади: ҳар бир одамнинг асосий йўлдаги энг узоқ қадами
    reach: dict = {}
    last_seen: dict = {}
    for uid, step, _w, created, _d in rows:
        last_seen[uid] = max(last_seen.get(uid, created), created)
        if step in FUNNEL:
            reach[uid] = max(reach.get(uid, -1), FUNNEL.index(step))
    stopped = []
    for i, s in enumerate(FUNNEL[:-1]):
        users = [u for u, r in reach.items() if r == i]
        stopped.append({"key": s, "label": STEPS[s][0], "next": STEPS[FUNNEL[i + 1]][0], "n": len(users),
                        "users": sorted(users, key=lambda u: last_seen[u], reverse=True)[:12]})

    # қайтиб келди: битта ишни (объект/папка) бошқа кунда яна очди — давом эттириш учун қайтган
    days_by_work: dict = {}
    for uid, step, work, created, _d in rows:
        if work and step in ("review", "result", "mark_open"):
            days_by_work.setdefault((uid, work), set()).add(timezone.localdate(created))
    returned_works = [k for k, d in days_by_work.items() if len(d) > 1]
    active_days: dict = {}
    for uid, _s, _w, created, _d in rows:
        active_days.setdefault(uid, set()).add(timezone.localdate(created))

    errors = [r for r in rows if r[1] == "upload_error"]
    errors.sort(key=lambda r: r[3], reverse=True)
    return {
        "funnel": funnel(FUNNEL), "mark": funnel(MARK_FUNNEL), "stopped": stopped,
        "people": len({r[0] for r in rows}), "events": len(rows),
        "returned_users": len({u for u, _w in returned_works}), "returned_works": len(returned_works),
        "multi_day": sum(1 for d in active_days.values() if len(d) > 1),
        "errors": errors[:20],
        "other": [{"key": s, "label": STEPS[s][0], "hint": STEPS[s][1], "users": len(by_step_users.get(s, ())),
                   "events": by_step_count.get(s, 0)}
                  for s in ("pick", "replan", "mark_end", "memo_forget", "upload_error", "help")],
    }

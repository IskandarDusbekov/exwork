"""Очиқ саҳифалар: «Биз ҳақимизда», «Фойдаланиш шартлари» (матни панелда — ``SiteSetting`` "about" / "terms")
ва мақола / видео кўрилишлари сони (``panel.models.ViewCount``)."""
import hashlib

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from .richtext import sanitize
from web.i18n import ui
from django.utils.translation import gettext_noop as _noop

# ===================== Биз ҳақимизда =====================
ABOUT_DEFAULT = {
    "lead": _noop("mhisobot — смета­чилар ва техник назоратчилар учун иш қуроли: смета нархлаш, офёрта ва далолатномаларни "
            "бир жойда, тез ва хатосиз тайёрлаш."),
    "body": (
        _noop("<h2>Лойиҳа ҳақида</h2>"
        "<p>«Муҳандис ҳисобот» (mhisobot) қурилиш соҳасида ҳар куни такрорланадиган ҳисоб ишларини автоматлаштириш "
        "учун яратилган. Смета файлини юкласангиз — тизим ресурсларни танийди, нарх базасидан мос нархни топади ва "
        "натижани Excel кўринишида қайтаради.</p>"
        "<h2>Нима учун</h2>"
        "<ul><li>Смета нархлашга кетадиган вақтни соатлардан дақиқаларга қисқартириш.</li>"
        "<li>Ҳисобдаги қўл хатоларини камайтириш.</li>"
        "<li>Техник назорат ҳужжатларини (офёрта, далолатнома) бир хил тартибда юритиш.</li></ul>"
        "<h2>Маълумотлар хавфсизлиги</h2>"
        "<p>Ҳар бир фойдаланувчининг файллари фақат ўзига кўринади. Юкланган сметалар бошқаларга берилмайди.</p>")
    ),
    "facts": [],  # [[рақам, изоҳ]] — масалан ["1 200+", "нархланган смета"]
    "team": [],  # [{"name", "role", "bio", "photo"}]
    "contacts": {"phone": "", "email": "", "telegram": "", "address": ""},
}
MAX_TEAM = 24


def about() -> dict:
    from panel import conf
    v = conf.raw("about", {}) or {}
    d = {k: v.get(k, ABOUT_DEFAULT[k]) for k in ABOUT_DEFAULT}
    d["lead"], d["body"] = ui(d["lead"]), sanitize(ui(d["body"]))  # стандарт матн — жорий тилда
    d["team"] = [m for m in d["team"] if isinstance(m, dict) and m.get("name")][:MAX_TEAM]
    d["facts"] = [f for f in d["facts"] if isinstance(f, (list, tuple)) and len(f) == 2 and (f[0] or f[1])][:6]
    c = d["contacts"] if isinstance(d["contacts"], dict) else {}
    d["contacts"] = {k: str(c.get(k, "")) for k in ABOUT_DEFAULT["contacts"]}
    tg = d["contacts"]["telegram"].strip().lstrip("@")
    d["tg_url"] = tg if tg.startswith("https://") else (f"https://t.me/{tg}" if tg else "")
    return d


# ===================== Фойдаланиш шартлари =====================
TERMS_MODES = (("checkbox", "Белгилаш шарт: «Шартларга розиман» катакчаси"),  # i18n: ok
               ("note", "Огоҳлантириш: «Рўйхатдан ўтиш билан шартларга рози бўласиз»"),  # i18n: ok
               ("off", "Кўрсатилмайди"))  # i18n: ok
TERMS_DEFAULT = (
    _noop("<h2>1. Умумий қоидалар</h2>"
    "<p>Ушбу шартлар mhisobot.uz сайти ва унинг хизматларидан (смета нархлаш, техник назорат ҳужжатлари, калькулятор "
    "ва бошқалар) фойдаланиш тартибини белгилайди. Рўйхатдан ўтиш ёки сайтдан фойдаланиш шу шартларга рози "
    "бўлганингизни билдиради.</p>"
    "<h2>2. Ҳисоб</h2>"
    "<ul><li>Рўйхатдан ўтишда тўғри маълумот (исм, email) киритилади.</li>"
    "<li>Парол ва кириш маълумотларини бошқаларга бермаслик фойдаланувчи зиммасида.</li>"
    "<li>Қоидабузарлик аниқланса, ҳисоб вақтинча ёки бутунлай ёпилиши мумкин.</li></ul>"
    "<h2>3. Юкланган файллар ва махфийлик</h2>"
    "<p>Юкланган сметалар ва ҳужжатлар фақат эгасига кўринади. Улар учинчи шахсларга берилмайди. Хизмат сифатини "
    "ошириш учун номсиз (шахсга боғланмаган) статистика ишлатилиши мумкин.</p>"
    "<h2>4. Натижалар учун жавобгарлик</h2>"
    "<p>Тизим берган нархлар ва ҳисоблар маълумот учун: якуний ҳужжатни смета­чи ўзи текширади ва унга ўзи жавоб "
    "беради. Нарх манбаларидаги хато ёки эскирган маълумот учун сайт жавобгар эмас.</p>"
    "<h2>5. Шартлар ўзгариши</h2>"
    "<p>Шартлар янгиланиши мумкин; янги таҳрири шу саҳифада эълон қилинади.</p>"
    "<h2>6. Алоқа</h2>"
    "<p>Саволлар бўлса — сайтдаги «Ёрдам хизмати» орқали ёзинг.</p>")
)


def terms() -> dict:
    """{"body", "mode", "version", "updated"} — панелда ёзилмаган бўлса стандарт матн."""
    from panel import conf
    v = conf.raw("terms", {}) or {}
    mode = v.get("mode") if v.get("mode") in dict(TERMS_MODES) else "checkbox"
    return {"body": sanitize(ui(v.get("body") or TERMS_DEFAULT)), "mode": mode, "version": int(v.get("version") or 1),
            "updated": v.get("updated") or ""}


def promo() -> dict:
    """Рўйхатдан ўтиш саҳифасидаги кичик эълон учун: видео ва мақолалар сони."""
    from panel.models import Post
    from .site import video_sections
    return {"videos": sum(len(s["videos"]) for s in video_sections()), "posts": Post.objects.filter(published=True).count()}


# ===================== кўрилишлар сони =====================
BOTS = ("bot", "crawl", "spider", "slurp", "facebookexternalhit", "preview", "lighthouse", "headless", "curl", "wget",
        "python-requests", "httpx")
SEEN_FOR = 6 * 3600


def _is_bot(request):
    ua = request.META.get("HTTP_USER_AGENT", "").lower()
    return not ua or any(b in ua for b in BOTS)


def hit(request, kind: str, key: str) -> bool:
    """Кўрилишни ҳисоблайди (бир одам 6 соатда бир марта). Админлар ва ботлар ҳисобланмайди."""
    from panel.models import ViewCount
    u = getattr(request, "user", None)
    if request.method != "GET" or _is_bot(request) or (u and u.is_authenticated and u.is_staff):
        return False
    who = (request.META.get("REMOTE_ADDR", "") + "|" + request.META.get("HTTP_USER_AGENT", "")[:200]).encode()
    mark = "vw:" + hashlib.sha1(who + f"|{kind}|{key}".encode()).hexdigest()
    if not cache.add(mark, 1, SEEN_FOR):
        return False
    if not ViewCount.objects.filter(kind=kind, key=key).update(n=F("n") + 1, updated=timezone.now()):
        obj, made = ViewCount.objects.get_or_create(kind=kind, key=key, defaults={"n": 1})
        if not made:
            ViewCount.objects.filter(pk=obj.pk).update(n=F("n") + 1)
    return True


def counts(kind: str, keys) -> dict:
    """{key: n} — йўқлари 0."""
    from panel.models import ViewCount
    keys = [str(k) for k in keys]
    got = dict(ViewCount.objects.filter(kind=kind, key__in=keys).values_list("key", "n"))
    return {k: got.get(k, 0) for k in keys}


def count(kind: str, key) -> int:
    return counts(kind, [key])[str(key)]

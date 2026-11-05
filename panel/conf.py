"""Панелдан ўзгартириладиган созламалар. Қиймат базада (``SiteSetting``) бўлса — шу, бўлмаса — ``settings`` / .env даги
стандарт. Кодда ``conf.get("uploads_per_hour")`` — сервер қайта ишга туширилмасдан ўзгаради (кеш 30 сония).

Хавфсизликка тегишли нарсалар (икки босқичли кириш, DEBUG, калитлар) бу ерда ЙЎҚ — улар фақат серверда, .env да:
панелга кирган одам (ёки ўғирланган сессия) уларни ўчира олмасин."""
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from django.conf import settings
from django.core.cache import cache

CACHE_KEY = "panel:conf"
MB = 1024 * 1024


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    kind: str  # int | bool | choice
    default: str  # settings атрибути
    help: str = ""
    unit: str = ""
    scale: int = 1  # settings'да байтда, панелда МБ: scale=MB
    lo: int = 0
    hi: int = 10 ** 9
    choices: Tuple[Tuple[str, str], ...] = ()


FIELDS = (
    Field("uploads_per_hour", "Бир соатдаги юклашлар", "Чекловлар", "int", "NARXLASH_UPLOADS_PER_HOUR",
          "Битта фойдаланувчи бир соатда нечта папка / смета / техназор объекти юклай олади (офёрта тузиш ҳам). 0 — чексиз.",
          "та", hi=10000),
    Field("user_quota_mb", "Ҳар фойдаланувчига диск", "Чекловлар", "int", "NARXLASH_USER_QUOTA_MB",
          "Нархлаш ва техназор файллари жами. Алоҳида фойдаланувчига бошқача — унинг саҳифасида. 0 — чексиз.", "МБ", hi=10 ** 7),
    Field("max_upload_mb", "Битта файл", "Чекловлар", "int", "NARXLASH_MAX_UPLOAD",
          "Смета ёки ресурс файли (папка ичидагиси ҳам).", "МБ", scale=MB, lo=1, hi=700),
    Field("max_archive_mb", "ZIP / RAR архив", "Чекловлар", "int", "NARXLASH_MAX_ARCHIVE",
          "nginx 800 МБ дан каттасини ўтказмайди.", "МБ", scale=MB, lo=1, hi=790),
    Field("texnazor_max_upload_mb", "Техназор офёрта файли", "Техназор", "int", "TEXNAZOR_MAX_UPLOAD",
          "", "МБ", scale=MB, lo=1, hi=700),
    Field("my_ofyorta", "«Менинг офёрталарим» кўринсин", "Техназор", "bool", "TEXNAZOR_MY_OFYORTA",
          "Нархланган сметалардан офёрта тузиш тугмалари (ҳозирча синовда)."),
    Field("auto_min_score", "Таклифни ўзи олиш: энг кам ўхшашлик", "Нархлаш", "int", "NARXLASH_AUTO_MIN_SCORE",
          "«Автоматик» усулда RES да аниқ топилмаган ресурсга энг яқин ресурс нархи фақат шунча ўхшаш бўлсагина "
          "ўзи қўйилади; ундан пасти «ТОПИЛМАДИ» бўлиб смета­чига қолади. Мисол: «МУСОР СТРОИТЕЛЬНЫЙ» ↔ «ГВОЗДИ "
          "СТРОИТЕЛЬНЫЕ» — 70%, олинмайди. Пасайтирсангиз — кўпроқ ўзи нархланади, лекин хато нарх хавфи ортади.",
          "%", lo=50, hi=100),
    Field("auto_same_word", "Таклифни ўзи олиш: биринчи сўз бир хил бўлсин", "Нархлаш", "bool", "NARXLASH_AUTO_SAME_WORD",
          "Ёқилган бўлса — номлар бир хил сўздан бошланиши керак («ЦЕМЕНТ М400» ↔ «ЦЕМЕНТ М500» — бўлади, "
          "«МУСОР …» ↔ «ГВОЗДИ …» — йўқ). Ўчириш тавсия этилмайди."),
    Field("auto_same_unit", "Таклифни ўзи олиш: бирлиги бир хил бўлсин", "Нархлаш", "bool", "NARXLASH_AUTO_SAME_UNIT",
          "Ёқилган бўлса — «Т» ли ресурсга «КГ» ли ресурс нархи ўзи қўйилмайди."),
    Field("fuzzy_default", "«Ўхшаш» деб нархлаш чегараси", "Нархлаш", "int", "NARXLASH_FUZZY_DEFAULT",
          "Янги сметалар учун: ном шунча ўхшаш бўлса, RES нархи «ўхшаш (fuzzy)» деб ёзилади. Смета­чи ҳар сметада "
          "80–100 оралиғида ўзгартира олади. Аввал нархланганларга таъсир қилмайди.", "%", lo=80, hi=100),
    Field("base_price_ratio", "Шубҳали нарх: медианадан фарқ", "Ресурс базаси", "int", "NARXLASH_BASE_PRICE_RATIO",
          "Базага тушган нарх ўша ресурс медианасидан шунча баравар катта ёки кичик бўлса — «Текширув» навбатига "
          "тушади (базага ўзи кирмайди). Мисол: 3 — медиана 1 000 бўлса, 3 000 дан катта ёки 333 дан кичиги.",
          "марта", lo=2, hi=50),
    Field("signup", "Рўйхатдан ўтиш", "Фойдаланувчилар", "choice", "NARXLASH_SIGNUP",
          "Ёпиқ бўлса — ҳисобни фақат админ очади.", choices=(("open", "Очиқ — ҳар ким"), ("closed", "Ёпиқ"))),
)
BY_KEY = {f.key: f for f in FIELDS}
# бўлим остидаги изоҳ (панел саҳифасида)
GROUP_HELP = {
    "Нархлаш": "Нархлаш қоидалари — ҳамма фойдаланувчига. Ўзгариш кейинги нархлашдан бошлаб ишлайди; аввал "
               "нархланган смета ўзгармайди (керак бўлса — смета саҳифасида «Қайта нархлаш»).",
    "Ресурс базаси": "Миллий ресурс базасига нарх қўшилганда текшириш.",
}


def _stored() -> dict:
    data = cache.get(CACHE_KEY)
    if data is None:
        from .models import SiteSetting
        data = dict(SiteSetting.objects.values_list("key", "value"))
        cache.set(CACHE_KEY, data, 30)
    return data


def default(key: str) -> Any:
    f = BY_KEY[key]
    v = getattr(settings, f.default)
    return v // f.scale if f.kind == "int" else v


def get(key: str) -> Any:
    """Панелда ёзилган қиймат ёки стандарт. ``*_mb`` ва ``scale`` ли майдонлар — байтда (кодга қулай)."""
    f = BY_KEY[key]
    v = _stored().get(key)
    if v is None:
        return getattr(settings, f.default)
    return int(v) * f.scale if f.kind == "int" else v


def shown(key: str) -> Any:
    """Панелда кўрсатиладиган қиймат (МБ да)."""
    f = BY_KEY[key]
    v = get(key)
    return v // f.scale if f.kind == "int" else v


def parse(f: Field, raw) -> Any:
    """Формадан: нотўғри бўлса — ValueError (матни саҳифада)."""
    if f.kind == "bool":
        return raw in ("1", "on", "true", True)
    if f.kind == "choice":
        if raw not in dict(f.choices):
            raise ValueError(f"{f.label}: нотўғри танлов")
        return raw
    try:
        v = int(str(raw).replace(" ", "").strip())
    except ValueError:
        raise ValueError(f"{f.label}: бутун сон бўлиши керак")
    if not f.lo <= v <= f.hi:
        raise ValueError(f"{f.label}: {f.lo} дан {f.hi} гача")
    return v


def save(changes: dict, user) -> list:
    """{key: қиймат} — фақат ўзгарганлари ёзилади; стандартга тенг бўлса — базадан ўчади (кейин .env ҳал қилади).
    Қайтади: [(label, эски, янги)] — журнал учун."""
    from .models import SiteSetting
    done = []
    for key, value in changes.items():
        f = BY_KEY[key]
        old = shown(key)
        if value == old:
            continue
        if value == default(key):
            SiteSetting.objects.filter(key=key).delete()
        else:
            SiteSetting.objects.update_or_create(key=key, defaults={"value": value, "updated_by": user})
        done.append((f.label, old, value))
    cache.delete(CACHE_KEY)
    return done


def user_quota(user) -> Optional[int]:
    """Фойдаланувчининг диск чегараси (байт); 0 / None — чексиз."""
    mb = user.quota_mb if getattr(user, "quota_mb", None) is not None else get("user_quota_mb")
    return mb * MB if mb else None

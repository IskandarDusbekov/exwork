"""Панелдан ўзгартириладиган созламалар. Қиймат базада (``SiteSetting``) бўлса — шу, бўлмаса — ``settings`` / .env даги
стандарт. Кодда ``conf.get("uploads_per_hour")`` — сервер қайта ишга туширилмасдан ўзгаради (кеш 30 сония).

Хавфсизликка тегишли нарсалар (икки босқичли кириш, DEBUG, калитлар) бу ерда ЙЎҚ — улар фақат серверда, .env да:
панелга кирган одам (ёки ўғирланган сессия) уларни ўчира олмасин."""
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from django.conf import settings
from django.core.cache import cache

CACHE_KEY = "panel:conf"
BILLING_GROUP = "Тўлов"  # бу гуруҳ «Созламалар»да эмас — «Тўлов ва тарифлар» саҳифасида
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
    Field("base_loose", "Базадан қидириш: тиниш белгиларисиз", "Ресурс базаси", "bool", "NARXLASH_BASE_LOOSE",
          "Сметадаги ном базадагидан фақат тиниш белгилари ва бўшлиқ билан фарқ қилса — ўша ресурс деб нархланади: "
          "«КОЛЕНО/ОТВОД 90°/ PP-R ДН-25» = «КОЛЕНО(ОТВОД 90°) PP-R ДН-25». Ҳарф ва рақамлар айнан бир хил бўлиши, "
          "бирлиги бир хил бўлиши ва базада шундай ном битта бўлиши шарт (иккита бўлса — смета­чи танлайди)."),
    Field("base_near_min", "Базадан қидириш: ўхшаш ном (таклиф)", "Ресурс базаси", "int", "NARXLASH_BASE_NEAR_MIN",
          "Базада топилмаган ресурсга шунча ўхшаш ном таклиф қилинади (нарх ўзи ёзилмайди — «Топилмаганлар»да "
          "«яқин ресурс»; «Автоматик» усулда «Таклифни ўзи олиш» қоидалари билан олинади). Бирлиги ва номидаги "
          "барча рақамлар бир хил бўлиши шарт: «ДН-25» ↔ «ДН-32» 95% ўхшаш, лекин таклиф қилинмайди. 0 — ўчиқ.",
          "%", lo=0, hi=100),
    Field("max_files", "Битта юклашда Excel файллар", "Чекловлар", "int", "NARXLASH_MAX_FILES",
          "«Файл танлаш» билан бир марта нечта Excel юкланади (папка ва архив — алоҳида).", "та", lo=1, hi=200),
    Field("max_project_files", "Битта папка / архивда файллар", "Чекловлар", "int", "NARXLASH_MAX_PROJECT_FILES",
          "Бутун объект юкланганда ичидаги файллар сони.", "та", lo=10, hi=5000),
    Field("lm_owners_min", "L / M базаси: нечта смета­чи деса", "Ресурс базаси", "int", "NARXLASH_LM_OWNERS_MIN",
          "RES файлида далил бўлмаса — ресурс кабель (L) ёки ускуна (M) деб базага шунча ҳар хил смета­чи "
          "бир хил белгилагандан кейин киради. Битта одамнинг 20 сметаси — битта овоз.", "киши", lo=1, hi=20),
    Field("lm_strong_votes", "L / M базаси: ишончли деб ҳисоблаш", "Ресурс базаси", "int", "NARXLASH_LM_STRONG_VOTES",
          "Шунча (ва ундан кўп) бир хил овоз бўлса ва қарама-қаршиси бўлмаса — тур ишончли деб ўзи қўйилади.",
          "овоз", lo=1, hi=50),
    Field("lm_personal", "L / M базаси: смета­чининг ўз танлови", "Ресурс базаси", "bool", "NARXLASH_LM_PERSONAL",
          "Смета­чи бир марта ўзи L ёки M га кўчирган (ёки K га қайтарган) ресурс кейинги сметаларида унинг ўзига "
          "ишончли таклиф бўлади («Автоматик» усулда ўзи қўйилади). Фақат шу смета­чига: умумий базадаги тур ва "
          "бошқа смета­чилар ўзгармайди; админ ўргатгани устун; фақат айнан шу ном ёки код бўйича."),
    Field("price_stale_days", "Нарх базаси: эскирган нарх", "Ресурс базаси", "int", "NARXLASH_PRICE_STALE_DAYS",
          "Ресурсга шунча кундан бери янги нарх келмаган бўлса — «Эскирган» деб кўрсатилади.", "кун", lo=30, hi=3650),
    Field("billing_on", "Пулли режим", "Тўлов", "bool", "BILLING_ON",
          "Ўчиқ — ҳамма нарса бепул (ҳозиргидек). Ёқилса — нархланган смета, офёрта ва техназор файлларини юклаб "
          "олишда аввал тариф ҳисобидан, етмаса балансдан ечилади; балансда ҳам етмаса юклаб олиш ёпилади. "
          "Нархлаш, натижани кўриш ва тузатиш доим бепул. Ходимлар тўламайди. Тарифлар — «Тўлов ва тарифлар» саҳифасида."),
    Field("billing_row_price", "Тарифдан ошган ҳар қатор", "Тўлов", "int", "BILLING_ROW_PRICE",
          "Ойлик қатор тугаганда ҳар нархланган қатор учун балансдан. Мисол: 4 — 611 қаторли смета 2 444 сўм.",
          "сўм", lo=0, hi=100000),
    Field("billing_object_price", "Тарифдан ошган техназор объекти", "Тўлов", "int", "BILLING_OBJECT_PRICE",
          "Тарифдаги объектлар сонидан ошса — ҳар объект учун ойига бир марта (шу ойда биринчи юклаб олишда).",
          "сўм", lo=0, hi=10 ** 8),
    Field("billing_topup_min", "Балансни тўлдириш: энг кам", "Тўлов", "int", "BILLING_TOPUP_MIN",
          "Бир тўловда шундан кам сумма қабул қилинмайди (сервер текширади).", "сўм", lo=1000, hi=10 ** 7),
    Field("billing_topup_max", "Балансни тўлдириш: энг кўп", "Тўлов", "int", "BILLING_TOPUP_MAX",
          "Бир тўловда шундан катта сумма қабул қилинмайди. Энг камидан катта бўлсин.", "сўм", lo=1000, hi=10 ** 8),
    Field("billing_disc_3", "3 ойлик обуна чегирмаси", "Тўлов", "int", "BILLING_DISC_3",
          "Тариф 3 ойга олинса — нархдан шунча фоиз арзон. 0 — чегирмасиз.", "%", lo=0, hi=90),
    Field("billing_disc_6", "6 ойлик обуна чегирмаси", "Тўлов", "int", "BILLING_DISC_6", "", "%", lo=0, hi=90),
    Field("billing_disc_12", "1 йиллик обуна чегирмаси", "Тўлов", "int", "BILLING_DISC_12",
          "Тарифнинг ўз чегирмаси (акция) бўлса — иккаласи кетма-кет қўлланади: 20% ва 10% → 28%.", "%", lo=0, hi=90),
    Field("brand", "mhisobot.uz белгиси", "Тайёр файллар", "bool", "NARXLASH_BRAND",
          "Ёқилган бўлса — юклаб олинадиган файл номига « (mhisobot.uz)» қўшилади ва нархланган сметанинг LOG "
          "листида (жадвалнинг ўнг томонида) «mhisobot.uz тизимида тайёрланди» ҳаволаси туради. Смета, Ф-2, Ф-3 "
          "жадвалларининг ўзига ҳеч нарса ёзилмайди. Файл хусусиятларидаги белги (Excel: Файл → Сведения → "
          "Организация) калитга қарамай доим ёзилади. LOG'даги ёзув кейинги нархлашдан бошлаб ўзгаради."),
    Field("signup", "Рўйхатдан ўтиш", "Фойдаланувчилар", "choice", "NARXLASH_SIGNUP",
          "Ёпиқ бўлса — ҳисобни фақат админ очади.", choices=(("open", "Очиқ — ҳар ким"), ("closed", "Ёпиқ"))),
)
BY_KEY = {f.key: f for f in FIELDS}
# бўлим остидаги изоҳ (панел саҳифасида)
GROUP_HELP = {
    "Нархлаш": "Нархлаш қоидалари — ҳамма фойдаланувчига. Ўзгариш кейинги нархлашдан бошлаб ишлайди; аввал "
               "нархланган смета ўзгармайди (керак бўлса — смета саҳифасида «Қайта нархлаш»).",
    "Ресурс базаси": "Миллий ресурс базасига нарх қўшилганда текшириш ва RES'сиз сметани базадан нархлашда қидириш қоидалари.",
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


def raw(key: str, default=None):
    """``FIELDS`` да йўқ, панелда сақланадиган маълумот (ёрдам матнлари, видео рўйхати) — JSON."""
    v = _stored().get(key)
    return default if v is None else v


def put(key: str, value, user):
    """``raw`` учун ёзиш; бўш қиймат — базадан ўчади (кодда ёзилгани ишлайди)."""
    from .models import SiteSetting
    if value in (None, {}, []):
        SiteSetting.objects.filter(key=key).delete()
    else:
        SiteSetting.objects.update_or_create(key=key, defaults={"value": value, "updated_by": user})
    cache.delete(CACHE_KEY)


def user_quota(user) -> Optional[int]:
    """Фойдаланувчининг диск чегараси (байт); 0 / None — чексиз."""
    mb = user.quota_mb if getattr(user, "quota_mb", None) is not None else get("user_quota_mb")
    return mb * MB if mb else None

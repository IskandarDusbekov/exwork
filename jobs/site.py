"""Очиқ сайт мазмуни: бош саҳифа (landing) матнлари ва блог (видео дарслар бўлимлари + мақолалар).

Бош саҳифа матнлари кодда (``BLOCKS``) стандарт сифатида туради; панел «Сайт: бош саҳифа ва блог» бўлимида
ўзгартирилгани ``SiteSetting`` "landing" да сақланади ({блок: {майдон: матн, "rows": [[…], …]}} — фақат
ўзгарганлари). Бўш майдон — коддаги матн. Видео дарслар — ``jobs.guide.videos`` (панел «Ёрдам матнлари ва видео»).
"""
import re
from dataclasses import dataclass, field
from typing import List, Tuple

from web.i18n import ui
from django.utils.translation import gettext as _, gettext_noop as _noop

MAX_ROWS = 12
MAX_LEN = 600


@dataclass(frozen=True)
class Block:
    key: str
    title: str
    hint: str = ""
    fields: Tuple[Tuple[str, str, str], ...] = ()  # (калит, номи, стандарт матн)
    cols: Tuple[str, ...] = ()  # такрорланадиган қаторлар устунлари (номи)
    rows: Tuple[Tuple[str, ...], ...] = ()  # стандарт қаторлар
    icons: Tuple[str, ...] = field(default=())  # қаторлар белгиси (тартиб бўйича; панелда ўзгармайди)


BLOCKS: Tuple[Block, ...] = (
    Block("hero", _noop("Бош қисм"), _noop("Саҳифанинг энг тепаси: катта сарлавҳа ва қисқа тушунтириш."), fields=(
        ("title_a", _noop("Сарлавҳа: боши"), _noop("Локал сметани")),
        ("title_em", _noop("Сарлавҳа: рангли қисми"), _noop("бир неча сонияда")),
        ("title_b", _noop("Сарлавҳа: охири"), _noop("нархланг")),
        ("lead", _noop("Тушунтириш"), _noop("Ҳар бир ресурс нарх файли (RES) ёки ресурс базаси билан номи ва ўлчов бирлиги бўйича "
                               "солиштирилади, нарх ёзилади, йиғма смета ҳисобланади. Аниқ мос келмаган қаторга тахминий "
                               "нарх ёзилмайди — у сиз кўриб чиқишингиз учун белгиланади.")),
        ("note", _noop("Пастки изоҳ"), _noop("Excel: .xls, .xlsx, .xlsm · ZIP ва RAR · асл файл ўзгармайди")),
    )),
    Block("proof", _noop("Ўлчанган натижа"), _noop("Смета расми остидаги рақамлар: ҳақиқий объектда ўлчанган."), fields=(
        ("label", _noop("Изоҳ"), _noop("Битта ҳақиқий объект сметаси: 198 та иш, 1 520 та ресурс")),
    ), cols=(_noop("Рақам"), _noop("Изоҳ")), rows=(
        (_noop("13–14 с"), _noop("солиштириш, нарх ёзиш ва йиғма смета")),
        ("1 517", _noop("ресурс аниқ топилди (1 520 тадан)")),
        ("3", _noop("қатор қўлда кўриш учун қолди")),
        ("0", _noop("тахминий нарх ёзилди")),
    )),
    Block("modules", _noop("Икки бўлим"), fields=(
        ("eyebrow", _noop("Устки ёзув"), _noop("Икки бўлим")),
        ("title", _noop("Сарлавҳа"), _noop("Сметадан бажарилган ишгача")),
        ("intro", _noop("Тушунтириш"), _noop("Сметачи сметани нархлайди, техник назорат муҳандиси эса шартнома бўйича қанча иш "
                                "бажарилганини юритади. Иккаласи бир тизимда, ҳар бир фойдаланувчининг иши фақат ўзига кўринади.")),
        ("m1_title", _noop("1-бўлим номи"), _noop("Нархлаш")),
        ("m1_text", _noop("1-бўлим матни"), _noop("Локал смета ва ресурс нархлари файлини ёки бутун объект папкасини юкланг. Тизим листларни "
                                     "мазмунидан танийди, ҳар бир локал сметага мос нарх файлини топади ва нархлайди.")),
        ("m2_title", _noop("2-бўлим номи"), _noop("Бажарилган ишлар")),
        ("m2_text", _noop("2-бўлим матни"), _noop("Шартнома офертасини бир марта юкланг. Кейин ҳар бир далолатномада фақат бажарилган иш "
                                     "ҳажмини белгилайсиз: ресурслар меъёр бўйича ўзи ҳисобланади, қолдиқ доим кўриниб туради.")),
    ), cols=(_noop("1-бўлим имконияти (рўйхат)"),), rows=(
        (_noop("Нарх фақат номи аниқ ва бирлиги бир хил бўлганда ёзилади"),),
        (_noop("Ўхшаш ёки шубҳали қаторга таклиф берилади, қарорни сиз қабул қиласиз"),),
        (_noop("Иш ҳақи, машина, материал, кабель ва ускуна алоҳида устунларда"),),
        (_noop("ZIP, RAR ёки папка: ҳамма қисмлар битта натижада"),),
    )),
    Block("how", _noop("Иш тартиби"), fields=(
        ("eyebrow", _noop("Устки ёзув"), _noop("Иш тартиби")),
        ("title", _noop("Сарлавҳа"), _noop("Тўрт қадам")),
        ("intro", _noop("Тушунтириш"), _noop("Ҳар бир қадамда тизим нимани топганини кўрсатади. Лист ёки устун нотўғри аниқланган бўлса, "
                                "давом этишдан олдин тузатасиз.")),
    ), cols=(_noop("Қадам"), _noop("Тушунтириш")), rows=(
        (_noop("Юкланг"), _noop("Локал смета ва ресурс нархлари файлини (.xls, .xlsx) ёки бутун объект папкасини юкланг.")),
        (_noop("Текширинг"), _noop("Тизим листлар ва устунларни аниқлаб кўрсатади. Бирортаси нотўғри бўлса, шу ернинг ўзида тузатасиз.")),
        (_noop("Нархланг"), _noop("Ҳар бир ресурс нарх файли билан солиштирилади, нарх ва сумма ёзилади, йиғма смета ҳисобланади.")),
        (_noop("Юклаб олинг"), _noop("Тайёр Excel файл: нархлар, рангли ҳолатлар, йиғма смета ва «LOG» листи.")),
    )),
    Block("security", _noop("Хавфсизлик"), fields=(
        ("eyebrow", _noop("Устки ёзув"), _noop("Хавфсизлик")),
        ("title", _noop("Сарлавҳа"), _noop("Смета тижорат сири бўлиб қолади")),
    ), cols=(_noop("Чора"), _noop("Тушунтириш")), rows=(
        (_noop("Файллар серверда қолади"), _noop("Сметалар фақат шу серверда ишланади. Ташқи хизматларга ҳам, сунъий интеллектга ҳам юборилмайди.")),
        (_noop("Ҳар ким ўз ишини кўради"), _noop("Ҳар бир фойдаланувчи ўз ҳисоби билан киради. Бошқа одамнинг сметаси унга кўринмайди.")),
        (_noop("Икки босқичли кириш"), _noop("Паролдан ташқари телефондаги илова коди. Ҳамма амаллар журналга ёзилади.")),
    ), icons=("database", "users", "lock")),
    Block("faq", _noop("Саволлар"), fields=(
        ("eyebrow", _noop("Устки ёзув"), _noop("Саволлар")),
        ("title", _noop("Сарлавҳа"), _noop("Кўп сўраладиган саволлар")),
    ), cols=(_noop("Савол"), _noop("Жавоб")), rows=(
        (_noop("Ресурс нархлари файли (RES) нима?"),
         _noop("Объект учун тузилган ресурслар рўйхати: ҳар бир материал, машина-соат ва иш кучининг номи, ўлчов бирлиги ва нархи. "
         "Тизим локал сметадаги ресурсларни шу рўйхат билан солиштириб, нархини олади.")),
        (_noop("Нарх топилмаса нима бўлади?"),
         _noop("Қатор қизил «ТОПИЛМАДИ» деб белгиланади ва тизим нархни тахмин қилмайди. Ёнида номи энг яқин учта ресурс ўхшашлик "
         "фоизи билан кўрсатилади: бирини танлайсиз ёки нархни ўзингиз ёзасиз.")),
        (_noop("Бирлик бошқача ёзилган бўлса-чи?"),
         _noop("Бирлик ҳам ном билан бирга солиштирилади. «м3» ва «т» каби бошқа бирликдаги ресурсга нарх ёзилмайди, у текшириш "
         "учун белгиланади.")),
        (_noop("Сметам бошқаларга кўринадими?"),
         _noop("Йўқ. Ҳар бир фойдаланувчи фақат ўзи юклаган смета ва объектларни кўради. Файллар ташқи хизматларга юборилмайди.")),
    )),
    Block("final", _noop("Якуний чақириқ"), fields=(
        ("title", _noop("Сарлавҳа"), _noop("Биринчи сметани бугун нархланг")),
        ("text", _noop("Матн"), _noop("Локал смета ва ресурс нархлари файлини ёки бутун папкани юкланг. Нархланган Excel, йиғма смета ва ҳар "
                         "бир қарор ёзилган «LOG» листини оласиз.")),
        ("contact", _noop("Алоқа сарлавҳаси"), _noop("Ташкилотингиз учун савол ёки таклиф бўлса, биз билан боғланинг")),
    )),
)
BY_KEY = {b.key: b for b in BLOCKS}

# ===================== иловадаги чап меню =====================
# Панелда яширса бўладиган бандлар (калит — jobs/_side.html даги key). «Панел» бу ерда йўқ — у фақат ходимга ва доим кўринади.
# Яширилган банд фақат менюдан йўқолади: саҳифа манзил орқали очилаверади.
SIDE_ITEMS: Tuple[Tuple[str, str, str], ...] = (
    ("dashboard", "Иш столи", "Иш"),  # i18n: ok
    ("upload", "Смета нархлаш", "Иш"),  # i18n: ok
    ("texnazor", "Офёрта ва далолатнома", "Иш"),  # i18n: ok
    ("calc", "Калькулятор", "Иш"),  # i18n: ok
    ("settings", "Созламалар", "Иш"),  # i18n: ok
    ("balance", "Баланс ва тариф", "Иш"),  # i18n: ok — пулли режим ёқиқ бўлсагина кўринади
    ("help", "Йўриқнома", "Ёрдам"),  # i18n: ok
    ("guide", "Видео қўлланма", "Ёрдам"),  # i18n: ok
    ("support", "Ёрдам хизмати", "Ёрдам"),  # i18n: ok
    ("landing", "Тизим ҳақида", "Ёрдам"),  # i18n: ok
    ("about_us", "Биз ҳақимизда", "Ёрдам"),  # i18n: ok
)
SIDE_KEYS = {k for k, _l, _g in SIDE_ITEMS}


def side_hidden() -> frozenset:
    """Панелда яширилган бандлар калитлари (SiteSetting "side_hidden")."""
    from panel import conf
    v = conf.raw("side_hidden", [])
    return frozenset(k for k in (v if isinstance(v, list) else []) if k in SIDE_KEYS)


def side_menu() -> dict:
    """Шаблон учун: {"off": яширилганлар, "groups": {"Иш": бирортаси кўринадими, "Ёрдам": …}}."""
    off = side_hidden()
    groups = {}
    for k, _l, g in SIDE_ITEMS:
        groups[g] = groups.get(g, False) or k not in off
    return {"off": off, "ish": groups["Иш"], "yordam": groups["Ёрдам"]}  # i18n: ok


def _clean(v) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "").replace("\r", "")).strip()[:MAX_LEN]


def stored() -> dict:
    from panel import conf
    v = conf.raw("landing", {})
    return v if isinstance(v, dict) else {}


def content() -> dict:
    """Саҳифа учун: {блок: {майдон: матн, "rows": [(…), …], "icons": (…)}} — панелдаги ўзгаришлар билан."""
    over, out = stored(), {}
    for b in BLOCKS:
        o = over.get(b.key) if isinstance(over.get(b.key), dict) else {}
        # стандарт матн — жорий тилда; панелда ўзгартирилгани — таржимаси бўлса таржимаси, бўлмаса ўзи
        d = {k: ui(_clean(o.get(k)) or default) for k, _label, default in b.fields}
        rows = o.get("rows")
        if b.cols and isinstance(rows, list) and rows:
            n = len(b.cols)
            d["rows"] = [tuple((_clean(r[i]) if i < len(r) else "") for i in range(n))
                         for r in rows if isinstance(r, (list, tuple)) and any(_clean(x) for x in r)][:MAX_ROWS]
        if b.cols and not d.get("rows"):
            d["rows"] = list(b.rows)
        if b.cols:
            d["rows"] = [tuple(ui(x) for x in r) for r in d["rows"]]
        if b.icons:
            d["rows"] = [(b.icons[i % len(b.icons)],) + tuple(r) for i, r in enumerate(d["rows"])]
        out[b.key] = d
    return out


def save(post, user) -> List[str]:
    """Панел формасидан: ``f_<блок>_<майдон>`` ва ``r_<блок>_<устун>`` (рўйхат). Стандарт билан бир хили сақланмайди.
    Қайтаради — ўзгарган блоклар номи."""
    from panel import conf
    old, new = stored(), {}
    for b in BLOCKS:
        d = {}
        for k, _label, default in b.fields:
            v = _clean(post.get(f"f_{b.key}_{k}"))
            if v and v != default:
                d[k] = v
        if b.cols:
            cols = [post.getlist(f"r_{b.key}_{i}") for i in range(len(b.cols))]
            rows = [[_clean(c[j]) if j < len(c) else "" for c in cols] for j in range(max(map(len, cols), default=0))]
            rows = [r for r in rows if any(r)][:MAX_ROWS]
            if rows and [tuple(r) for r in rows] != list(b.rows):
                d["rows"] = rows
        if d:
            new[b.key] = d
    changed = [b.title for b in BLOCKS if old.get(b.key) != new.get(b.key)]
    if changed:
        conf.put("landing", new, user)
    return changed


# ===================== блог =====================
_TRANSLIT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y",  # i18n: ok
             "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",  # i18n: ok
             "х": "x", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",  # i18n: ok
             "ў": "o", "қ": "q", "ғ": "g", "ҳ": "h", "ʻ": "", "'": "", "’": ""}  # i18n: ok
RESERVED_SLUGS = {"video", "page", "feed", "c"}


def slugify(text: str) -> str:
    """«Смета қандай нархланади?» → «smeta-qanday-narxlanadi» (манзил учун: лотин ҳарф, рақам ва «-»)."""
    s = "".join(_TRANSLIT.get(ch, ch) for ch in str(text or "").lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:80].strip("-")
    return s


def video_sections() -> list:
    """Блог учун: [{key, slug, label, videos: [{title, id, note, added}]}] — фақат видеоси бор бўлимлар,
    тартиби ``VIDEO_PAGES`` дагидек (бўлим — панелда видеога танланган «саҳифа»)."""
    from .guide import VIDEO_PAGES, videos
    by_page = {}
    for v in videos():
        by_page.setdefault(v["page"], []).append(v)
    return [{"key": k, "slug": k or "umumiy", "label": _(label) if k else _("Умумий дарслар"), "videos": by_page[k]}
            for k, label in VIDEO_PAGES if by_page.get(k)]


def find_video(video_id: str):
    """(видео, бўлим) ёки (None, None)."""
    for sec in video_sections():
        for v in sec["videos"]:
            if v["id"] == video_id:
                return v, sec
    return None, None


def render_body(text: str):
    """Мақола матни → хавфсиз HTML. Қоидалар: бўш қатор — янги хатбоши; «## » — сарлавҳа; «- » — рўйхат банди.
    Ҳамма матн экранланади (HTML ёзиб бўлмайди)."""
    from django.utils.html import escape
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
            items.clear()

    for line in str(text or "").replace("\r", "").split("\n"):
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("## "):
            flush()
            out.append(f"<h2>{escape(s[3:].strip())}</h2>")
        elif s.startswith("- "):
            if para:
                flush()
            items.append(escape(s[2:].strip()))
        else:
            if items:
                flush()
            para.append(escape(s))
    flush()
    from django.utils.safestring import mark_safe
    return mark_safe("".join(out))  # ҳар бир бўлак юқорида экранланган

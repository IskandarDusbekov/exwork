"""SEO: қидирув тизимлари учун сарлавҳа, тавсиф, тасдиқлаш кодлари (Google, Yandex, Bing), аналитика, robots.txt,
sitemap.xml ва ижтимоий тармоқ расми. Ҳаммаси панелдан ўзгаради (``SiteSetting`` key="seo"), кеш 30 сония.

Аналитика (Google Analytics, Яндекс Метрика) фақат очиқ саҳифаларда (бош саҳифа, кириш, рўйхатдан ўтиш) уланади —
фойдаланувчиларнинг сметалари ва панел саҳифалари бегона серверларга ҳисобот бермайди."""
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

KEY = "seo"
CACHE_KEY = "panel:seo"
IMAGE_MAX = 2 * 1024 * 1024
IMAGE_TYPES = {b"\x89PNG": ("png", "image/png"), b"\xff\xd8\xff": ("jpg", "image/jpeg"), b"RIFF": ("webp", "image/webp")}


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    default: str = ""
    help: str = ""
    max: int = 200
    multiline: bool = False
    placeholder: str = ""
    pattern: str = ""  # тўғри қиймат (тозалангандан кейин); бўш — ҳар қандай матн


FIELDS = (
    Field("site_name", "Сайт номи", "main", "mhisobot", "Қидирув натижасида сайт номи сифатида чиқади.", 60),
    Field("title", "Бош саҳифа сарлавҳаси", "main", "mhisobot — муҳандис ҳисобот: смета нархлаш ва техник назорат",
          "Google'да кўк рангли катта ёзув. 50–60 белги яхши — узуни кесилади.", 120),
    Field("description", "Тавсиф", "main",
          "Локал смета ресурсларини RES файли ва ресурс базаси билан бир неча сонияда нархлаш; "
          "техник назоратда бажарилган ишларни юритиш.",
          "Сарлавҳа остидаги кулранг матн. 120–160 белги — нима қилишингизни бир-икки жумлада.", 300, True),
    Field("keywords", "Калит сўзлар", "main", "смета, смета нархлаш, ресурс нархлари, RES, локал смета, техник назорат, "
          "техназор, офёрта, муҳандис ҳисобот, mhisobot",
          "Вергул билан. Google деярли ҳисобга олмайди, Yandex — бироз.", 400, True),
    Field("google", "Google Search Console", "verify", "",
          "«HTML-тег» усули: content=\"…\" ичидаги код ёки бутун <meta …> тег. «HTML-файл» усули: файл номи "
          "(google….html) ёки ичидаги матн — сайт шу файлни ўзи беради.", 200,
          placeholder="масалан: aBcD1234…", pattern=r"[A-Za-z0-9_\-.:=]{8,120}"),
    Field("yandex", "Яндекс Вебмастер", "verify", "",
          "«Мета-тег» усули: content=\"…\" ичидаги код ёки бутун тег. «HTML-файл» усули: файл номи "
          "(yandex_….html) — иккаласи бир хил код.", 200,
          placeholder="масалан: 1a2b3c4d5e6f7a8b", pattern=r"[A-Za-z0-9_\-]{8,120}"),
    Field("bing", "Bing Webmaster", "verify", "", "Ихтиёрий: msvalidate.01 мета-тегининг коди.", 200,
          pattern=r"[A-Za-z0-9_\-]{8,120}"),
    Field("ga", "Google Analytics (GA4)", "analytics", "", "Ўлчов ID: G-XXXXXXXXXX. Бўш — уланмайди.", 30,
          placeholder="G-XXXXXXXXXX", pattern=r"G-[A-Z0-9]{4,20}"),
    Field("metrika", "Яндекс Метрика", "analytics", "", "Ҳисоблагич рақами (фақат рақам). Бўш — уланмайди.", 20,
          placeholder="12345678", pattern=r"\d{4,12}"),
    Field("org_email", "Алоқа email", "org", "", "Қидирув тизимига ташкилот маълумоти (JSON-LD) учун, ихтиёрий.", 120,
          pattern=r"[^@\s]+@[^@\s]+\.[^@\s]+"),
    Field("org_phone", "Телефон", "org", "", "Масалан: +998 90 123 45 67. Ихтиёрий.", 40, pattern=r"\+?[\d\s()\-]{7,20}"),
    Field("telegram", "Telegram", "org", "", "Канал ёки бот ҳаволаси: https://t.me/… Ихтиёрий.", 120,
          pattern=r"https://t\.me/[A-Za-z0-9_/]{3,80}"),
)
BY_KEY = {f.key: f for f in FIELDS}
GROUPS = (
    ("main", "Қидирувда қандай кўринади", "Google ва Yandex натижасида чиқадиган сарлавҳа ва тавсиф."),
    ("verify", "Қидирув тизимларида тасдиқлаш", "Сайт сизники эканини Google, Yandex ва Bing'га исботлаш — "
     "кодни қўйиб сақланг, кейин уларнинг сайтида «Тасдиқлаш» тугмасини босинг."),
    ("analytics", "Ташриф статистикаси", "Неча киши, қаердан келди — Google ва Яндекс ҳисоботларида."),
    ("org", "Ташкилот маълумоти", "Қидирув тизими сайт кимники эканини тушунсин (структураланган маълумот)."),
)
GOOGLE_FILE_RE = re.compile(r"google[0-9a-f]{16}")  # Search Console «HTML-файл»: google<код>.html
_META_RE = re.compile(r"""content\s*=\s*["']([^"']+)["']""", re.I)


def _stored() -> dict:
    data = cache.get(CACHE_KEY)
    if data is None:
        from .models import SiteSetting
        row = SiteSetting.objects.filter(key=KEY).first()
        data = dict(row.value) if row and isinstance(row.value, dict) else {}
        data["_updated"] = row.updated.isoformat() if row else ""
        cache.set(CACHE_KEY, data, 30)
    return data


def values() -> dict:
    """Ҳамма майдонлар (сақланмаганлари — стандарт) + ``index`` (индексланишига рухсат) ва ``image``."""
    data = _stored()
    out = {f.key: data.get(f.key, f.default) for f in FIELDS}
    out["index"] = data.get("index", True)
    # Google'да мета-тег коди ва файл номи ҳар хил: файл номи қўйилган бўлса — мета-тег эмас, файл берилади
    out["google_file"] = out["google"] if GOOGLE_FILE_RE.fullmatch(out["google"]) else ""
    out["image"] = data.get("image", "")
    out["updated"] = data.get("_updated", "")
    return out


def clean(f: Field, raw: str) -> str:
    """Формадан келган қиймат: бутун мета-тег қўйилса — фақат content; бўш жой ва узунлик. Хато — ValueError."""
    v = (raw or "").strip()
    if f.group == "verify" and "<" in v:
        m = _META_RE.search(v)
        if not m:
            raise ValueError(f"{f.label}: тег ичида content=\"…\" топилмади — фақат кодни қўйинг")
        v = m.group(1).strip()
    if f.key == "google":  # файл номи ёки ичидаги «google-site-verification: google….html»
        v = re.sub(r"^google-site-verification:\s*", "", v, flags=re.I)
        name = re.sub(r"\.html?$", "", v, flags=re.I)
        v = name if GOOGLE_FILE_RE.fullmatch(name) else v
    if f.key == "yandex":  # yandex_<код>.html → код
        v = re.sub(r"^yandex_([0-9a-f]+)\.html?$", r"\1", v, flags=re.I)
    if f.key == "metrika":
        v = re.sub(r"\D", "", v) if re.fullmatch(r"[\d\s]+", v) else v
    if f.key == "ga":
        v = v.upper()
    if not f.multiline:
        v = " ".join(v.split())
    if len(v) > f.max:
        raise ValueError(f"{f.label}: {f.max} белгидан ошмасин (ҳозир {len(v)})")
    if v and f.pattern and not re.fullmatch(f.pattern, v):
        raise ValueError(f"{f.label}: нотўғри кўриниш — {f.help}")
    if not v and f.key in ("site_name", "title", "description"):
        raise ValueError(f"{f.label}: бўш бўлмасин")
    return v


def save(new: dict, user) -> list:
    """{key: қиймат} → базага; қайтади: ўзгарган майдонлар номлари (журнал учун)."""
    from .models import SiteSetting
    old = values()
    data = {k: new.get(k, old[k]) for k in [*BY_KEY, "index", "image"]}
    changed = [BY_KEY[k].label if k in BY_KEY else {"index": "Индекслаш", "image": "Расм"}[k]
               for k in data if data[k] != old[k]]
    SiteSetting.objects.update_or_create(key=KEY, defaults={"value": data, "updated_by": user})
    cache.delete(CACHE_KEY)
    return changed


# ===================== ижтимоий тармоқ расми =====================
def image_dir() -> Path:
    return Path(settings.MEDIA_ROOT) / "seo"


def image_path():
    name = values()["image"]
    p = image_dir() / name if name else None
    return p if p is not None and p.is_file() else None


def save_image(upload) -> str:
    """PNG / JPEG / WEBP, 2 МБ гача. Қайтади: файл номи (og.<ext>). Хато — ValueError."""
    if upload.size > IMAGE_MAX:
        raise ValueError("Расм 2 МБ дан катта бўлмасин")
    head = upload.read(12)
    upload.seek(0)
    kind = next((v for magic, v in IMAGE_TYPES.items() if head.startswith(magic)), None)
    if kind is None or (kind[0] == "webp" and head[8:12] != b"WEBP"):
        raise ValueError("Фақат PNG, JPG ёки WEBP расм")
    d = image_dir()
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("og.*"):
        old.unlink(missing_ok=True)
    name = f"og.{kind[0]}"
    with open(d / name, "wb") as fh:
        for chunk in upload.chunks():
            fh.write(chunk)
    return name


def image_type(path: Path) -> str:
    return {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}.get(path.suffix.lstrip("."), "image/png")

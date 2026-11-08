"""Нархлаш web: email + parol bilan kirish; har bir smetachi faqat o'z ishlarini ko'radi."""
import os
import sys
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

from urllib.parse import parse_qsl, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY o'rnatilmagan (production'da majburiy)")
    SECRET_KEY = "dev-only-not-for-production"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "jobs",
    "panel",
    "resbase",
    "texnazor",
    "support",
]

MIDDLEWARE = [
    "panel.access.AccessLogMiddleware",  # кириш уринишлари (хотирада йиғиб, дақиқада бир ёзади) — энг биринчи
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # static файллар (CSS, admin) — nginx'сиз ҳам
    "panel.middleware.BlockedIPMiddleware",  # блокланган IP — ҳеч нарса очилмайди
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Hamma sahifa sukut bo'yicha yopiq: ochiqlari (kirish, ro'yxat, «Тизим ҳақида») @login_not_required
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "panel.middleware.StaffTwoFactorMiddleware",  # панел / админ — фақат икки босқичли кириш билан
    "panel.middleware.SessionTrackMiddleware",  # IP, қурилма, охирги фаоллик
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
]

ROOT_URLCONF = "web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
            "accounts.context.signup",
            "support.context.support",
        ]},
    },
]

WSGI_APPLICATION = "web.wsgi.application"

# ===================== baza, kesh, fondagi ishlar =====================
# DATABASE_URL=postgres://user:parol@host:5432/narxlash  — PostgreSQL (production'da majburiy)
# bo'lmasa — SQLite (faqat ishlab chiqish uchun: bir vaqtda ko'p yozishni ko'tarmaydi)
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL:
    _db = urlsplit(DATABASE_URL)
    if _db.scheme not in ("postgres", "postgresql"):
        raise ImproperlyConfigured("DATABASE_URL: faqat postgres://… qo'llanadi")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(_db.path.lstrip("/")),
            "USER": unquote(_db.username or ""),
            "PASSWORD": unquote(_db.password or ""),
            "HOST": _db.hostname or "localhost",
            "PORT": _db.port or 5432,
            "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),  # ulanish qayta ishlatiladi
            "CONN_HEALTH_CHECKS": True,
            # ?sslmode=require kabi parametrlar to'g'ridan-to'g'ri psycopg'ga
            "OPTIONS": dict(parse_qsl(_db.query)),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            # timeout — papka fonda narxlanayotganda sahifa so'rovlari kutib turadi. IMMEDIATE — tranzaksiya yozish
            # qulfini boshida oladi: aks holda «o'qib-keyin-yozish» (update_or_create) parallel so'rovlarda kutmasdan
            # «database is locked» beradi. WAL — o'qish yozishni to'smaydi.
            "OPTIONS": {"timeout": 30, "transaction_mode": "IMMEDIATE",
                        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;"},
        }
    }

# REDIS_URL=redis://localhost:6379/0 — umumiy kesh: barcha sayt jarayonlari va worker'lar bir xil ko'radi
# (IP bloklar, onlayn holat, statistika keshi). Bo'lmasa — har jarayonning o'z xotirasi (faqat bitta jarayon uchun).
REDIS_URL = os.environ.get("REDIS_URL", "")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "narxlash",
            "TIMEOUT": 300,
        }
    }
# Sessiyalar bazada qoladi (kesh emas): admin sessiyani tugatsa yoki IP bloklasa — darhol kuchga kiradi.

# Fondagi ishlar (papka ochish/narxlash): "worker" — alohida ``manage.py run_worker`` jarayoni (production);
# "thread" — saytning o'zida oqim (ishlab chiqish). Production'da standart — worker.
# «Менинг офёрталарим» (narxlangan smetalardan ofyorta) — tugmalari hozircha yashirin; sahifalar URL bilan ochiladi
# Panel va Django admin — faqat ikki bosqichli kirish bilan (is_staff). O'chirish faqat favqulodda: 0
NARXLASH_STAFF_2FA = os.environ.get("NARXLASH_STAFF_2FA", "1") == "1"
TEXNAZOR_MY_OFYORTA = os.environ.get("TEXNAZOR_MY_OFYORTA", "0") == "1"
NARXLASH_TASKS = os.environ.get("NARXLASH_TASKS", "thread" if DEBUG else "worker")
if NARXLASH_TASKS not in ("thread", "worker"):
    raise ImproperlyConfigured("NARXLASH_TASKS: thread yoki worker")

LANGUAGE_CODE = "ru"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = False
USE_TZ = True

# ===================== auth va xavfsizlik =====================
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"  # киргандан кейин — «Иш столи»
# Django admin va boshqaruv paneli manzillari — production'da taxmin qilib bo'lmaydigan uzun yo'l qo'ying
ADMIN_URL = os.environ.get("NARXLASH_ADMIN_URL", "admin/")
PANEL_URL = os.environ.get("NARXLASH_PANEL_URL", "panel/")
for _name, _url in (("NARXLASH_ADMIN_URL", ADMIN_URL), ("NARXLASH_PANEL_URL", PANEL_URL)):
    if not re.fullmatch(r"[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*/", _url):
        raise ImproperlyConfigured(f"{_name}: faqat lotin harf, raqam, - va _, oxirida / (masalan: boshqaruv-7f3a9c/)")
if ADMIN_URL == PANEL_URL:
    raise ImproperlyConfigured("NARXLASH_ADMIN_URL va NARXLASH_PANEL_URL bir xil bo'lmasin")

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",  # eng kuchli; eski PBKDF2 xeshlar kirishda yangilanadi
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
     "OPTIONS": {"user_attributes": ("email", "first_name", "last_name")}},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SESSION_COOKIE_AGE = 7 * 24 * 3600
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# «Кириш уринишлари» (panel.access): тестларда ўчиқ — бошқа тестлардаги сўровлар саналмасин
NARXLASH_ACCESS_LOG = os.environ.get("NARXLASH_ACCESS_LOG", "1") == "1" and sys.argv[1:2] != ["test"]
CSRF_FAILURE_VIEW = "panel.access.csrf_failure"  # рад этилиш «Кириш уринишлари»да кўринади
CSRF_COOKIE_HTTPONLY = True  # JS tokenni formadagi yashirin maydondan oladi, cookie'dan emas
X_FRAME_OPTIONS = "DENY"
# Content-Security-Policy: skript, uslub, rasm va so'rovlar faqat o'z saytimizdan (shriftlar — Google Fonts).
# XSS bo'lib qolsa ham begona saytdan skript yuklab bo'lmaydi va ma'lumot boshqa domenga yuborilmaydi (connect-src,
# form-action). Sahifalardagi kichik ichki <script> / onclick lar hozircha bor — shuning uchun 'unsafe-inline'.
from django.utils.csp import CSP  # noqa: E402

SECURE_CSP = {
    "default-src": [CSP.SELF],
    # googletagmanager / mc.yandex.ru — панел «SEO» да аналитика ёқилса (фақат очиқ саҳифаларда уланади)
    "script-src": [CSP.SELF, CSP.UNSAFE_INLINE, "https://www.googletagmanager.com", "https://mc.yandex.ru"],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE, "https://fonts.googleapis.com"],
    "font-src": [CSP.SELF, "https://fonts.gstatic.com", "data:"],
    "img-src": [CSP.SELF, "data:", "blob:", "https://*.google-analytics.com", "https://www.googletagmanager.com",
                "https://mc.yandex.ru"],
    "connect-src": [CSP.SELF, "https://*.google-analytics.com", "https://*.analytics.google.com",
                    "https://www.googletagmanager.com", "https://mc.yandex.ru", "https://mc.yandex.com"],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
# Berilmasa — production'da ALLOWED_HOSTS dagi domenlardan: mhisobot.uz → https://mhisobot.uz,
# .mhisobot.uz → https://*.mhisobot.uz (localhost va IP — kerak emas)
if not CSRF_TRUSTED_ORIGINS and not DEBUG:
    CSRF_TRUSTED_ORIGINS = [
        "https://" + ("*" + h if h.startswith(".") else h) for h in ALLOWED_HOSTS
        if h not in ("*", "localhost") and not re.fullmatch(r"[\d.:\[\]]+", h)
    ]
# Nginx kabi proksi ortida: X-Forwarded-For / X-Forwarded-Proto ga faqat shunda ishoniladi.
# Qiymat — sayt oldidagi proksilar soni: faqat nginx — 1; Cloudflare + nginx — 2; to'g'ridan-to'g'ri — 0.
# Proksi ortida 0 qolsa, hamma foydalanuvchi bitta IP (proksi manzili) bo'lib ko'rinadi: kirish cheklovi va
# IP bloklash hammaga birdan ta'sir qiladi.
try:
    NARXLASH_TRUST_PROXY = max(0, int(os.environ.get("NARXLASH_TRUST_PROXY", "0") or 0))
except ValueError:
    raise ImproperlyConfigured("NARXLASH_TRUST_PROXY: proksilar soni (0, 1, 2 …)")
if NARXLASH_TRUST_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SSL_REDIRECT", "1") == "1"
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# ===================== loglar =====================
# Konsol + fayl (logs/narxlash.log, 10 MB × 5 ta, aylanib turadi) + panel («Хатолар»: WARNING va undan yuqori).
LOG_DIR = Path(os.environ.get("NARXLASH_LOG_DIR", BASE_DIR / "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "%(asctime)s %(levelname)s %(name)s [%(process)d %(threadName)s] %(message)s"},
    },
    "filters": {"skip_noise": {"()": "panel.logs.SkipNoise"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        "file": {"class": "logging.handlers.RotatingFileHandler", "filename": LOG_DIR / "narxlash.log",
                 "maxBytes": 10 * 1024 * 1024, "backupCount": 5, "encoding": "utf-8", "formatter": "verbose",
                 "delay": True},
        "panel": {"class": "panel.logs.DBLogHandler", "level": "WARNING", "filters": ["skip_noise"]},
    },
    "root": {"level": "INFO", "handlers": ["console", "file", "panel"]},
    "loggers": {
        "django.server": {"level": "INFO", "handlers": ["console"], "propagate": False},  # runserver сўровлари
        "django.db.backends": {"level": "WARNING"},
    },
}

# ===================== email (паролни тиклаш) =====================
# SMTP созламалари — панел «Почта» бўлимида (базада, пароль шифрланган) ёки шу ердаги .env да:
# EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS / EMAIL_USE_SSL. Иккаласида ҳам
# сервер бўлмаса — хатлар консолга чиқади (ишлаб чиқиш). panel.mail.PanelMailBackend ҳар сафар қайта ўқийди.
_email_host = os.environ.get("EMAIL_HOST", "")
NARXLASH_EMAIL_ON = bool(_email_host)  # фақат .env ҳақида; ҳақиқий ҳолат — panel.mail.configured()
MAILERS = {"default": {"BACKEND": "panel.mail.PanelMailBackend", "OPTIONS": {}}}
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "mhisobot <noreply@localhost>")
PASSWORD_RESET_TIMEOUT = 2 * 3600  # ҳавола 2 соат амал қилади
PASSWORD_RESET_CODE_MINUTES = 15  # хатдаги 6 рақамли код (шу браузерда киритилади)
EMAIL_VERIFY_LINK_HOURS = 48  # рўйхатдан ўтгандаги хат: тасдиқлаш ҳаволаси (код — PASSWORD_RESET_CODE_MINUTES)

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "web" / "static"]  # css/app.css — frontend/ да йиғилади (npm run build)
STATIC_ROOT = Path(os.environ.get("NARXLASH_STATIC_ROOT", BASE_DIR / "staticfiles"))  # manage.py collectstatic
STATIC_ROOT.mkdir(parents=True, exist_ok=True)  # WhiteNoise бўш бўлса ҳам кутади (ишлаб чиқишда огоҳлантирмасин)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # production: номида хеш (браузер кеши янгиланишда бузилмайди) ва gzip/brotli; collectstatic шарт
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Yuklangan va natija fayllar: media/jobs/<uuid>/
MEDIA_ROOT = Path(os.environ.get("NARXLASH_MEDIA_ROOT", BASE_DIR / "media"))
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
NARXLASH_MAX_UPLOAD = 30 * 1024 * 1024  # bitta fayl uchun
TEXNAZOR_MAX_UPLOAD = 60 * 1024 * 1024  # техназор офёрта файли
# Захира нусхалар (deploy/backup.sh ва панелдаги «Захира нусха»): db/ — база, media/<сана>/ — файллар
NARXLASH_BACKUP_DIR = Path(os.environ.get("NARXLASH_BACKUP_DIR", (BASE_DIR / "backups") if DEBUG else (BASE_DIR.parent / "backups")))
NARXLASH_MAX_ARCHIVE = 700 * 1024 * 1024  # ZIP/RAR arxiv (ichida PDF, chizmalar ham bo'ladi)
# Bitta foydalanuvchi serverni (disk, CPU) to'ldira olmasin. Xodimlar (is_staff) — cheklovsiz. 0 — cheklov yo'q.
NARXLASH_UPLOADS_PER_HOUR = int(os.environ.get("NARXLASH_UPLOADS_PER_HOUR", "30"))
NARXLASH_USER_QUOTA_MB = int(os.environ.get("NARXLASH_USER_QUOTA_MB", "5000"))
# Нархлаш қоидалари (панел «Созламалар» → «Нархлаш» да ўзгаради; бу ер — стандарт)
NARXLASH_AUTO_MIN_SCORE = 90  # «Автоматик»: яқин ресурс таклифи шу фоиздан ўхшаш бўлсагина ўзи олинади
NARXLASH_AUTO_SAME_WORD = True  # … ва номининг биринчи сўзи бир хил («МУСОР …» ≠ «ГВОЗДИ …»)
NARXLASH_AUTO_SAME_UNIT = True  # … ва бирлиги бир хил
NARXLASH_FUZZY_DEFAULT = 95  # янги сметада «ўхшаш» деб нархлаш чегараси (смета­чи ўзи 80–100 оралиғида ўзгартиради)
NARXLASH_BASE_PRICE_RATIO = 3  # ресурс базаси: медианадан шунча баравар фарқ қилган нарх — шубҳали
# Ro'yxatdan o'tish: open — hamma o'zi ochadi; closed — hisobni faqat admin yaratadi (Django admin)
NARXLASH_SIGNUP = os.environ.get("NARXLASH_SIGNUP", "open")
if NARXLASH_SIGNUP not in ("open", "closed"):
    raise ImproperlyConfigured("NARXLASH_SIGNUP: open yoki closed")
DATA_UPLOAD_MAX_NUMBER_FILES = 1000  # butun papka yuklanadi
DATA_UPLOAD_MAX_NUMBER_FIELDS = 3000

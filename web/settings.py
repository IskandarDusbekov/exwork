"""Нархлаш web: email + parol bilan kirish; har bir smetachi faqat o'z ishlarini ko'radi."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

from django.core.exceptions import ImproperlyConfigured

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY o'rnatilmagan (production'da majburiy)")
    SECRET_KEY = "dev-only-not-for-production"
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Hamma sahifa sukut bo'yicha yopiq: ochiqlari (kirish, ro'yxat, «Тизим ҳақида») @login_not_required
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
        ]},
    },
]

WSGI_APPLICATION = "web.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 30},  # papka fonda narxlanayotganda sahifa so'rovlari kutib turadi
    }
}

LANGUAGE_CODE = "ru"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = False
USE_TZ = True

# ===================== auth va xavfsizlik =====================
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "index"
ADMIN_URL = os.environ.get("NARXLASH_ADMIN_URL", "admin/")  # production'da taxmin qilib bo'lmaydigan yo'l qo'ying

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
CSRF_COOKIE_HTTPONLY = True  # JS tokenni formadagi yashirin maydondan oladi, cookie'dan emas
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]
# Nginx kabi proksi ortida: X-Forwarded-For / X-Forwarded-Proto ga faqat shunda ishoniladi
NARXLASH_TRUST_PROXY = os.environ.get("NARXLASH_TRUST_PROXY", "0") == "1"
if NARXLASH_TRUST_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SSL_REDIRECT", "1") == "1"
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Yuklangan va natija fayllar: media/jobs/<uuid>/
MEDIA_ROOT = Path(os.environ.get("NARXLASH_MEDIA_ROOT", BASE_DIR / "media"))
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
NARXLASH_MAX_UPLOAD = 30 * 1024 * 1024  # bitta fayl uchun
NARXLASH_MAX_ARCHIVE = 700 * 1024 * 1024  # ZIP/RAR arxiv (ichida PDF, chizmalar ham bo'ladi)
DATA_UPLOAD_MAX_NUMBER_FILES = 1000  # butun papka yuklanadi
DATA_UPLOAD_MAX_NUMBER_FIELDS = 3000

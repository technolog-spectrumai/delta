"""
Django settings for the delta project.

delta is a math-focused e-learning host on the toto platform (sibling to zenobia
and faros). It is a small, fixed-shape WSGI host: the base toto platform plus the
revived education apps carried as delta's own toto.* namespace portion under
delta/toto/ (academy, quizzes, competence, palimpsest, library, subscriptions).

It deliberately omits the heavy tiers — no Neo4j/graph, no AI, no realtime chat,
no media/ffmpeg pipeline. Video lessons ride a plain vault.VaultFile upload
(Lesson.video_file) or an external URL (Lesson.lecture_video_url); the
practice/quiz flow is all synchronous HTTP. A small Celery worker + beat pair
exists solely to recompute the recommendation similarity matrix (daily).

All configuration is driven by environment variables — no YAML is read here.

Key env vars:
  DEBUG=1                       (default: 1)
  DJANGO_ENV=PROD               (default: dev — set to PROD for production)
  SERVER_MODE=wsgi              (default: wsgi)
  BUILD_GEO=1                   (GIS on by default; 0 drops GDAL/GEOS/spatialite)
  SECRET_KEY, FIELD_ENCRYPTION_KEY, SSO_VAULT_PASSWORD
  ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, PLATFORM_DOMAIN
  DB_ENGINE(postgis|spatialite), DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
  REDIS_CACHE_URL
  CELERY_BROKER_URL              (unset -> tasks run eagerly in-process)
  CELERY_TASK_ALWAYS_EAGER=1     (force eager mode even with a broker)
  EMAIL_BACKEND(console|smtp|dummy), EMAIL_HOST, EMAIL_PORT, EMAIL_USE_TLS, ...
  FULL_INGRESS=1
"""
import os
import base64
import secrets
from pathlib import Path

from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from toto.auth_config import authentication_backends, login_url, resolve_auth

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Core flags
# ---------------------------------------------------------------------------

DEBUG = os.environ.get("DEBUG", "1") == "1"
DJANGO_ENV = os.environ.get("DJANGO_ENV", "dev")

SECRET_KEY = (
    os.environ.get("SECRET_KEY")
    or ("toto-dev-secret-key-change-me" if DJANGO_ENV != "PROD" else secrets.token_urlsafe(50))
)

# ---------------------------------------------------------------------------
# Hosts / CSRF / CORS
# ---------------------------------------------------------------------------

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get(
        "ALLOWED_HOSTS",
        "localhost,127.0.0.1,web_delta,nginx,delta.local,testserver",
    ).split(",")
    if h.strip()
]

USE_X_FORWARDED_HOST = True

if DJANGO_ENV == "PROD":
    CSRF_TRUSTED_ORIGINS = [
        o.strip()
        for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
        if o.strip()
    ]
else:
    CSRF_TRUSTED_ORIGINS = [
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8080",
        "http://localhost:8000",
        "http://localhost:8080",
    ]

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
]
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------

# BUILD_GEO — GIS toggle (see the toto suite README, "Making GIS optional"). On by
# default; setting it to 0 drops the whole GDAL/GEOS/spatialite stack and loads
# toto.locations geometry-less (Address keeps plain lat/lon floats).
HAS_GIS = os.environ.get("BUILD_GEO", "1") == "1"

INSTALLED_APPS = [
    "django_prometheus",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "corsheaders",
    "django_jsonform",
    "django_json_widget",
    "rest_framework",
    "colorfield",
    "reversion",
    "markdownx",
    "trix_editor",
    # --- toto base platform (the irreducible core every host installs) ---
    "toto.core",
    "toto.api",
    "toto.backup",
    "toto.gervazy",      # encryption / key management
    "toto.vault",        # encrypted file storage (backs Lesson.video_file + notes)
    "toto.people",
    "toto.locations",
    "toto.socialhub",
    "toto.events",
    "toto.verbena",      # abstract page/section base (palimpsest + academy scripts)
    "toto.quota",
    "toto.sso_core",
    "toto.sso_master",
    "toto.social_login",
    # --- delta education portion (revived from toto_libs/limbo, carried here) ---
    "toto.competence",   # skill badges / DAG — academy CourseModule.unlocks_badge
    "toto.quizzes",      # tasks: A–D + open answers, practice pool (delta additions)
    "toto.palimpsest",   # page/section notes — academy CourseModule.verbena_page
    "toto.library",      # bibliography / reference manager
    "toto.subscriptions",  # course gating + plans (academy soft-dep for enrollment)
    "toto.academy",      # LMS core: Course -> CourseModule -> Lesson
    "toto.backoffice",   # teacher back office (Panel autorski): shell + module registry
    "toto.vod",          # video-on-demand: native player for Vault video/audio files

    # The host project package itself — carries host-only tooling like the
    # smoke_urls management command (no models, no templates).
    "delta",
]

if not HAS_GIS:
    INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "django.contrib.gis"]
    MIGRATION_MODULES = {"locations": "toto.locations.migrations_nogis"}

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "toto.core.middleware.PlatformMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "delta.middleware.RoleAwareLandingMiddleware",
    "toto.core.middleware.ProfileLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

# ---------------------------------------------------------------------------
# URLs / WSGI
# ---------------------------------------------------------------------------

ROOT_URLCONF = "delta.urls"

# delta is a synchronous HTTP host — WSGI by default.
SERVER_MODE = os.environ.get("SERVER_MODE", "wsgi")
WSGI_APPLICATION = "delta.wsgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_db_engine = os.environ.get("DB_ENGINE", "spatialite" if DEBUG else "postgis")

if _db_engine == "spatialite":
    DATABASES = {
        "default": {
            "ENGINE": "django.contrib.gis.db.backends.spatialite" if HAS_GIS else "django.db.backends.sqlite3",
            # Distinct sqlite file so local delta dev never clobbers a sibling host's db.
            # DB_NAME (same var the postgres branch uses) overrides the path so
            # harnesses (scripts/ui_test.sh, the gate) can use a scratch file.
            "NAME": Path(os.environ["DB_NAME"]) if os.environ.get("DB_NAME") else BASE_DIR.parent / "db.delta.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }
    if HAS_GIS:
        SPATIALITE_LIBRARY_PATH = "mod_spatialite"
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.contrib.gis.db.backends.postgis" if HAS_GIS else "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "delta"),
            "USER": os.environ.get("DB_USER", "delta"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
        }
    }

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_redis_cache_url = os.environ.get("REDIS_CACHE_URL")

if _redis_cache_url:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": _redis_cache_url,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# ---------------------------------------------------------------------------
# Vault — encrypt synchronously by default. A Celery worker now runs in the
# stack (recommendation matrix), so VAULT_ENCRYPT_ASYNC=1 is viable if needed.
# ---------------------------------------------------------------------------
VAULT_ENCRYPT_ASYNC = os.environ.get("VAULT_ENCRYPT_ASYNC", "0") == "1"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Project-level templates win over app templates — used to override the
        # vendored oya/home.html with delta's own education welcome page.
        "DIRS": [str(BASE_DIR / "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "toto.core.context_processors.last_visited",
                "toto.backoffice.context_processors.backoffice_access",
                "toto.academy.context_processors.welcome_copy",
            ]
        },
    }
]

# ---------------------------------------------------------------------------
# Markdown (markdownx) — rich content for academy scripts. The custom math
# extension shields LaTeX ($…$, $$…$$) so Markdown never mangles a formula;
# KaTeX renders it client-side.
# ---------------------------------------------------------------------------
MARKDOWNX_MARKDOWN_EXTENSIONS = [
    "toto.academy.mdx_math",
    "fenced_code",
    "tables",
]

# ---------------------------------------------------------------------------
# Storage / static / media
# ---------------------------------------------------------------------------

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "toto.core.storage.ResilientManifestStaticFilesStorage"
    },
}

STATIC_URL = "/static/"
STATIC_ROOT = str(BASE_DIR / "staticfiles")
STATICFILES_DIRS = [str(BASE_DIR.parent / "data" / "img")]
MEDIA_URL = "/media/"
# MEDIA_ROOT override keeps harnesses out of the docker-owned media/ dir.
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media"))

# toto filesystem contract: shared seed/branding data and the run/ dir with local
# vault-password bundles — both at the repo root (mapped to /app/data + /app/run).
TOTO_DATA_DIR = str(BASE_DIR.parent / "data")
TOTO_RUN_DIR = str(BASE_DIR.parent / "run")

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

# Login strategy (toto.auth_config): delta is a standalone OIDC provider
# (the resolver default); registration is env-toggled and defaults open.
_A = resolve_auth(os.environ.get, default_open_registration=True)

AUTHENTICATION_BACKENDS = authentication_backends(_A)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = reverse_lazy(login_url(_A))

# ---------------------------------------------------------------------------
# i18n — delta is a Polish math e-learning product (English available too).
# ---------------------------------------------------------------------------

LANGUAGE_CODE = os.environ.get("LANGUAGE_CODE", "pl")
LANGUAGES = [
    ("pl", "Polski"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Security (production only)
# ---------------------------------------------------------------------------

def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    return default if v is None else v == "1"


if DJANGO_ENV == "PROD":
    _secure_default = not DEBUG
    SECURE_SSL_REDIRECT = _env_bool("SECURE_SSL_REDIRECT", _secure_default)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", _secure_default)
    CSRF_COOKIE_SECURE = _env_bool("CSRF_COOKIE_SECURE", _secure_default)
    # Must stay readable by JS: AJAX endpoints read the CSRF token from document.cookie.
    CSRF_COOKIE_HTTPONLY = False

# ---------------------------------------------------------------------------
# Celery — broker on redis db 0 (the cache stays on db 1). Without a broker
# URL (bare local dev, tests) tasks run eagerly in-process, so nothing needs
# a worker; the beat schedule then simply never fires. The deploy stack runs
# dedicated worker + beat containers (scripts/deploy.py, services.celery).
# ---------------------------------------------------------------------------

from celery.schedules import crontab

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "")
CELERY_TASK_ALWAYS_EAGER = _env_bool("CELERY_TASK_ALWAYS_EAGER", not bool(CELERY_BROKER_URL))
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TIMEZONE = TIME_ZONE
# No result backend: the task's outcome is the refreshed cache payload itself.
CELERY_BEAT_SCHEDULE = {
    "academy-recompute-badge-similarity": {
        "task": "toto.academy.tasks.recompute_similarity_matrix",
        "schedule": crontab(hour=3, minute=0),
    },
}

# ---------------------------------------------------------------------------
# Secrets / encryption
# ---------------------------------------------------------------------------

FIELD_ENCRYPTION_KEY = os.environ.get("FIELD_ENCRYPTION_KEY") or base64.urlsafe_b64encode(os.urandom(32)).decode()
SSO_VAULT_PASSWORD = os.environ.get("SSO_VAULT_PASSWORD", "")
SSO_OPEN_REGISTRATION = _A.open_registration
WALLET_VAULT_SECRET = os.environ.get("WALLET_VAULT_SECRET", SECRET_KEY)

if not SSO_VAULT_PASSWORD:
    import json as _json
    for _bundle in (BASE_DIR.parent / "run").glob("sso_*.json"):
        try:
            _vp = _json.loads(_bundle.read_text()).get("vault_password", "")
            if _vp:
                SSO_VAULT_PASSWORD = _vp
                break
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Toto platform settings
# ---------------------------------------------------------------------------

FULL_INGRESS = os.environ.get("FULL_INGRESS", "0") == "1"
LOGIN_RETRY_COOLDOWN_SECONDS = _A.login_retry_cooldown_seconds
CAPTCHA_RETRY_COOLDOWN_SECONDS = _A.captcha_retry_cooldown_seconds
TOTO_ADMIN_READONLY = os.environ.get("TOTO_ADMIN_READONLY", "0") == "1"
PLATFORM_LOGO_PATH = os.environ.get("PLATFORM_LOGO_PATH", os.path.join("..", "data", "img", "okti_old.png"))
ACME_CHALLENGE_ROOT = str(BASE_DIR / "acme-challenges")

# Seeding order for `manage.py ingress_all` — infra first, then content apps.
# locations/events are intentionally omitted: delta is an e-learning host and does
# not need their geospatial demo data (and their seed requires BUILD_GEO=1).
INGRESS_ALLOWED_APPS = [
    "toto.core", "toto.gervazy",
    "toto.socialhub",
    "toto.vault",
    "toto.sso_master",
    # delta education content
    "toto.competence", "toto.quizzes", "toto.palimpsest",
    "toto.subscriptions", "toto.academy", "toto.library",
]

APPS_TO_SYNC = [
    "locations", "events", "socialhub",
]

# toto.locations is installed only as a model dependency (people/socialhub/events
# FK into it) — delta mounts no locations URLs, so keep its UI (manual section,
# links) off or reverse('locations:…') would 500 (same pattern as faros).
LOCATIONS_UI_ENABLED = False

LOCATIONS_GEOCODING = {
    "enabled": True,
    "reverse_url": "https://nominatim.openstreetmap.org/reverse",
    "search_url": "https://nominatim.openstreetmap.org/search",
    "user_agent": "toto-locations/1.0",
    "timeout": 8,
    "accept_language": "pl",
    "fail_silently": True,
    "search_limit": 5,
}

# ---------------------------------------------------------------------------
# Dashboard / navigation
# ---------------------------------------------------------------------------

# Header mirrors zenobia: Dashboard + My Profile only — content is reached
# through the dashboard cards below.
HEADER_NAV_ITEMS = [
    {"label": _("Dashboard"), "icon": "fas fa-tachometer-alt", "url_name": "core:dashboard", "requires_auth": False},
    {"label": _("My Profile"), "icon": "fas fa-user", "url_name": "sso:my_profile", "requires_auth": True},
]

DASHBOARD_ITEMS = [
    # --- learning ---
    {"title": _("Nauka"),        "icon": "fa-solid fa-graduation-cap",      "description": _("Courses, lessons and practice tasks — high-school maths and matura."), "link": "academy:course-list",       "visibility": "public"},
    {"title": _("Zadania"),      "icon": "fa-solid fa-circle-question",     "description": _("Practice task pools — solve until every task in a section is done."),  "link": "quizzes:quiz-list",         "visibility": "public"},
    {"title": _("My progress"),  "icon": "fa-solid fa-chart-line",          "description": _("See how many tasks you have solved in each section."),                  "link": "academy:student-progress",  "visibility": "private"},
    {"title": _("My path"),      "icon": "fa-solid fa-route",               "description": _("Your personalized learning path — ordered steps to close your skill gaps."), "link": "academy:personal-path-list", "visibility": "private"},
    {"title": _("Skills"),       "icon": "fa-solid fa-diagram-project",     "description": _("The skill tree — badges you earn as you complete modules."),           "link": "academy:skill-forest",      "visibility": "public"},
    # --- materials ---
    {"title": _("Notatki"),      "icon": "fa-solid fa-pen-nib",             "description": _("Lesson notes and articles from your teachers."),                       "link": "palimpsest:page_list",      "visibility": "public"},
    {"title": _("Books"),        "icon": "fa-solid fa-book",                "description": _("Recommended books and reference materials."),                          "link": "library:book_list",         "visibility": "private"},
    {"title": _("Articles"),     "icon": "fa-solid fa-newspaper",           "description": _("Curated articles and further reading."),                               "link": "library:article_list",      "visibility": "private"},
    {"title": _("Storage"),      "icon": "fa-solid fa-vault",               "description": _("Your files and downloadable PDF notes in encrypted storage."),         "link": "/vault/",                   "visibility": "private"},
    # --- account / community ---
    {"title": _("Subscription"), "icon": "fa-solid fa-id-card",             "description": _("Your active course, its validity and your discount codes."),           "link": "subscriptions:plan_list",   "visibility": "private"},
    {"title": _("SocialHub"),    "icon": "fa-solid fa-users",               "description": _("Meet other students and stay connected."),                             "link": "socialhub:profile_list",    "visibility": "private"},
    {"title": _("Events"),       "icon": "fa-solid fa-calendar-days",       "description": _("Upcoming lessons, deadlines and events."),                             "link": "events:event_list",         "visibility": "public"},
]

DASHBOARD_CATEGORIES = [
    {"title": _("Learning"),  "items": ["Nauka", "Zadania", "My progress", "My path", "Skills"]},
    {"title": _("Materials"), "items": ["Notatki", "Books", "Articles", "Storage"]},
    {"title": _("Account"),   "items": ["Subscription", "SocialHub", "Events"]},
]

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

_email_backend = os.environ.get("EMAIL_BACKEND", "console")

if _email_backend == "smtp":
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
    EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "0") == "1"
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
elif _email_backend == "dummy":
    EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

_platform_domain = os.environ.get("PLATFORM_DOMAIN", "localhost")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", f"noreply@{_platform_domain}")
SERVER_EMAIL = os.environ.get("SERVER_EMAIL", DEFAULT_FROM_EMAIL)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_log_dir = BASE_DIR / "logs"
_log_dir.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "[%(name)s] %(asctime)s %(levelname)s %(message)s"}
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "default"},
    },
    "root": {
        "handlers": ["console"],
        "level": "DEBUG" if DEBUG else "INFO",
    },
}

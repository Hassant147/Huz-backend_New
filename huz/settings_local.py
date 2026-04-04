from pathlib import Path

from decouple import config

from .settings import *  # noqa: F401,F403


DEBUG = config("DEBUG", cast=bool, default=True)
SERVE_MEDIA_AND_STATIC_FROM_DJANGO = True

local_db_path = Path(
    config("LOCAL_DB_PATH", default=str(BASE_DIR / "local.sqlite3"))
).expanduser()
if not local_db_path.is_absolute():
    local_db_path = BASE_DIR / local_db_path

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": local_db_path,
    }
}

EMAIL_DELIVERY_BACKEND = config("EMAIL_DELIVERY_BACKEND", default="smtp").strip().lower()

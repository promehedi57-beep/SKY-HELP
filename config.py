"""
Central configuration loader. (Phase 5: + Gemini model/timeout/cooldown settings.)
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_path: Path
    log_dir: Path
    admin_ids: tuple[int, ...]

    ai_enabled: bool
    gemini_api_keys: tuple[str, ...]
    gemini_model: str
    gemini_timeout_seconds: int
    gemini_key_cooldown_seconds: int
    gemini_timeout_cooldown_seconds: int
    ai_requests_per_minute: int
    max_context_messages: int

    fuzzy_match_threshold: float

    scraper_enabled: bool
    scraper_default_interval: int

    log_level: str


def _as_bool(value: str, default: bool = False) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on") if value else default


def _as_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_settings(env_file: Path | None = None) -> Settings:
    env_path = env_file or (BASE_DIR / ".env")
    if env_path.exists():
        load_dotenv(env_path)
    else:
        logger.warning(".env not found at %s — relying on environment variables", env_path)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty. Copy .env.example to .env and fill it in.")

    raw_admin = os.getenv("ADMIN_ID", "")
    admin_ids = tuple(int(x) for x in raw_admin.replace(";", ",").split(",") if x.strip().isdigit())

    raw_keys = os.getenv("GEMINI_API_KEYS", "")
    gemini_keys = tuple(k.strip() for k in raw_keys.split(",") if k.strip())

    settings = Settings(
        bot_token=bot_token,
        database_path=BASE_DIR / os.getenv("DATABASE_PATH", "data/bot.db"),
        log_dir=BASE_DIR / "logs",
        admin_ids=admin_ids,
        ai_enabled=_as_bool(os.getenv("AI_ENABLED", "true"), True),
        gemini_api_keys=gemini_keys,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
        gemini_timeout_seconds=_as_int(os.getenv("GEMINI_TIMEOUT_SECONDS"), 30),
        gemini_key_cooldown_seconds=_as_int(os.getenv("GEMINI_KEY_COOLDOWN_SECONDS"), 60),
        gemini_timeout_cooldown_seconds=_as_int(os.getenv("GEMINI_TIMEOUT_COOLDOWN_SECONDS"), 10),
        ai_requests_per_minute=_as_int(os.getenv("AI_REQUESTS_PER_MINUTE"), 5),
        max_context_messages=_as_int(os.getenv("MAX_CONTEXT_MESSAGES"), 15),
        fuzzy_match_threshold=_as_float(os.getenv("FUZZY_MATCH_THRESHOLD"), 0.82),
        scraper_enabled=_as_bool(os.getenv("SCRAPER_ENABLED", "true"), True),
        scraper_default_interval=_as_int(os.getenv("SCRAPER_DEFAULT_INTERVAL"), 300),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper().strip() or "INFO",
    )

    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    return settings


def setup_logging(level: str, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    file_handler = logging.FileHandler(log_dir / "bot.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)

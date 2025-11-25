"""Bot configuration and environment variables."""

import os
from enum import IntEnum
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

load_dotenv()


# --- Environment variables ---
# Required
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN not found in environment variables")
# Optional
DISCORD_BOT_OWNER_ID = os.environ.get("DISCORD_BOT_OWNER_ID")
# Wolfram Alpha API (https://developer.wolframalpha.com/access)
WOLFRAM_APP_ID = os.environ.get("WOLFRAM_APP_ID")
# Lavalink Music Server
LAVALINK_HOST = os.getenv("LAVALINK_HOST", "localhost")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", 2333))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")


# --- Directory structure ---
BASE_DIR = Path(__file__).parent
COGS_DIR = BASE_DIR / "cogs"
BACKUP_DIR = BASE_DIR / "backups"
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = BASE_DIR / "temp"


# --- Bot schema ---
BOT_ICON = "https://icon-library.com/images/icon-for-discord/icon-for-discord-17.jpg"
BOT_PREFIX = "s!"  # Legacy - all commands are slash commands currently


class Color(IntEnum):
    """Bot theme color palette."""

    SUCCESS = 0x57F287
    INFO = 0xFFAE00
    WARNING = 0xFEE75C
    ERROR = 0xED4245
    MUSIC = 0xFFAE00


# --- System constants ---
ENCODING = "utf-8"
"""Default text encoding for file I/O operations."""
DATE_FORMAT = "%d-%m-%Y"
"""Canonical format: DD-MM-YYYY."""
MAX_EMBED_FIELD_LENGTH = 1024
"""Discord embed field character limit."""
PAGE_SIZE = 10
"""Default number of items per page in paginated views."""
ERROR_THUMBNAIL = "https://cdn.discordapp.com/emojis/839119737458917467.webp?size=96&animated=true"  # mm-m-m-monkey  # noqa: E501


# --- Data Files ---
# fmt: off
_JSON_SUFFIX: Final = ".json"
LAST_RUN_FILE = (DATA_DIR / "last_run").with_suffix(_JSON_SUFFIX)  # Main bot
BLOCKED_USERS_FILE = (DATA_DIR / "blocked_users").with_suffix(_JSON_SUFFIX)  # Admin cog
BIRTHDAY_FILE = (DATA_DIR / "user_birthdays").with_suffix(_JSON_SUFFIX)  # Birthday cog
MUSIC_VOLUME_FILE = (DATA_DIR / "music_volumes").with_suffix(_JSON_SUFFIX)  # Music cog
REPORT_FILE = (DATA_DIR / "user_reports").with_suffix(_JSON_SUFFIX)  # Report cog
ANSWER_FILE = (DATA_DIR / "user_answers").with_suffix(_JSON_SUFFIX)  # Question cog
# fmt: on


# --- Cog settings ---
# main.py
AUTOSAVE_UPTIME_INTERVAL = 1800  # seconds (30 minutes)
DISCONNECT_TIMER_THRESHOLD = 3600  # seconds (1 hour)

# birthday_cog.py
BIRTHDAY_CHECK_INTERVAL = 300  # seconds (5 minutes)

# music_cog.py
MUSIC_AUTO_LEAVE_CHECK_INTERVAL = 60  # seconds (1 minute)
MUSIC_AUTO_LEAVE_TIMEOUT = 900  # seconds (15 minutes)
MUSIC_DEFAULT_VOLUME = 10  # percentage (10%)

# wolfram_cog.py
# https://developer.wolframalpha.com/access
WOLFRAM_API_URL = "http://api.wolframalpha.com/v2/query"
WOLFRAM_QUERY_TIMEOUT = 30
WOLFRAM_MAX_QUERY_LEN = 200

# Image settings
WOLFRAM_PLOT_RESIZE = (800, None)
"""Target plot width (height scales proportionally)."""
WOLFRAM_PLOT_MAX_SIZE = (1200, 1200)
"""Maximum plot dimensions before compression."""
WOLFRAM_PLOT_QUALITY = 90
"""JPEG quality (0-100)."""

# Fuzzy matching and search
FUZZY_THRESHOLD_DEFAULT = 95
"""Minimum fuzzy match score to consider a match (0-100)."""
FUZZY_MATCH_LIMIT = 10
"""Maximum number of fuzzy search results to return."""
SUGGESTION_THRESHOLD = 25
"""Minimum score to show as a suggestion (0-100)."""

# question_cog.py
MAX_ANSWER_SAMPLE_SIZE = 8
"""Minimum score to show as a suggestion (0-100)."""

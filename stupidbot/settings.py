"""Bot configuration and environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# --- Environment variables ---
# Required
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
# Optional
DISCORD_BOT_OWNER_ID = os.environ.get("DISCORD_BOT_OWNER_ID")
MESSAGE_LOG_ENABLED = os.getenv("MESSAGE_LOG_ENABLED", "false").lower() == "true"
# The sample configuration selects Unicode controls.
CUSTOM_EMOJIS_ENABLED = os.getenv("CUSTOM_EMOJIS_ENABLED", "true").lower() == "true"
# Wolfram Alpha API (https://developer.wolframalpha.com/access)
WOLFRAM_APP_ID = os.environ.get("WOLFRAM_APP_ID")
# Voice collector
VOICE_PROBE_ENABLED = os.getenv("VOICE_PROBE_ENABLED", "true").lower() == "true"
# Lavalink Music Server
LAVALINK_HOST = os.getenv("LAVALINK_HOST", "localhost")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "")
LAVALINK_SECURE = os.getenv("LAVALINK_SECURE", "false").lower() == "true"
LAVALINK_NODE_LABEL = os.getenv("LAVALINK_NODE_LABEL", "MAIN")
LAVALINK_CONNECT_RETRY_DELAY = float(os.getenv("LAVALINK_CONNECT_RETRY_DELAY", "30.0"))


# --- Directory structure ---
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


# --- Bot schema ---
BOT_PREFIX = "s!"  # Prefix messages receive slash-command guidance.


# --- System constants ---
ENCODING = "utf-8"
"""Default text encoding for file I/O operations."""


VOICE_PROFILE_TIMEZONE = os.getenv("VOICE_PROFILE_TIMEZONE", "UTC")


# --- Cog settings ---
# stupidbot/app/cli.py
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
WOLFRAM_API_URL = "https://api.wolframalpha.com/v2/query"
WOLFRAM_MAX_QUERY_LEN = 200
WOLFRAM_HTTP_TIMEOUT_SECONDS = 30
WOLFRAM_XML_MAX_RESPONSE_BYTES = 2 * 1024 * 1024

# Image settings
WOLFRAM_PLOT_REQUEST_WIDTH = 400
WOLFRAM_PLOT_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
WOLFRAM_PLOT_MAX_SOURCE_PIXELS = 25_000_000
WOLFRAM_PLOT_MAX_UPLOAD_BYTES = 9 * 1024 * 1024
WOLFRAM_PLOT_MAX_SIZE = (1200, 1200)

# Fuzzy matching and search
FUZZY_THRESHOLD_DEFAULT = 90
"""Minimum fuzzy match score to consider a match (0-100)."""
FUZZY_MATCH_LIMIT = 10
"""Maximum number of fuzzy search results to return."""

# ask_cog.py
MAX_ANSWER_SAMPLE_SIZE = 8
"""Maximum number of answer samples to consider."""

# Voice collection and journal lifecycle
VOICE_PROBE_HEARTBEAT_SECONDS = 60
"""Liveness checkpoint and clock-discontinuity check cadence in seconds."""
VOICE_PROBE_FULL_ANCHOR_SECONDS = 900
"""Seconds between full authoritative Discord cache snapshots."""
VOICE_PROBE_EVENT_QUEUE_MAX = 10_000
"""Buffer between the event listener and the single journal writer."""
VOICE_PROBE_WRITER_BATCH_MAX = 500
"""Maximum number of facts committed per journal batch."""
VOICE_PROBE_MONOTONIC_JUMP_SECONDS = 30.0
"""Delta above which the process clock is treated as suspended."""

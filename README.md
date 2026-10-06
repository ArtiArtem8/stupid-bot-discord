# StupidBot

[![Python](https://img.shields.io/badge/python-3.12-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.7.1%2B-5865F2?style=flat-square&logo=discord&logoColor=white)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Dependency Manager](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/b094540d4d7b4bbea618b775ce0597e7)](https://app.codacy.com/gh/ArtiArtem8/stupid-bot-discord/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)
[![Scrutinizer Code Quality](https://scrutinizer-ci.com/g/ArtiArtem8/stupid-bot-discord/badges/quality-score.png?b=main)](https://scrutinizer-ci.com/g/ArtiArtem8/stupid-bot-discord/?branch=main)

**StupidBot** is a Discord bot built with Python 3.12 and [discord.py](https://github.com/Rapptz/discord.py). It uses cogs for music, utilities, and server administration tools. Durable application data uses one SQLite database; user-facing text is mostly Russian.

## Features

- **Audio playback** through [Lavalink](https://github.com/lavalink-devs/Lavalink): `/join`, `/play`, `/stop`, `/skip`, `/pause`, `/resume`, `/queue`, `/volume`, `/leave`, `/rotate-queue`.
  - Supports YouTube, SoundCloud, and Yandex Music with the right Lavalink plugins.
  - The bot starts without Lavalink, but music commands stay unavailable until Lavalink is reachable.
- **WolframAlpha integration**: `/solve`, `/plot`.
- **Administration tools**: `/block`, `/unblock`, `/block-info`, `/list-blocked`.
- **Birthday system**: `/set-birthday`, `/setup-birthdays`, `/remove-birthday`, `/list-birthdays`.
- **Feedback reports**: `/report`, `/set-report-channel`.
- **Utilities**: deterministic Magic 8-Ball answers, greeting reactions, and Russian time formatting.

## Prerequisites

- Python 3.12.
- [uv](https://github.com/astral-sh/uv).
- A Discord bot token from the [Discord Developer Portal](https://discord.com/developers/applications).
- Optional: a [WolframAlpha App ID](https://developer.wolframalpha.com/) for `/solve` and `/plot`.
- Optional: Lavalink with Java 17+ for music.

## Installation

```bash
git clone https://github.com/ArtiArtem8/stupid-bot-discord.git
cd stupid-bot-discord
uv sync --locked --no-dev
```

Then create your local `.env` file:

```bash
cp .env.example .env
```

Edit `.env` and set at least `DISCORD_BOT_TOKEN`. If you want WolframAlpha or music commands, fill in the related values too.

For music, run Lavalink separately and make sure its host, port, password, and secure flag match `.env`. Without Lavalink, the rest of the bot still starts.

## Usage

Prepare `data/app.sqlite` before starting the bot, or select another prepared
file with `--database PATH`. Follow [SQLite setup, import and recovery](repositories/sqlite/README.md).
Startup rejects missing, incompatible or unfinished databases; migrations and
imports are explicit maintenance operations.

Start the bot with `uv`:

```bash
uv run --locked --no-dev main.py
```

Allocation tracing is disabled by default. For memory diagnostics, start with
`uv run --locked --no-dev main.py --tracemalloc`. Tracing adds CPU and memory
overhead and is intended for diagnostic runs.

Platform launcher scripts are also included:

```bash
# Linux/macOS
./runstupidbot.sh
```

```powershell
# Windows PowerShell
.\runstupidbot.bat
```

## Configuration

Global configuration is loaded in `config.py`.

- Environment variables cover the Discord token, optional owner ID, optional WolframAlpha ID, and Lavalink connection values.
- `VOICE_PROBE_ENABLED=true` enables voice collection (`cogs/voice/collector_cog.py`). Observed facts are stored in SQLite and retained indefinitely.
- `/voice-profile` privately shows your server-local voice XP, level and statistics by default. `private:false` publishes the card with an owner-only trash button; Refresh is also owner-only. Controls disappear after ten minutes of inactivity. Starter through Rare use PNG; Epic and higher use four-second lossless animated WebP, with PNG fallback. The renderer requires **Inkscape** on the bot host and the bundled Inter fonts. `VOICE_PROFILE_TIMEZONE` defaults to `UTC`. See [profile card setup](docs/voice/profile-card.md).
- Runtime files use `data/` and temporary renderer directories. Backups are explicit SQLite maintenance operations.
- Logging is configured in `utils/logging_setup.py`.
- Static strings and small resource lists live in `resources.py`.

## Development

The project uses Ruff, Basedpyright, ty, pytest, and prek. See [CONTRIBUTING.md](CONTRIBUTING.md) for the normal contributor workflow and the full local check command.

## License

This project is licensed under the [MIT License](LICENSE). Free to use, modify, and distribute.

Some UI icons are provided by [Icons8](https://icons8.com). Emoji images are kept in
[resources/emojis/](resources/emojis/) with the same names as the Discord application emojis.

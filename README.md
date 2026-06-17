# StupidBot

[![Python](https://img.shields.io/badge/python-3.12-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.7.1%2B-5865F2?style=flat-square&logo=discord&logoColor=white)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Dependency Manager](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/b094540d4d7b4bbea618b775ce0597e7)](https://app.codacy.com/gh/ArtiArtem8/stupid-bot-discord/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)
[![Scrutinizer Code Quality](https://scrutinizer-ci.com/g/ArtiArtem8/stupid-bot-discord/badges/quality-score.png?b=main)](https://scrutinizer-ci.com/g/ArtiArtem8/stupid-bot-discord/?branch=main)

**StupidBot** is a Discord bot built with Python 3.12 and [discord.py](https://github.com/Rapptz/discord.py). It uses cogs for music, utilities, and server administration tools. Runtime data lives in `data/` as JSON for now, and user-facing text is mostly Russian.

## Features

- **Audio playback** through [Lavalink](https://github.com/lavalink-devs/Lavalink): `/join`, `/play`, `/stop`, `/skip`, `/pause`, `/resume`, `/queue`, `/volume`, `/leave`, `/rotate-queue`.
  - Supports YouTube, SoundCloud, and Yandex Music with the right Lavalink plugins.
  - The bot starts without Lavalink, but music commands stay unavailable until Lavalink is reachable.
- **WolframAlpha integration**: `/solve`, `/plot`.
- **Administration tools**: `/block`, `/unblock`, `/blockinfo`, `/list-blocked`.
- **Birthday system**: `/setbirthday`, `/setup-birthdays`, `/remove-birthday`, `/list_birthdays`.
- **Feedback reports**: `/report`, `/set-report-channel`.
- **Utilities**: deterministic Magic 8-Ball answers, greeting reactions, and Russian time formatting.

New features usually belong in a cog under `cogs/`.

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
# Linux/macOS
cp .env.example .env
```

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

Edit `.env` and set at least `DISCORD_BOT_TOKEN`. If you want WolframAlpha or music commands, fill in the related values too.

For music, run Lavalink separately and make sure its host, port, password, and secure flag match `.env`. Without Lavalink, the rest of the bot still starts.

## Usage

Start the bot with `uv`:

```bash
uv run main.py
```

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
- Runtime directories are `data/`, `backups/`, and `temp/`.
- Logging is configured in `utils/logging_setup.py`.
- Static strings and small resource lists live in `resources.py`.

## Development

The project uses Ruff, Basedpyright, ty, pytest, and pre-commit. See [CONTRIBUTING.md](CONTRIBUTING.md) for the normal contributor workflow and the full local check command.

## License

This project is licensed under the [MIT License](LICENSE). Free to use, modify, and distribute.

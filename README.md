# StupidBot

[![Python](https://img.shields.io/badge/python-3.12-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.5.2%2B-5865F2?style=flat-square&logo=discord&logoColor=white)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Dependency Manager](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/b094540d4d7b4bbea618b775ce0597e7)](https://app.codacy.com/gh/ArtiArtem8/stupid-bot-discord/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)

**StupidBot** is a Discord bot built with Python 3.12 and [discord.py](https://github.com/Rapptz/discord.py). It has a component-based architecture (Cogs) to provide music playback, utilities, and server administration tools. All data is stored in the `data` directory in `.json` format *(might be changed in the future)*. Main language in representation layer is **Russian** (no translations yet).

## Table of Contents

- [Features](#features)
  - [Extensibility](#extensibility)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
  - [Logging](#logging)
  - [Resources](#resources)
- [Development](#development)
  - [Code Quality Tools](#code-quality-tools)
  - [Linting and Formatting](#linting-and-formatting)
  - [Type Checking](#type-checking)
- [License](#license)

## Features

- **Audio Playback** ([Lavalink](https://github.com/lavalink-devs/Lavalink)):
  - Commands: `/join`, `/play`, `/stop`, `/skip`, `/pause`, `/resume`, `/queue`, `/volume`, `/leave`, `/rotate-queue`
  - Supports YouTube, SoundCloud, Yandex Music and Soundcloud. Requires manual plugin installation.
  - *Note: Requires a running Lavalink server.*

- **WolframAlpha Integration** ([WolframAlpha API](https://products.wolframalpha.com/api/)):
  - Commands: `/solve`, `/plot`
  - Solves math problems and generates plots.
  - *Note: Requires a valid `WOLFRAM_APP_ID` in `.env`.*

- **Administration Tools**:
  - Commands: `/block`, `/unblock`, `/blockinfo`, `/list-blocked`
  - Manages blocked users with history tracking. Blocked users cannot interact with the bot.

- **Birthday System**:
  - Commands: `/setbirthday`, `/setup-birthdays`, `/remove-birthday`, `/list_birthdays`
  - Tracks birthdays and sends automated wishes in a configured channel, also gives a special role.
  - *Note: Requires `/setup-birthdays` to be run in the target channel.*

- **Feedback System**:
  - Commands: `/report`, `/set-report-channel`
  - Allows users to submit bug reports.
  - *Note: Requires `/set-report-channel` to configure the destination channel.*

- **Utilities**:
  - **Magic 8-Ball**: `/ask` - Deterministic answers cached per user.
  - **Message Reactions**: Fuzzy matching for greetings (e.g., "доброе утро").
  - **Russian Time Formatting**: Custom formatting for time durations.

### Extensibility

The bot is designed to be easily extensible. New features can be added by creating new Cog classes in the `cogs/` directory. The bot automatically discovers and loads all valid cogs upon startup.

## Prerequisites

- **Python 3.12**: Strictly required.
- **[uv](https://github.com/astral-sh/uv)**: Fast Python package installer and resolver.
- **[Lavalink](https://github.com/lavalink-devs/Lavalink)**: Required for music functionality (Java 11+).
- **Discord Bot Token**: From [Discord Developer Portal](https://discord.com/developers/applications).
- **WolframAlpha App ID**: From [WolframAlpha Developer Portal](https://developer.wolframalpha.com/).

## Installation

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/ArtiArtem8/stupid-bot-discord.git
   cd stupid-bot-discord
   ```

2. **Configure Environment:**

   Create a `.env` file in the root directory:

   ```env
   DISCORD_BOT_TOKEN=your_token_here
   DISCORD_BOT_OWNER_ID=your_id_here
   WOLFRAM_APP_ID=your_app_id_here

   # Lavalink Configuration (default params)
   LAVALINK_HOST=localhost
   LAVALINK_PORT=2333
   LAVALINK_PASSWORD=youshallnotpass
   ```

3. **Install Dependencies:**

   Use `uv` for dependency management.

   ```bash
   uv sync
   ```

4. **Setup Lavalink:**

   Download and run `Lavalink.jar`. Ensure the password matches your `.env`.

## Usage

Start the bot using `uv`:

```bash
uv run main.py
```

> [!NOTE]
> Ensure the Lavalink server is running before starting the bot to enable music functionality.

## Configuration

Global configuration is managed in [`config.py`](config.py).

- **Environment Variables**: API keys (`DISCORD_BOT_TOKEN`, `WOLFRAM_APP_ID`) and Lavalink credentials.
- **Directories**: Paths for data (`DATA_DIR`), backups (`BACKUP_DIR`), and temp files (`TEMP_DIR`).
- **Bot Settings**: Theme colors (`Color`), default prefix, and icons.
- *Other cog specific settings*

### Logging

Logging configuration is defined in [`utils/logging_setup.py`](utils/logging_setup.py). It handles console output and log formatting.

### Resources

Static data such as localized strings, birthday wishes, and magic 8-ball answers are stored in [`resources.py`](resources.py).

## Development

We *(Me)* enforce strict code quality standards using **Ruff** and **Pylance**.

### Code Quality Tools

- **[Ruff](https://github.com/astral-sh/ruff)**: A fast Python linter and formatter. It enforces code style, sorts imports, and catches common errors and bugs.
- **[Pylance](https://marketplace.visualstudio.com/items?itemName=ms-python.vscode-pylance)**: A performant language server for Python in VS Code. It provides static type analysis, helping to catch type-related errors before runtime.

### Linting and Formatting

```bash
# Run linting
uv run ruff check .

# Run formatting
uv run ruff format .
```

### Type Checking

Ensure you have the **Pylance** extension installed in VS Code. The project is configured to work seamlessly with Pylance for static type analysis.

## License

This project is licensed under the [MIT License](LICENSE). Free to use/modify/distribute

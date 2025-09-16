# StupidBot

[![Python](https://img.shields.io/badge/python-%3E%3D3.12-blue)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-%3E%3D2.5.2-blueviolet)](https://github.com/Rapptz/discord.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![ Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

StupidBot is a feature-rich, open-source Discord bot built with [discord.py](https://github.com/Rapptz/discord.py) v2.x. It's a modern rewrite of an older bot (v1.7), emphasizing slash commands, cogs for modularity, and integrations like WolframAlpha and Lavalink. Designed for fun and utility in guilds of any size, it handles birthdays, music, math solving, reports, and per-guild bot block for users.

## Features

- **Admin Tools** (`/block`, `/unblock`, `/blockinfo`, `/list-blocked`):  
  Manage blocked users with history tracking, name changes, and stats. Admins only.
  Blocked users are not allowed to use any of bots functionality.

- **Birthday System** (`/setbirthday`, `/setup-birthdays`, `/remove-birthday`, `/list_birthdays`):  
  Set personal birthdays (DD-MM-YYYY or YYYY-MM-DD). Auto-wishes in configured channel, assigns/removes roles.

- **WolframAlpha Integration** (`/solve`, `/plot`, context menu "Solve with Wolfram"):  
  Solve math problems or plot functions (e.g., `sin(x)`). Outputs embeds/images.

- **Music Player** ([Lavalink-powered](https://github.com/lavalink-devs/Lavalink): `/join`, `/play`, `/stop`, `/skip`, `/pause`, `/resume`, `/queue`, `/volume`, `/leave`, `/rotate-queue`):  
  Play tracks/playlists from YouTube, SoundCloud, Yandex Music, VK. Queue management, volume (0-200%), voice channel handling.

- **Question Magic 8-Ball** (`/ask`):  
  Ask questions for randomized, deterministic answers (e.g., "Will it rain?"). Caches per-user.

- **Message Reactions**:  
  Fuzzy-matches greetings (e.g., "доброе утро") for morning/evening responses. Logs attachments.

- **Report System** (`/report`, `/set-report-channel`):  
  Submit bugs/issues (cooldown: 1/min). Sends to dev channel with embeds; devs only for setup.

- **Utilities**:  
  - Uptime tracking (persists across restarts).
  - Russian time formatting (e.g., "1 час и 32 минуты").
  - Data backup (JSON).

## Installation

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/ArtiArtem8/stupid-bot-discord.git
   cd stupidbot
   ```

2. **Set Up Lavalink:**
   - Requires running Lavalink server (Java 11+ required)
   - Official repo: [lavalink](https://github.com/lavalink-devs/Lavalink)
   - Create `.env` file with Lavalink credentials:

     ```bash
     LAVALINK_HOST=localhost
     LAVALINK_PORT=2333
     LAVALINK_PASSWORD=youshallnotpass
     ```

3. **Install Dependencies:**

   ```bash
   pip install -e .
   ```

4. **Set Up Environment Variables:**

   Create a `.env` (if not already present) file or set environment variables in your system with at least:

   ```bash
   DISCORD_BOT_TOKEN=your_bot_token_here  # From Discord Developer Portal
   WOLFRAM_APP_ID=your_wolfram_app_id  # From WolframAlpha (free tier OK)
   DISCORD_BOT_OWNER_ID=your_user_id
   LAVALINK_HOST=localhost  # Lavalink server
   LAVALINK_PORT=2333 # default port
   LAVALINK_PASSWORD=youshallnotpass # default password
   ```

   Discord token: [Discord Developer Portal](https://discord.com/developers/applications) > Your Bot > Bot > Token.
   Intents: Enable Message Content, Members, Guilds in portal.

## Configuration

All global configuration is stored in [`config.py`](config.py). You can update settings such as:

- Bot prefix, token and fine tuning.
- File paths for data storage for various cogs.
- Logging configuration and formatting.
- Birthday wishes, questions, and answer lists.

## Running the Bot
>
> [!WARNING]  
> Lavalink server must be running before running the bot. Do it by yourself

After installing dependencies and setting up your environment variables, run:

```bash
python main.py
```

or just run the script using the [`runstupidbot.bat`](runstupidbot.bat) batch file (windows only):

The bot will start, load all cogs, and sync its slash commands.

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests to improve the project. Please ensure your code adheres to the current project structure and style.

## License

This project is licensed under the [MIT License](LICENSE). Free to use/modify/distribute

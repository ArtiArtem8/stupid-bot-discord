# Running your own StupidBot

This guide starts with a fresh clone and ends with a bot in your Discord server.
The commands work from the repository root. Use PowerShell on Windows or a shell
on Linux; where they differ, both versions are shown.

For code changes, use [CONTRIBUTING.md](CONTRIBUTING.md). If you already run an
older installation, start with [upgrading and backups](docs/storage/README.md#backup-restore-and-upgrades):
creating an empty database does not import existing data.

## What to install

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), a
Discord account that can add apps to a server, and Python 3.12. `uv` can install
the required Python version for you.

The additional programs depend on what you want to try:

| Feature | Extra setup |
| --- | --- |
| Birthdays, settings, reports, role monitoring and `/ask` | None beyond the bot and its SQLite database. |
| Music | A separate Lavalink v4 server and the source plugins you want to use. |
| Voice profile images | Inkscape on the same machine as the bot. Fonts are included in the repository. |
| `/solve` and `/plot` | A WolframAlpha App ID. |

You can start before setting up music, image rendering or WolframAlpha. Their
commands report that the feature is unavailable until its requirements are met.
Voice observations are collected by default, even without the image renderer.
Set `VOICE_PROBE_ENABLED=false` if you do not want to collect them.

## Create the Discord app

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
   and create an application.
2. On its **Bot** page, obtain a bot token. You will put it in `.env` below;
   keep it out of Git, screenshots and support messages.
3. Enable these **Privileged Gateway Intents** on the same page:

   | Intent | Use in this bot |
   | --- | --- |
   | Server Members | Member caches, role monitoring and birthday membership. |
   | Message Content | Greeting reactions, legacy prefix guidance and optional message-content logging. |
   | Presence | Requested by the current client configuration. |

   The running client requests all three. Disabling one in the portal while the
   client still requests it can prevent connection.
   [Discord explains the intent requirements here](https://docs.discord.com/developers/events/gateway#privileged-intents).
4. Install the app into your server with the `bot` and `applications.commands`
   scopes. Use **Guild Install**, or the OAuth2 URL generator if you are creating
   the invite URL manually.

Give the bot permissions in the channels where it will run:

| Use | Bot permissions |
| --- | --- |
| Replies and public messages | View Channels, Send Messages, Embed Links. |
| Replies inside threads | Send Messages in Threads. |
| Profile cards and plots | Attach Files. |
| Greeting reactions and existing-message operations | Add Reactions, Read Message History. |
| Music | View Channel, Connect and Speak in the voice channel. |
| Birthday roles and role restoration | Manage Roles, with the bot's role above the roles it manages. |

Channel overrides still apply. Members also need permission to use application
commands. Bot permissions are separate from access to administrative commands:
for example, `/settings-server` requires Manage Server from the person using it.
There is no need to grant the bot Administrator just to run these features.

## Get the code

Check that `git --version` and `uv --version` work in a new terminal, then run:

```sh
git clone https://github.com/ArtiArtem8/stupid-bot-discord.git
cd stupid-bot-discord
uv python install 3.12
uv sync --locked --no-dev
```

This creates `.venv` with the versions recorded in `uv.lock`. You do not need to
activate it when using `uv run`.

## Configure your instance

Copy the example once. On Windows:

```powershell
Copy-Item .env.example .env
```

On Linux:

```sh
cp .env.example .env
chmod 600 .env
```

Open `.env` in a text editor. Fill in `DISCORD_BOT_TOKEN` and choose
`VOICE_PROFILE_TIMEZONE`, such as `Europe/Moscow` or `Europe/Berlin`. This controls
the fallback calendar timezone for profiles; birthday delivery uses the host's
local date. Existing installations should keep their previous timezone.

`DISCORD_BOT_OWNER_ID` is optional. Set it to your Discord user ID if you want to
choose the account allowed to use owner commands, including `/health`; otherwise
the bot uses Discord's application ownership.

Leave `MESSAGE_LOG_ENABLED=false` for ordinary use. Enabling it records incoming
message content and edits in the bot logs. Operational warnings and errors are
logged either way.

The remaining settings are described beside their values in
[.env.example](.env.example). Existing process environment variables take
precedence over `.env`; restart the bot after changing configuration.

## Prepare the database and start

For a new installation:

```sh
uv run --locked --no-dev python -c "from pathlib import Path; Path('data').mkdir(exist_ok=True)"
uv run --locked --no-dev python -m stupidbot.storage --database data/app.sqlite migrate
uv run --locked --no-dev python -m stupidbot.storage --database data/app.sqlite check
uv run --locked --no-dev main.py
```

The maintenance commands return successfully without starting Discord. Startup
then reports `SQLite schema validated; storage is COMPLETE`,
`Application commands synced` and `Bot is ready`. In Discord, try `/ask` or
`/customize-me`. The owner can run `/health` for a private view of the database,
voice journal, background tasks and optional services.

Music may still be unavailable when the bot becomes ready; its connection runs
in the background. `/health` describes current process state rather than
performing an external connectivity test.

Press **Ctrl+C** once to stop and wait for `Bot stopped.` before closing the
terminal. Shutdown drains accepted voice writes and saves uptime before closing
SQLite. Linux services can request the same shutdown with SIGTERM.

To use a different prepared database, pass its path:

```sh
uv run --locked --no-dev main.py --database /path/to/app.sqlite
```

On Windows, a path such as `"D:\StupidBot\data\app.sqlite"` works too. Run only
one bot process against a database.

The included `.\runstupidbot.bat` and `./runstupidbot.sh` are interactive
launchers with a restart prompt. Use the Python entry point directly for a
background service.

## Optional setup

### Custom emoji

The example sets `CUSTOM_EMOJIS_ENABLED=false`, so buttons work with standard
Unicode emoji. Custom artwork is optional:

1. Upload the PNG files from
   [the emoji assets directory](stupidbot/discord_ui/assets/emojis/) to your
   application's emoji page in the Developer Portal.
2. Replace the numeric IDs beside the matching names in
   [emojis.py](stupidbot/discord_ui/emojis.py) with your uploaded IDs.
3. Set `CUSTOM_EMOJIS_ENABLED=true` in `.env` and restart.

The bundled IDs belong to the original bot. Existing installations that omit
the switch keep that artwork. The bot does not upload emoji at startup.

### Music

Install Lavalink v4 using its
[getting started instructions](https://lavalink.dev/getting-started/). Follow the
Java requirement for the version you install. Lavalink runs separately from the
Python bot; `uv sync` does not install it.

Set `LAVALINK_HOST`, `LAVALINK_PORT`, `LAVALINK_PASSWORD` and `LAVALINK_SECURE` in
`.env` to match that server. `localhost:2333` assumes both processes run on the
same host. In a container, `localhost` refers to that container, so use an address
reachable from the bot instead.

The example password is a placeholder; choose a password and use the same value
on both sides. Enable TLS only when your Lavalink endpoint serves HTTPS/WSS.
Install and configure the source plugins your chosen Lavalink version needs.
A successful node connection alone does not establish that YouTube, SoundCloud
or another source can load tracks.

Start Lavalink, join a voice channel and try `/play` with a track from a configured
source. Check pause, resume and skip. If Lavalink is offline, the bot retries the
connection while other features remain usable.

Playback controls normally require the bot's voice channel. A track's requester
can skip that currently playing track remotely. Manage Server permits remote
`/leave` and `/reconnect`; it does not bypass other playback controls.

For a repeatable node/API check, the project also has an
[opt-in Lavalink test](CONTRIBUTING.md#optional-integration-checks).

### Voice profiles

Install [Inkscape](https://inkscape.org/release/) and check `inkscape --version`
using the account that runs the bot. Keep the bundled fonts in
`stupidbot/assets/fonts/`; no separate FFmpeg installation is needed.

If Inkscape is installed outside PATH, set `INKSCAPE_BIN` to its executable.
On Windows this can be
`C:\Program Files\Inkscape\bin\inkscape.exe`. Restart the bot, join and leave a voice
channel, then try `/voice-profile`.

For font locations, service-account cache permissions and the native rendering
check, see [renderer setup](docs/voice/profile-card.md#setup).
Voice history is kept indefinitely in SQLite; disabling the collector stops new
observations without deleting existing history.

### WolframAlpha

Obtain an App ID from the [WolframAlpha developer site](https://developer.wolframalpha.com/),
set `WOLFRAM_APP_ID`, and restart. Try `/solve` with `2+2` and `/plot` with `sin(x)`.
An empty App ID leaves those commands unavailable.

## Keep it running

For a small local installation, leaving the terminal open is enough. On a Linux
host with systemd, first get the bot working interactively under the account
that will run it. The example below assumes that account is `stupidbot` and the
clone is `/home/stupidbot/stupid-bot-discord`. Adjust both paths and `User`.

Save this as `/etc/systemd/system/stupidbot.service`:

```ini
[Unit]
Description=StupidBot Discord bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=stupidbot
WorkingDirectory=/home/stupidbot/stupid-bot-discord
ExecStart=/home/stupidbot/stupid-bot-discord/.venv/bin/python main.py --database /home/stupidbot/stupid-bot-discord/data/app.sqlite
Restart=on-failure
RestartSec=10s
KillSignal=SIGTERM
KillMode=mixed
TimeoutStopSec=90s

[Install]
WantedBy=multi-user.target
```

The bot reads `.env` from the project. Its account needs write access to `data/`,
the log files, temporary files and the renderer's font cache.
[`KillMode=mixed`](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html#KillMode=)
sends the initial shutdown signal to the bot, allowing it to close its Inkscape
worker itself. The [systemd stop timeout](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html#TimeoutStopSec=)
is an upper limit, not a promise that every shutdown finishes within 90 seconds.

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now stupidbot
sudo systemctl status stupidbot
journalctl -u stupidbot -f
```

Stop with `sudo systemctl stop stupidbot`. Wait for it to exit before maintenance
or starting another instance. For an update, stop the service, follow the
[backup and upgrade procedure](docs/storage/README.md#backup-restore-and-upgrades),
then start it again.

## When something does not work

| Symptom | Check |
| --- | --- |
| Missing token or Discord login failure | Set the bot token in `.env`; check that an old process environment value is not overriding it. Reset an exposed or invalid token in the portal. |
| Discord rejects privileged intents | Enable the intents listed above for this application. |
| Missing database or unsupported schema | Use the explicit preparation or upgrade procedure for the file selected by `--database`. Startup does not migrate it. |
| Bot is online but a command is absent | Look for `Application commands synced`; check the app was installed with application command scope and that the member can use commands in that channel. |
| Buttons fail or custom icons are missing | Set `CUSTOM_EMOJIS_ENABLED=false` for Unicode, or follow [custom emoji setup](#custom-emoji) for your application. Restart after changes. |
| Music unavailable | Compare both Lavalink configurations; check the node log, source plugin and voice-channel permissions. |
| Profile unavailable | Check Inkscape and bundled fonts under the actual bot account; see the renderer setup page. |
| `/health` is refused | Use the application owner account or the explicit `DISCORD_BOT_OWNER_ID`. |
| Role assignment fails | Check Manage Roles, role order and whether Discord manages the target role. |

Start with `/health` and `discord-bot.log`; `discord-bot-debug.log` has more
detail. Both logs rotate. Keep `.env`, databases and message-bearing logs out of
public issue attachments. Back up the database with the
[SQLite maintenance commands](docs/storage/README.md#backup-restore-and-upgrades),
not by copying the main file while it is in use.

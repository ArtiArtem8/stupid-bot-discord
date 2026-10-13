<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/banner-light.png">
  <img src="docs/assets/banner-light.png" alt="StupidBot: music and voice profiles" width="1200">
</picture>

# StupidBot

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7.1%2B-5865F2?style=flat-square&logo=discord&logoColor=white)](https://github.com/Rapptz/discord.py)
[![Lavalink](https://img.shields.io/badge/Lavalink-E3A008?style=flat-square)](https://lavalink.dev/)
[![SQLite](https://img.shields.io/badge/SQLite-003B57?style=flat-square&logo=sqlite&logoColor=white)](https://www.sqlite.org/)
<br>
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json&style=flat-square)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![basedpyright](https://img.shields.io/endpoint?url=https://docs.basedpyright.com/latest/badge.json&style=flat-square)](https://docs.basedpyright.com/)
[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json&style=flat-square)](https://github.com/astral-sh/ty)
[![prek](https://raw.githubusercontent.com/j178/prek/master/docs/assets/badge.svg)](https://github.com/j178/prek)
[![CI](https://img.shields.io/github/actions/workflow/status/ArtiArtem8/stupid-bot-discord/ci.yml?branch=main&style=flat-square&label=CI&logo=githubactions&logoColor=white)](https://github.com/ArtiArtem8/stupid-bot-discord/actions/workflows/ci.yml)
[![Codacy](https://app.codacy.com/project/badge/Grade/b094540d4d7b4bbea618b775ce0597e7)](https://app.codacy.com/gh/ArtiArtem8/stupid-bot-discord/dashboard)
[![Scrutinizer](https://scrutinizer-ci.com/g/ArtiArtem8/stupid-bot-discord/badges/quality-score.png?b=main)](https://scrutinizer-ci.com/g/ArtiArtem8/stupid-bot-discord/?branch=main)
[![License](https://img.shields.io/badge/license-MIT-57A773?style=flat-square)](LICENSE)

**StupidBot** is a Discord bot for music, voice profiles and birthday greetings.
You can host your own instance.

The interface and profile cards support Russian, English and Korean.

**English** · [Русский](README.ru.md)

[Music](#music) · [Voice profiles](#voice-profiles) · [Birthdays](#birthdays) ·
[Language and timezone](#language-and-timezone) · [Engineering](#engineering) ·
[Run locally](#run-locally) · [Contributing](#contributing)

## Music

Start a track or playlist with `/play`. Restart, seek by ten seconds, pause,
resume or skip from the controller below the track.

![Discord controller: Rivals by Creo at 1:54, with restart, seek, pause and skip controls.](docs/assets/music-controller.webp)

<details>
<summary>Queue, playback demo and commands</summary>

![Discord queue: Rivals by Creo is playing; Unveil and Sphere are next, with navigation, close and refresh buttons.](docs/assets/music-queue.webp)

![Recording of the real controller: pause, seek and resume.](docs/assets/music-controls.webp)

| Commands | What they do |
| --- | --- |
| `/play`, `/play-next` | Add tracks or playlists to the queue, or place the request next. |
| `/queue` | Show the queue privately by default. |
| `/pause`, `/resume`, `/skip` | Control the current track. |
| `/shuffle`, `/repeat` | Shuffle the queue or toggle repeat. |
| `/rotate` | Move the current track to the end of the queue. |
| `/volume` | Set volume from 0 to 200. |
| `/stop`, `/leave` | Stop and clear the queue, or disconnect. |
| `/join`, `/reconnect` | Join a voice channel or reconnect. |

</details>

The queue is private by default; a session summary stays in the channel when
listening ends. Controllers appear for tracks longer than 45 seconds, excluding
live streams. Music requires Lavalink, with sources such as YouTube, SoundCloud
and Yandex Music depending on its configuration and plugins. Other features
remain available while Lavalink is unreachable.

## Voice profiles

`/voice-profile` shows your server-local level, XP, voice time and session count.
Cards and their details support Russian, English and Korean. Both private and public
cards use your personal language and timezone from `/customize-me`. Open cards keep
that context when refreshed.
Activity, People and XP buttons add details to the same message.

![Demo Listener's voice profile: Mythic level 40, 360 hours 45 minutes in voice and 80 sessions. Synthetic data.](docs/assets/profile.png)

<details>
<summary>See the animated profile</summary>

![Four-second animated Mythic profile using synthetic demonstration data.](docs/assets/profile-motion.webp)

Starter through Rare use PNG. Epic and higher use a four-second animated WebP,
with PNG fallback if encoding fails or the image exceeds the upload budget.

</details>

<details>
<summary>Explore activity details</summary>

![Activity detail card with a 30-day presence chart, session statistics and observation coverage. Synthetic data.](docs/assets/activity.png)

Activity covers the last 30 calendar dates in the configured timezone. People
shows time spent with other members; XP explains awards and reductions.
The cards distinguish missing observations from inactivity. See [profile behavior and rendering](docs/voice/profile-card.md)
and [XP progression](docs/voice/progression.md).

</details>

Profiles are private by default. `private:false` publishes the card; only its
owner can use the controls. Refresh updates the displayed cards together, and
Delete removes the message without deleting history or XP. Controls disappear
after ten minutes of inactivity.

## Birthdays

![Upcoming birthdays in Discord, using fictional example names and dates.](docs/assets/birthdays.webp)

Members register with `/set-birthday` and remove their date with
`/remove-birthday`. Accepted formats are `DD-MM-YYYY` and `YYYY-MM-DD`.
Administrators use `/setup-birthdays` to choose a congratulations channel and
an optional birthday role, and `/list-birthdays` to see upcoming dates privately
by default. Birthdays follow the host's local date; `VOICE_PROFILE_TIMEZONE`
only affects voice profiles.

| Also included | Commands and behavior |
| --- | --- |
| WolframAlpha | `/solve` for answers and calculations; `/plot` for graphs. Requires an App ID. |
| Magic 8-Ball | `/ask` remembers its answer to a repeated question. |
| Member roles | `/monitor` manages saved role snapshots and restoration. |
| Bot access | `/block`, `/unblock`, `/block-info` and `/list-blocked`. |
| Feedback | `/report` stores a report; `/set-report-channel` configures notifications. |
| Chat replies | Greeting reactions and guidance from legacy prefix commands to slash commands. |

## Language and timezone

`/customize-me` opens your personal language and timezone settings, shared across
servers. `/settings-server` sets the language of shared messages and the fallback
profile timezone for this server. It requires Manage Server. Both editors are
visible only to you.

<details>
<summary>Language priority, saving settings and reminders</summary>

Choose Russian, English, Korean or **As in Discord**, select a fixed timezone offset
from `UTC-11` to `UTC+12`, check the preview and press **Save**. Fixed offsets
do not follow daylight saving time. **Other…** lets you enter a regional IANA
timezone such as `Europe/Moscow`.
**Cancel** discards pending changes. **As in Discord** resets only the language;
leaving the custom timezone field blank restores the inherited timezone.
**Reset settings** restores both defaults; press **Save** to apply or **Cancel**
to discard the reset. Open editors keep their original display language and
reject a save if another editor has already changed those settings.

Private replies and all your profile cards use your settings, including public
cards. Other public messages use the server settings. The language preference
takes priority over the corresponding Discord locale, with Russian as the
fallback for unsupported or unavailable locales. Personal and server settings
are independent. Profile calendar dates use your timezone or
the server timezone if no personal override is set, then `VOICE_PROFILE_TIMEZONE`.
Language does not inherit from the server. Discord does not supply a timezone.

Command descriptions and built-in timestamps follow your Discord client settings.
User text, external Wolfram answers and previously saved `/ask` replies stay
unchanged. A new `/ask` answer is translated before saving; repeated questions
return that saved text.
The bot-wide uptime status remains Russian.

After the second successful private profile use, an optional settings reminder
can appear at most twice, at least 14 days apart. Choose **Later** or
**Do not remind me**; the bot does not send unsolicited DMs.

</details>

## Engineering

- **Playback state survives overlapping events.** [MusicPlayer](stupidbot/features/music/playback/player.py)
  serializes queue transitions and gives each playback attempt an identity.
  [Event handlers](stupidbot/features/music/runtime/playback_events.py) use it to distinguish
  delayed events from current playback. [Composition](stupidbot/features/music/composition.py)
  connects the player, recovery and Discord UI.
  The bot uses the [maintained Mafic fork](https://github.com/ArtiArtem8/mafic),
  pinned to an exact commit in [pyproject.toml](pyproject.toml) and [uv.lock](uv.lock).
  Its [changelog](https://github.com/ArtiArtem8/mafic/blob/maintained/lavalink-v4/FORK_CHANGELOG.md)
  covers playback identity, monotonic position and transfer contracts.
  Recovery checks voice readiness on the current player and target node before
  accepting a transfer; a recovered channel move keeps its active session.
- **Profiles read a timeline held in memory.** [VoiceAnalytics](stupidbot/features/voice/runtime.py)
  restores it at startup and updates it after journal writes commit. Normal
  profile requests use immutable snapshots without querying raw history in SQL.
  Repeated reads reuse the snapshot until that server's committed state changes.
  [Voice architecture](docs/voice/architecture.md) explains how sessions,
  observation gaps and recovery work.
- **One database, an explicit shutdown sequence.** [Database](stupidbot/storage/database.py)
  serves all durable features. Shutdown stops producers, finishes accepted
  voice writes and saves uptime before draining transactions and closing the
  database. [Storage documentation](docs/storage/README.md) covers
  explicit migrations, offline import, backups and recovery.

## Run locally

Existing deployments must [review guild languages before cutover](docs/storage/README.md#backup-restore-and-rollback).

For a new installation, explicitly choose the profile calendar timezone;
the example configuration suggests `Europe/Moscow`. Keep the existing
`VOICE_PROFILE_TIMEZONE` when upgrading. An unset value still means `UTC`,
and birthday scheduling continues to use the host's date.

You need **Python 3.12**, Git, [uv](https://docs.astral.sh/uv/getting-started/installation/)
and a bot application from the [Discord Developer Portal](https://discord.com/developers/applications).
Enable **Presence**, **Server Members** and **Message Content** privileged intents
on its Bot page. Install the application into your server with bot and application
command scopes. Grant the permissions needed for messaging, attachments and
voice playback, plus role management if you use birthday roles or restoration.

### Install and configure

```bash
git clone https://github.com/ArtiArtem8/stupid-bot-discord.git
cd stupid-bot-discord
uv sync --locked --no-dev
```

Copy [.env.example](.env.example) to `.env` (`cp .env.example .env` in a POSIX shell,
or `Copy-Item .env.example .env` in PowerShell). Set `DISCORD_BOT_TOKEN` before
starting the bot.

| Setting | Purpose |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Required Discord bot token. |
| `DISCORD_BOT_OWNER_ID` | Optional owner ID. |
| `VOICE_PROBE_ENABLED` | `true` by default; enables voice collection. Observations are retained indefinitely in SQLite. |
| `VOICE_PROFILE_TIMEZONE` | Default IANA timezone for profile calendar details when neither personal nor server settings specify one; `UTC` if unset. Does not change elapsed voice time or lifetime XP. |
| `WOLFRAM_APP_ID` | [WolframAlpha App ID](https://developer.wolframalpha.com/) for `/solve` and `/plot`; optional for other features. |
| `LAVALINK_HOST`, `LAVALINK_PORT` | Lavalink address, default `localhost:2333`. |
| `LAVALINK_PASSWORD`, `LAVALINK_SECURE` | Match the Lavalink server's password and TLS configuration. |

For music, run [Lavalink](https://lavalink.dev/getting-started/) separately and
configure the source plugins you need. Follow its installation guide for the
Java requirement of your Lavalink version.

For profiles, install **Inkscape** on the bot host and keep the
[bundled fonts](stupidbot/assets/fonts/) (Inter and Korean UI glyphs from Noto Sans KR).
Verify `inkscape --version` under the service
account. Missing Inkscape or fonts makes the profile command unavailable;
the rest of the bot can start. See [renderer setup](docs/voice/profile-card.md#setup).
Custom application emoji IDs in [shared emoji references](stupidbot/discord_ui/emojis.py) and the
[music controller](stupidbot/features/music/discord/views/controller.py) must be usable by your bot.

### Prepare the database and start

For a **new installation**, create and validate the SQLite database:

```bash
uv run --locked --no-dev python -c "from pathlib import Path; Path('data').mkdir(exist_ok=True)"
uv run --locked --no-dev python -m stupidbot.storage --database data/app.sqlite migrate
uv run --locked --no-dev python -m stupidbot.storage --database data/app.sqlite check
uv run --locked --no-dev main.py
```

> [!IMPORTANT]
> Startup requires a prepared database. It rejects missing, incompatible or
> unfinished files. For an existing installation, follow [storage migration and recovery](docs/storage/README.md)
> before starting; the commands above do not import legacy data.

To use a different prepared file:

```bash
uv run --locked --no-dev main.py --database /path/to/app.sqlite
```

<details>
<summary>Launchers and operational settings</summary>

From the repository root, `./runstupidbot.sh` (Linux/macOS) or
`.\runstupidbot.bat` (Windows PowerShell) starts the bot.

`LAVALINK_NODE_LABEL` defaults to `MAIN`; `LAVALINK_CONNECT_RETRY_DELAY` defaults
to 30 seconds. Renderer paths, font cache permissions and `INKSCAPE_BIN` are
documented in [profile setup](docs/voice/profile-card.md#setup).

Runtime files use `data/` and temporary renderer directories. Backups are explicit
SQLite maintenance operations. [stupidbot/settings.py](stupidbot/settings.py) contains operational
constants, [shared emoji references](stupidbot/discord_ui/emojis.py) contains shared control emoji, and
[logging setup](stupidbot/app/logging.py) configures logs.

Allocation tracing is off by default. `uv run --locked --no-dev main.py --tracemalloc`
enables it for diagnostics, with additional CPU and memory overhead.

</details>

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and the Ruff,
basedpyright, ty, pytest and prek checks. Technical documentation is in English.

## License and artwork

Created by [ArtiArtem8](https://github.com/ArtiArtem8).

[MIT](LICENSE). Banner artwork uses the bot's profile emblem and music palette;
editable SVG sources are in [docs/assets/](docs/assets/).
Some interface icons are from [Icons8](https://icons8.com);
their files are in [stupidbot/discord_ui/assets/emojis/](stupidbot/discord_ui/assets/emojis/).
Inter and Noto Sans KR use the SIL Open Font License;
see the [font licenses and source](stupidbot/assets/fonts/README.md).

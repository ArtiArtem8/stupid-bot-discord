# Voice profile cards

`/voice-profile` shows your server-local XP, level, voice time and session count.
The response is private by default. Use `private:false` to publish it in the
channel with a trash button that deletes only the message, not its history or XP.
Refresh and trash are restricted to the command owner. Refresh replaces the
attachment on the same message. After ten minutes of inactivity, the controls
are removed and the card stays.

## Setup

Install Inkscape on the bot host and check `inkscape --version` under the service
account. Keep `Inter.ttf`, `Inter-600.ttf`, `Inter-750.ttf` and their `OFL.txt`
license under `resources/fonts/`. Inkscape uses system fallback fonts for glyphs
outside Inter, including CJK and emoji. FFmpeg and gifsicle are not required.

| Setting | Default and purpose |
| --- | --- |
| `INKSCAPE_BIN` | Optional executable path; otherwise PATH and conventional Windows installation directories are checked. |
| `PROFILE_FONT_DIR` | Font directory, default `resources/fonts/` relative to the repository. |
| `PROFILE_CACHE_DIR` | Optional cache root. Its `fontconfig/` directory holds the font cache; the default is the service user's application cache. |
| `VOICE_PROFILE_TIMEZONE` | Visible timezone label, default `UTC`; invalid names fall back to UTC. It does not change XP or elapsed voice time. |

The service account needs permission to launch Inkscape and write temporary files
and the font cache. Missing Inkscape or fonts disables the profile command;
the rest of the bot can start. The command returns an unavailable message and
logs the initialization failure. After fixing setup, restart or reload the Cog.

## Images and caches

Cards are 960×480. Starter through Rare use PNG; Epic and higher use a looping,
four-second lossless WebP at 20 FPS. An encoding failure or oversized WebP falls
back to the prepared PNG. Delivery respects both the internal 5 MiB limit and
Discord's upload limit.

Avatars request 256 pixels, as GIF when animated and PNG otherwise. Guild icons
request 64 pixels as PNG. Their original compressed bytes stay in memory only:
at most 64 entries and 16 MiB, keyed by the full CDN URL including hash, format
and size. Unchanged images are reused across card updates and members' cards.
Failed downloads are retried; missing or corrupt images use placeholders.
Animated avatars retain their frames and timing, but lower tiers still use PNG.

Rendered media has a separate memory cache: 32 entries, 64 MiB and a five-minute
TTL. Matching requests share one job; at most four distinct jobs are admitted
and one runs at a time. One persistent `Inkscape --shell` process prepares the
layers, then Pillow composes the animation. The 80 RGBA frames alone need about
140.6 MiB, before encoder overhead.

Timelines are cached for at most four guilds. A miss reads all retained guild and
session history, including compressed days. Journal generations are global, so
activity in another guild can invalidate a timeline. There are no incremental
reads or per-guild revisions. Reload clears caches and closes the owned shell
after admitted work completes; cancelling a Discord request does not cancel
native work already running.

## Editing artwork

Runtime artwork lives under `cogs/voice/profile/assets/`. Voice Profile Studio
is a separate editor project; the bot uses its exported SVG and JSON assets.
Preserve canvas dimensions, layer and element IDs, and local resource references.

The bottom-left `scope-label` is static text and can be edited directly in
`profile.svg`. Its tracking date is a shared label, not a date calculated from
each guild's history. `timezone-label` and profile values are replaced at render
time. Appearance band colors are described in [progression](progression.md).

Restart or reload the profile Cog after changing artwork or fonts. Its startup
revision includes their contents, so new media uses the changed assets. Editing
assets during a render is not supported. For Python changes that affect pixels,
update the renderer version. Check both a PNG and an animated WebP before shipping.

## Native check

The ordinary test suite does not require Inkscape. Run the native check on a host
where it is installed:

```sh
VOICE_PROFILE_NATIVE=1 uv run --locked pytest -q tests/cogs/voice/profile/test_native.py
```

In PowerShell, set `$env:VOICE_PROFILE_NATIVE = '1'` before the same pytest command
and remove it afterward with `Remove-Item Env:VOICE_PROFILE_NATIVE`.

The check renders real PNG/WebP cards, checks dimensions and animation, and
verifies shell reuse and shutdown. It does not contact Discord. CI runs it
separately with the Ubuntu Inkscape package.

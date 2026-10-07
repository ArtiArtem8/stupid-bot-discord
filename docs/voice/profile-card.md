# Voice profile cards

`/voice-profile` shows your server-local XP, level, voice time and session count.
The response is private by default. Use `private:false` to publish it in the
channel with a trash button that deletes only the message, not its history or XP.
All controls are restricted to the command owner. The main profile stays a
standalone image attachment. Activity, People and XP each append one static PNG
in a separate image embed in the same message, in click order. Each used button
disappears after a successful edit. A failed render or upload leaves the message
and button available for retry. After ten minutes of inactivity, the controls are
removed and the cards stay.

Refresh replaces the main card and every already-open detail in their existing
order. It reads one timeline snapshot and resolves the profile identity once.
Every image must finish before one message edit publishes the replacement set.
New details retain the displayed profile's identity and tier until refresh.

Discord omits embedded uploads from `message.attachments`. Reveal retains their
IDs from the returned Discord image URLs alongside the main attachment, and
binds all detail embeds through `attachment://` filenames. Keeping both the IDs
and these bindings prevents deleted images and duplicate standalone previews.
Only the new PNG is uploaded on reveal; refresh replaces the full file set.

## Detail scopes and coverage

Activity shows the current guild's last 30 local calendar dates, including today,
using `VOICE_PROFILE_TIMEZONE`. Its first boundary is local midnight 29 dates
before today; its last boundary is the snapshot request time. DST days therefore
contain 23 or 25 elapsed hours where applicable. The chart always has 30 columns.
The `Daily activity` chart places dates left to right and local time from 00:00
at the top to 24:00 at the bottom. Filled spans show actual presence, including
separate pieces of a session that crosses midnight. Hatching marks the specific
unobserved times; observed absence stays empty. Future time today uses a separate
background. The legend sits beside the chart title.

Short visits have a minimum visible footprint authored in SVG. Footprints merge
when they overlap at display resolution, and every column clips its contents;
this never merges underlying intervals or adds time or XP. A short break within
a grouped session remains empty whenever the display resolution allows it.
On DST dates an asterisk and outlined clock-change span mark skipped or repeated
times. Repeated local times share one position, while totals keep their true
elapsed duration. Calendar projection lives in `api.voice.profile.calendar`;
pixel geometry stays in the SVG and renderer.

`Top days` uses total observed voice time per weekday across completed Monday-to-
Sunday weeks fully contained in the 30-date window. Partial weeks at either edge
are excluded, giving each weekday the same number of calendar dates. Boundaries
use the profile timezone; observation gaps remain unknown and are not filled.
It includes every weekday with at least 95% of the largest weekday total and
lists them Monday first. This is a display grouping threshold, not statistical
confidence or visit frequency. All seven close totals display `All days`, which
does not claim presence on every calendar date. Multiple names are abbreviated
or represented as a consecutive weekday range only when native text measurement
shows the full names exceed the SVG field width. The font size stays unchanged.

`Peak hours` uses the full 30-date window and shows local hourly intervals.
Equal maximum hours are retained: adjacent hours form one interval, two separate
intervals use compact hour labels, and more scattered ties show `Multiple peaks`.
Equal nonzero activity in all 24 buckets shows `All day`.

Detail metrics show `-` when their period has neither observations nor credited
user activity. Observed empty periods still show zero. Average and median session
lengths show `-` when there are no sessions. People and XP apply this distinction
independently to lifetime and recent values; coverage remains visible.

People lists lifetime human co-presence, ordered by shared time, one-on-one time
and user ID, and includes a small 30-day summary. One-on-one is a subset of shared
time. Unknown participants prevent one-on-one credit; bots are not companions.
With-bots time counts each interval once even when several bots were present and
does not imply playback. Names use member/user caches and fall back to
`Unknown user`. Their colors use each person's guild-local lifetime profile tier;
no member fetches or companion avatar downloads are needed.

XP explains lifetime awards using the canonical policy and separately displays
the last 30 days' XP. Components remain exact `Fraction` values until formatting.
Green positive terms and red reductions retain signs for amounts of at least one
XP. Zero values are unsigned and muted; `<1` values are unsigned and retain their
category color. Audio reductions show Muted (including Stage suppression) and
Deafened separately, attributing each interval only to its strongest restriction.
The Bonus limit row shows the combined stream/camera bonus above the existing cap.
All XP labels truncate fractional XP, matching the main profile. Compact labels also
truncate at their displayed precision. Components are formatted independently,
so their displayed sum can differ from the displayed total; exact awards remain
unchanged. Nonzero components below one XP display `<1`; nonzero durations below
one minute display `<1m`. Large detail durations use compact hours from 1,000
hours onward.

The next-level estimate divides exact remaining XP by pooled XP per voice hour
over the same 30 local dates. Longer observations contribute proportionally more
than short ones; inactive dates contribute neither XP nor voice hours.
`api.voice.prediction` uses the canonical XP and level projections.

The small neutral caption below lifetime XP explicitly says "in voice" and shows
approximate hours, or minutes below one hour. It is omitted with less than one
observed voice hour, or without earning voice in the last seven local dates.
These are availability guards, not calibrated accuracy thresholds. Coverage describes completeness separately and
does not multiply the measured pace. The estimate has no confidence range or
calendar completion date.

Every card labels recent coverage. People and XP also label lifetime coverage,
measured from the earliest retained guild evidence to the request time. Missing
history has no lifetime coverage denominator; it is not reported as known empty
time. Percentages truncate to two decimal places so incomplete observation never
rounds up to 100%. Lifetime values are limited to retained, observed history.

See [voice history](architecture.md#sessions-and-companions) for session grouping.

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
| `VOICE_PROFILE_TIMEZONE` | Calendar timezone for recent details and visible label, default `UTC`; invalid names fall back to UTC. It does not change lifetime XP or elapsed voice time. |

The service account needs permission to launch Inkscape and write temporary files
and the font cache. Missing Inkscape or fonts disables the profile command;
the rest of the bot can start. The command returns an unavailable message and
logs the initialization failure. After fixing setup, restart or reload the Cog.

Buttons use the custom emoji references in `resources.py`: `statistic` for
Activity, `social` for People, `xp` for XP, and the shared `restart` and `trash`
controls for Refresh and Delete. Emoji IDs are bound directly to the buttons;
startup does not fetch or discover emojis by name.

## Images and caches

Cards are 960×480. Starter through Rare use PNG; Epic and higher use a looping,
four-second lossless WebP at 20 FPS. An encoding failure or oversized WebP falls
back to the prepared PNG. Delivery respects both the internal 5 MiB limit and
Discord's upload limit.

Detail cards are always static transparent PNGs at 960×480. Their three editable
templates live in `assets/details/`, use the same semantic palette and emblems,
and share the same native shell and owned worker as the main card. They bypass
WebP encoding and have no rendered-detail cache. The View retains attachment
references through its message, reveal order and identity metadata, not media
bytes or timeline data.

Avatars request 256 pixels, as GIF when animated and PNG otherwise. Guild icons
request 64 pixels as PNG. Their original compressed bytes stay in memory only:
at most 64 entries and 16 MiB, keyed by the full CDN URL including hash, format
and size. Unchanged images are reused across card updates and members' cards.
Failed downloads are retried; missing or corrupt images use placeholders.
Animated avatars retain their frames and timing, but lower tiers still use PNG.

Rendered media has a separate memory cache: 32 entries, 64 MiB and a five-minute
TTL. Each cached main card includes the immutable progression and identity that
produced it; retained identity image bytes count toward the same byte limit.
Matching main requests share preparation, asset retrieval and rendering. Main
and detail jobs share a four-job admission limit and one worker, starting before
profile/detail calculations and asset reads. Details are not cached. Cancellation
does not release a slot until its work finishes. One persistent `Inkscape --shell`
process prepares the layers, then Pillow composes the animation. The 80 RGBA
frames alone need about 140.6 MiB, before encoder overhead.

Voice analytics is restored into RAM before collection starts. Confirmed batches
update it sequentially; unsupported ordering triggers a full rebuild. Requests
receive immutable snapshots without SQL reads. During recovery they receive the
existing busy response. Admission remains bounded, and cancellation retains its
slot until snapshot work physically finishes. Collector reload preserves the
application-owned analytics; profile reload closes its media resources.

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

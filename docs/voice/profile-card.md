# Voice profile cards

`/voice-profile private:false` renders the invoking user's current guild profile.
`private:true` makes the response ephemeral. The owner-only Refresh button edits
the same message and replaces its attachment. It resolves the current member and
assets again using guild/user IDs.

## Production setup

Install **Inkscape** on the service host and run `inkscape --version` under the
service account. On Windows, the production shell and native smoke have been
verified with **Inkscape 1.4.4 (dcaf3e7, 2026-05-05)**. This is a tested version,
not a claimed minimum. Linux versions require the native smoke below; no Linux
version was verified during the Windows integration. CI runs a separate native
smoke with the Ubuntu package and records its version.

Keep `Inter.ttf`, `Inter-600.ttf`, `Inter-750.ttf` and `OFL.txt` in
`resources/fonts/`. The fonts are validated on startup. Inkscape uses these fonts
and system fallback for glyphs outside Inter, including CJK and emoji. Do not
disable system fallback. No FFmpeg or gifsicle is needed for profile cards.

| Setting | Meaning and default |
| --- | --- |
| `INKSCAPE_BIN` | Optional executable path; otherwise PATH and conventional Windows installation directories are checked. |
| `PROFILE_FONT_DIR` | Optional directory containing all three Inter fonts; defaults to the repository's `resources/fonts/`, independently of cwd. |
| `PROFILE_CACHE_DIR` | Optional writable cache root; `fontconfig/` is created below it. Defaults to the service user's local application cache under `stupid-bot-discord/`. |
| `VOICE_PROFILE_TIMEZONE` | Visible timezone label, default `UTC`; invalid names fall back to UTC. |

The service account must be able to launch its own Inkscape child, write temporary
files, and write the persistent Fontconfig cache. Writable cache is separate from
the shipped fonts/artwork. Missing Inkscape or fonts disables only this feature;
the command returns a short unavailable message and startup diagnostics go to
the log. Reload the Cog after correcting configuration.

## Data and ownership

```text
VoiceCollectorCog.journal.snapshot_for_guild() -> _timeline() -> build_profile()
Discord member/assets -> CardIdentity
VoiceProfileCog -> ProfileMediaCache -> ProfileMediaRenderer
                                      -> SvgProfileRenderer -> NativeRasterizer
                                                            -> Inkscape --shell
```

The existing voice/progression policies remain authoritative. Timeline reads are
paired with journal owner epoch and persisted generation. The journal reads guild
and shared session history under one file lock; the Cog never retries full reads
because another batch arrived. The timeline LRU retains at most four guilds.
A miss still reads full history, and the generation remains journal-wide: writes
in another guild can invalidate an entry. Per-guild revisions and incremental
history reads are deferred. The entry limit does not bound one guild's history.
Domain calculations run off the event loop and compute only lifetime presence
and XP for the card. Renderer inputs contain typed profile values and image bytes,
never Discord objects.

One Cog owns one cache and one renderer. Initialization runs during Cog load, off
the event loop. One persistent shell rasterizes the base and effects layers;
Pillow composes frames from these prepared layers without rerasterizing text.
Unload stops admission, drains owned tasks, closes the media/SVG renderer and
terminates only its own shell. Closing is idempotent. Cancellation of a Discord
waiter or shutdown waiter does not cancel admitted native work.

## Media and resource limits

The approved layout is 960×480 for every tier:

- Starter, Uncommon and Rare: PNG.
- Epic, Mythic, Legendary, Ascendant and Transcendent: lossless animated WebP,
  four seconds at 20 FPS, looping, `method=1`, `quality=75`, `allow_mixed=False`,
  `minimize_size=False`, `kmax=0`.
- Animation encoding failure or oversized WebP: the already prepared PNG.
  Failure to prepare the base is an error, not an animation fallback.

The 80 RGBA frames require about 140.6 MiB of raw pixels at 960×480, plus Pillow
and encoder overhead. The 42-million-pixel guard permits about 160 MiB of raw
RGBA pixels; it is not a peak RSS limit.

The internal attachment limit is 5 MiB. Both initial delivery and Refresh use the
smaller of that limit and Discord's current `interaction.filesize_limit`, falling
back to the existing still if it fits. Every send creates a fresh `discord.File`.

Animated Discord avatars are requested as GIF; static avatars and guild icons as
PNG. Decoders inspect the actual format. CDN reads have a five-second timeout and
2 MiB compressed limit. Image decoding additionally bounds source pixels, avatar
frame count (200), total source pixels (32 million), and decoded avatar frames
(8 MiB). Missing or corrupt assets use the SVG placeholder. Lower tiers retain
their PNG policy even with animated avatars.

The cache admits at most four distinct jobs and executes one build at a time.
Matching keys share one owned task. Excess work gets a Russian busy response;
there is no cross-user latest-request-wins behavior. Cached immutable bytes use a
300-second TTL and LRU bounds of 32 entries and 64 MiB, including PNG fallbacks.
Keys include guild/user, journal epoch/generation, the visible timezone label,
names and asset keys, and artwork/font/runtime revision. Old-epoch/generation
results cannot publish over current results.

## Updating approved artwork

Voice Profile Studio stays a separate project and dependency environment.
Copy only reviewed production changes: `profile.svg`, the eight referenced
`emblem-*.svg` files, `themes.json`, `motion.json`, and the loader's `clips.json`
under `cogs/voice/profile/assets/`. Preserve SVG layers, IDs, placement and editor
metadata. Keep one font copy and its license under `resources/fonts/`.

Update the renderer version when changing pixel-affecting Python behavior.
Restart/reload the profile Cog after changing runtime code, artwork or fonts;
its revision hash is captured at startup and includes artwork and fonts. Runtime
hot editing of assets during a render is not supported. Compare at least one PNG
and one animated WebP with approved Studio exports before deployment. Keep
parity matrices, benchmarks, demo adapters and browser tooling in Studio.

## Verification

The root quality gate runs production unit/contracts without native tools. The
single native integration test is explicitly opt-in and fails on a missing or
broken renderer when enabled:

```powershell
inkscape --version
$env:VOICE_PROFILE_NATIVE = '1'
uv run --locked pytest -q tests/cogs/voice/profile/test_native.py
Remove-Item Env:VOICE_PROFILE_NATIVE
```

```sh
inkscape --version
VOICE_PROFILE_NATIVE=1 uv run --locked pytest -q tests/cogs/voice/profile/test_native.py
```

This checks bundled fonts/artwork, PNG/WebP dimensions, shell reuse and shutdown.
It does not contact Discord. A live attachment check requires an explicitly
authorized Discord development server and credentials.

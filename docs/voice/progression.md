# Voice XP, levels and appearance

## Scope

This change is a pure read-side extension of the existing voice timeline.
It does not alter collection, journal schema, retention, gaps, Discord events,
music, cogs, commands, databases or account identity. It does not award roles or
spendable currency. Numeric choices are a chosen game balance, not inferred
facts about conversation quality.

Three independent responsibilities:

```text
VoiceTimeline + VoiceScope + user_id
    -> VoiceXpPolicy.explain() -> VoiceXpBreakdown.total (Fraction)
    -> LevelPolicy.progress() -> LevelProgress.level (int)
    -> LevelAppearancePolicy.for_level() -> LevelTier + RGB + AppearanceFeature
```

These are caller compositions, not an import chain. `levels.py` does not import
voice; `appearance.py` imports neither voice nor `levels.py`. No module imports
Discord. No service locator, registry, abstract policy base or I/O is needed.

## Files and dependencies

- `api/voice/metrics/xp.py`: one-room hourly award, temporal MAX, exact integration.
  Imports the existing model, timeline, scope and read models only.
- `api/voice/read_models.py`: adds `VoiceXpBreakdown`. A summary exposes
  `xp_breakdown`, numeric `.xp` as a computed property and `xp_policy_version`.
- `api/voice/queries.py`: evaluates the breakdown once; does not choose colors.
- `api/progression/levels.py`: the level curve and exact progress value.
- `api/progression/appearance.py`: stepped presentation data and selection.
- `api/progression/__init__.py`: documentation only, no eager exports.

The old `VoiceXpPolicyV1` was a one-XP-per-minute calculation. The current
`VoiceXpPolicy(version="voice-v3")` multiplies every voice-v2 XP/hour amount by
100; its eligibility, modifiers and global overlap rules are unchanged. There is
no runtime fallback. Journal records are unchanged and remain readable.

## XP contract

A recipient must be a positively identified human in a known room, outside a gap.
Known AFK means zero XP, including all bonuses. `afk=None` is neutral.

Human count is the number of known human Discord user IDs in this room, including
the recipient. There is no linked-account/person model. Known bots and unknown
occupants do not unlock either group threshold. Peer mute/deaf flags do not change
headcount. AFK gates the recipient, not an additional hidden peer-count policy.

Hourly base is 300 for one known human, 1200 for two or more. Add 75 to the base for
five or more known humans. This is one threshold; a sixth or tenth person does
not add another bonus.

Use the smallest applicable audio factor, never their product:

| State | Factor |
| --- | ---: |
| Normal / no known restriction | 1 |
| Self or server mute | 0.85 |
| Suppress | 0.80 |
| Self or server deaf | 0.25 |

Only with another known human: stream adds 300 XP/hour, video adds 120 XP/hour.
Their combined bonus is capped at 360 XP/hour. These bonuses are added AFTER audio
adjustment. Thus deaf streaming still gets a contribution bonus. This is deliberate.
A visible stream flag does not establish an audience or prove somebody watches it.

```text
rate = (base + large_group_bonus) * minimum_audio_factor
       + min(stream_bonus + video_bonus, contribution_cap)
```

`None` audio flags do not trigger a restriction; `None` stream/video do not trigger
a bonus. An unknown recipient is not silently classified as human. Raised hand,
join counts, bots, streaks and message activity have no XP effect in this version.

Default examples in XP/hour:

| Context | Normal | Muted | Deaf | Stream | Stream + video |
| --- | ---: | ---: | ---: | ---: | ---: |
| Solo | 300 | 255 | 75 | 300 | 300 |
| 2-4 humans | 1200 | 1020 | 300 | 1500 | 1560 |
| 5+ humans | 1275 | 1083.75 | 318.75 | 1575 | 1635 |

## Global MAX and interval arithmetic

Filter and clip by `VoiceScope` first. For each subinterval between start/end
boundaries, choose the greatest COMPLETE context rate for this user. Never merge
flags across contexts, take the maximum of already-integrated totals, or add
simultaneous contexts. This also applies within a guild if input contexts overlap.
Normal canonical guild histories do not overlap for one user.

Tie break is the smallest `(guild_id, channel_id)`. A tie does not change total
XP, but deterministic selection keeps the component explanation stable.
An unavailable/gap context does not invalidate a separate observed context.

Example: rate 1200 at 00:00-01:00, rate 1500 at 00:30-01:30 gives
`0.5*1200 + 0.5*1500 + 0.5*1500 = 2100 XP`, not 2700 or 1500.

The sweep visits event boundaries, not seconds. With N room slices and at most K
simultaneous contexts, complexity is O(N log N + N*K), memory O(N + K). K is small
for one Discord ID in this bot. A heap is unnecessary until measurements justify it.

Endpoints are converted to UTC before duration arithmetic. Integer microseconds
and Fraction coefficients preserve exact arithmetic at the precision of the input
journal. They do not improve the accuracy of the original observation timestamps.
Do not round each room, session, day or report component. Display rounding is a
consumer concern. `Fraction(17, 20)` is exact; `Fraction(0.85)` imports float error.

The input is a canonical `VoiceTimeline` from the existing builder. Its room gaps
must already be split/attached, as required by `observed_rooms`; these tools do not
replay a second uncertainty model.

## Explanation and summary compatibility

`VoiceXpBreakdown` records seven positive component amounts:

```text
solo_base + social_base + large_group_bonus - audio_reduction
    + stream_bonus + video_bonus - bonus_cap_reduction = total
```

Only winning contexts contribute to the global explanation. Audio reduction is
one total, not three stacked penalties. Full stream/video contributions and the
cap reduction are shown separately to avoid an arbitrary cap allocation.

`rate()` returns these amounts for exactly one hour; `explain()` returns amounts
for the requested history. `calculate()` returns the exact scalar total.
`UserVoiceSummary.xp` is now Fraction, not float. Its dataclass field is now
`xp_breakdown`, not a second mutable/independent XP total. Current known consumers
are migrated. A future JSON boundary must explicitly choose decimal display or
numerator/denominator serialization. Do not pass through float before level lookup.

IMPORTANT: existing presence/activity/companions remain guild-additive. This patch
changes global XP to temporal MAX, not all global statistics. A global elapsed-time
union is a separate metric change. Do not label summed guild-seconds as deduplicated
person-time or use them as an XP/hour denominator without deciding that semantics.

## Levels

`LevelPolicy(coefficient=250)` uses cumulative `T(L) = 250*(L-1)^2`, with level one
at zero XP. There is no upper cap. Level lookup uses `isqrt(total_xp // 250) + 1`,
not a floating-point square root. Fractional XP is retained in progress.

`LevelProgress` exposes total XP, level, current/next thresholds, earned,
required, remaining and exact progress ratio. For 5075 XP: level 5, 1075/2250
earned, 1175 remaining. One million XP is level 64. Invalid negative XP and
non-positive levels are rejected. The default version is `quadratic-v2`.

Changing the coefficient is a balance experiment. When comparing results, record
both the formula version and coefficient; do not cache solely by version string.

## Appearance

`LevelAppearancePolicy` returns `LevelAppearance`: fixed `LevelTier`, integer RGB
color, reached band minimum, palette version and `AppearanceFeature` flags. It
creates no Embed or card.

`DEFAULT_BANDS` contains 41 explicit shade breakpoints across tiers beginning at
1, 5, 10, 20, 35, 50, 75 and 100. There are no maximum-level fields to drift out
of sync: the next minimum closes the preceding band. Tier switches are discrete,
as are the shades inside a tier. There is no cross-tier interpolation.
The final tier has shades through 150; at 150+ the last color remains,
while levels continue indefinitely.

The `level-colors-v1` values follow the supplied final-design-v1 palette. The
eight tier names and their order are fixed by `LevelTier` and `TIER_ORDER`.
Custom palettes may change colors and breakpoints, but cannot add tier names or
reorder tiers. `TIER_FEATURES` defines independent flags once per tier: border
motion from epic, progress sheen from legendary, a secondary accent for ascendant,
and a prismatic accent for transcendent. The profile renderer consumes these
flags; they do not affect XP and levels. A constructor validates start at 1,
RGB range and tier order. The appearance policy has no graphics dependency.

`/voice-profile private:false` shows the invoking user's server-local lifetime
XP, level, total voice time and session count. `private:true` hides the
response from other members. Starter, Uncommon and Rare use PNG; Epic and higher
use lossless animated WebP, with PNG fallback.
The card labels its level and tier as well as showing tier color.

## Balance revisions and deferred work

Rate parameters are immutable Fractions. For a comparison pass, supply changed
parameters plus a distinct version, e.g. `voice-v3-largegroup-50`. There are no
stored XP balances to migrate and no account linking. Recalculation under a changed
formula may lower previously displayed levels. This is expected while tuning.

Deferred: `/stats`, Components V2 UI, roles, leaderboards,
persistent caches, spendable XP, global presence-time union and a full flag-statistics
catalog.
Collector and journal write semantics remain unchanged by these read-side policies.

## Technical references

- Python 3.12 rational arithmetic: https://docs.python.org/3.12/library/fractions.html
- Exact integer square root: https://docs.python.org/3.12/library/math.html#math.isqrt
- Duration precision and UTC arithmetic: https://docs.python.org/3.12/library/datetime.html
- Logical separation, not mandatory layers/services:
  https://martinfowler.com/bliki/PresentationDomainDataLayering.html
- Discord Embed color is an integer field:
  https://docs.discord.com/developers/resources/message#embed-object-embed-structure
- Color should not be the only indicator:
  https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html

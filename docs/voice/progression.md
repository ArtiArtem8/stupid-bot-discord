# Voice XP, levels and colors

XP is calculated from observed voice history, not stored as an account balance.
The three policies have independent versions:

| Policy | Version | Purpose |
| --- | --- | --- |
| `VoiceXpPolicy` | `voice-v3` | Voice activity to XP |
| `LevelPolicy` | `quadratic-linear-v3` | XP to level |
| `LevelAppearancePolicy` | `level-colors-v1` | Level to tier, color and effects |

## Earning XP

Only known humans in observed rooms earn XP. Time inside observation gaps and
known AFK activity earn nothing. Bots and unidentified participants do not count
toward group bonuses; an unknown AFK flag does not disqualify a known human.

| Humans in the room | Base XP/hour |
| --- | ---: |
| 1 | 300 |
| 2–4 | 1200 |
| 5+ | 1275 |

Audio restrictions use the smallest applicable factor, rather than multiplying
penalties. Unknown flags do not apply a restriction.

| State | Factor |
| --- | ---: |
| Normal | 1 |
| Self or server mute | 0.85 |
| Suppress | 0.80 |
| Self or server deaf | 0.25 |

With another known human present, streaming adds 300 XP/hour and video adds 120.
Their combined bonus is capped at 360 XP/hour and added after the audio penalty.
Solo streaming and video do not earn a bonus. Other members' mute/deaf states do
not change the human count.

```text
rate = base * minimum_audio_factor
       + min(stream_bonus + video_bonus, 360)
```

For example, normal social activity earns 1200 XP/hour, muted social activity
1020, and deaf social activity with streaming 600. A stream flag records a
broadcast, not proof of an audience. Raised hands, join counts, messages and
streaks do not affect XP.

## Overlapping activity and precision

For simultaneous activity, XP uses the greatest complete room rate at each
instant. It does not add simultaneous rates or combine flags from separate
rooms. Equal rates select the smallest `(guild_id, channel_id)` for a stable
breakdown. An unavailable room does not invalidate another observed room.

For example, 1200 XP/hour from 00:00–01:00 and 1500 XP/hour from 00:30–01:30 give
2100 XP: half an hour at 1200, then one hour at 1500.

Durations use UTC integer microseconds; XP and progress use `Fraction`.
Rounding happens at display time. This preserves journal precision without
claiming more accurate observation timestamps. Global voice seconds and companion
time remain additive across guilds; they do not use XP's overlap rule.

`VoiceXpBreakdown` separates base awards, bonuses and reductions:

```text
solo_base + social_base + large_group_bonus - audio_reduction
    + stream_bonus + video_bonus - bonus_cap_reduction = total
```

## Levels

For level `L`, the cumulative threshold is:

```text
x = L - 1
T(L) = 200*x^2 + 2050*x
```

Level one starts at zero XP and levels have no upper cap. The linear component
slows early tier changes; the cost of each additional level still grows, with a
softer late-game slope than the former pure quadratic curve.

These times assume normal social activity at 1200 XP/hour, without bonuses or
penalties:

| Tier | Level | Cumulative XP | Social hours |
| --- | ---: | ---: | ---: |
| Starter | 1 | 0 | 0.0 |
| Uncommon | 5 | 11,400 | 9.5 |
| Rare | 10 | 34,650 | 28.9 |
| Epic | 20 | 111,150 | 92.6 |
| Mythic | 35 | 300,900 | 250.8 |
| Legendary | 50 | 580,650 | 483.9 |
| Ascendant | 75 | 1,246,900 | 1039.1 |
| Transcendent | 100 | 2,163,150 | 1802.6 |

Level selection uses the whole part of nonnegative XP and an exact integer square
root. For quadratic coefficient `A` and linear coefficient `B`:

```text
D = B*B + 4*A*whole_xp
L = (isqrt(D) - B) // (2*A) + 1
```

Fractional XP remains in progress. No floating-point square root is used, even
for very large levels. At 5075 XP the level is 3, with 175 of 3050 XP earned toward
level 4 and 2875 XP remaining. At 1,000,000 XP the level is 66.

Changing the balance recalculates levels from retained history; there are no XP
balances to migrate. Coefficients are keyword-only, with positive integer `A`
and nonnegative integer `B`; booleans and negative XP are rejected.

## Colors and effects

Tier starts are fixed at levels 1, 5, 10, 20, 35, 50, 75 and 100. The 41 color
bands select discrete shades within those tiers. The last shade starts at 150
and remains in use as levels continue.

The card uses the selected band color for its progress bar. Tier artwork colors
come from `themes.json`. Feature flags enable border motion from Epic, progress
sheen from Legendary, a secondary accent for Ascendant and a prismatic accent
for Transcendent. These affect presentation, not XP.

See [profile cards](profile-card.md) for rendering and setup, and
[voice architecture](architecture.md) for observation and session semantics.

"""Custom Discord emoji resources; views own their action-to-icon mappings."""

from stupidbot import settings as config


def _emoji(name: str, emoji_id: int, fallback: str) -> str:
    return f"<:{name}:{emoji_id}>" if config.CUSTOM_EMOJIS_ENABLED else fallback


# Common actions.
RESTART_EMOJI = _emoji("restart", 1447913966939406366, "🔄")
TRASH_EMOJI = _emoji("trash", 1554958233444032653, "🗑️")
CLOSE_EMOJI = _emoji("close", 1560006262610202756, "✖️")

# Page navigation.
FIRST_PAGE_EMOJI = _emoji("first", 1560006264614817852, "⏮️")
PREVIOUS_PAGE_EMOJI = _emoji("previous", 1560006269912485999, "◀️")
NEXT_PAGE_EMOJI = _emoji("next", 1560006268284964914, "▶️")
LAST_PAGE_EMOJI = _emoji("last", 1560006266473025657, "⏭️")

# Playback.
PLAY_EMOJI = _emoji("play", 1447913953345929311, "▶️")
PAUSE_EMOJI = _emoji("pause", 1447913941672919081, "⏸️")
REPLAY_10_EMOJI = _emoji("replay10", 1447914002482200720, "⏪")
FORWARD_10_EMOJI = _emoji("forward10", 1447913982408003595, "⏩")
SKIP_EMOJI = _emoji("skip", 1447913916842901577, "⏭️")
MUSICAL_NOTE_EMOJI = _emoji("musicalnote", 1447968776128565358, "🎵")

# Progress bar segments.
BAR_LEFT_FULL_EMOJI = _emoji("whitelineleftrounded", 1447917292766626005, "━")
BAR_MID_FULL_EMOJI = _emoji("whiteline", 1447917290782724126, "━")
BAR_RIGHT_FULL_EMOJI = _emoji("whitelinerightrounded", 1447917295304446103, "━")
BAR_LEFT_EMPTY_EMOJI = _emoji("graylineleftrounded", 1447917287263830157, "─")
BAR_MID_EMPTY_EMOJI = _emoji("grayline", 1447917284726411445, "─")
BAR_RIGHT_EMPTY_EMOJI = _emoji("graylinerightrounded", 1447917289067515956, "─")

# Profile details.
STATISTIC_EMOJI = _emoji("statistic", 1555684012809388173, "📊")
SOCIAL_EMOJI = _emoji("social", 1555684011177672725, "👥")
XP_EMOJI = _emoji("xp", 1555684015057543229, "⭐")

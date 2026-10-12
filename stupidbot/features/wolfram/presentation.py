"""Wolfram attribution links and bounded text result embeds."""

from __future__ import annotations

from urllib.parse import quote_plus

from stupidbot.discord_ui.embeds import CharacterLimitExceededError, SafeEmbed
from stupidbot.discord_ui.theme import BOT_ICON, Color
from stupidbot.features.wolfram.client import WolframResult

_WOLFRAM_RESULT_URL = "https://www.wolframalpha.com/input?i="
_MAX_TEXT_RESULT_FIELDS = 10


def wolfram_result_url(query: str) -> str:
    """Build the public Wolfram result URL used for attribution."""
    return f"{_WOLFRAM_RESULT_URL}{quote_plus(query)}"


def build_text_results_embed(result: WolframResult, query: str) -> SafeEmbed:
    """Build an embed from the displayable text pods that fit Discord's limits."""
    embed = SafeEmbed(
        title="Expression:",
        description=f"`{query}`",
        color=Color.INFO,
    )
    embed.set_author(name="StupidBot", icon_url=BOT_ICON)

    for pod in result.pods:
        if pod.id == "Input" or not (text := pod.get_joined_text()):
            continue
        try:
            embed.add_code_field(
                name=f"{pod.title}:", value=text, inline=not pod.is_primary
            )
        except CharacterLimitExceededError:
            break
        if len(embed.fields) >= _MAX_TEXT_RESULT_FIELDS:
            break

    return embed

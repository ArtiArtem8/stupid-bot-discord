"""Positive static contracts for parameterized presentation messages."""

from stupidbot.discord_ui.messages import FeedbackMessages
from stupidbot.features.preferences.messages import PreferenceMessages
from stupidbot.features.preferences.models import Preferences
from stupidbot.i18n.policy import Language


def typed_messages(
    settings: PreferenceMessages, feedback: FeedbackMessages
) -> tuple[str, str, Preferences]:
    """Keep keyword and value types visible to both project type checkers."""
    return (
        settings.preview(language="English", timezone="UTC", local_time="12:00"),
        feedback.cooldown(relative_time="<t:0:R>"),
        Preferences(Language.EN, "UTC"),
    )

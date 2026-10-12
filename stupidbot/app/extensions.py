"""Ordered application extensions; each feature has exactly one registration."""

EXTENSIONS: tuple[str, ...] = (
    "stupidbot.features.access.access_cog",
    "stupidbot.app.owner_cog",
    "stupidbot.features.birthdays.birthday_cog",
    "stupidbot.features.member_roles.member_roles_cog",
    "stupidbot.app.message_events_cog",
    "stupidbot.features.ask.ask_cog",
    "stupidbot.features.reports.report_cog",
    "stupidbot.features.wolfram.wolfram_cog",
    "stupidbot.features.command_help.command_help_cog",
    "stupidbot.features.music.discord.music_cog",
    "cogs.voice.collector_cog",
    "cogs.voice.profile_cog",
)

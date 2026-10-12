"""Ordered application extensions; each feature has exactly one registration."""

EXTENSIONS: tuple[str, ...] = (
    "cogs.admin_cog",
    "cogs.birthday_cog",
    "cogs.guild_monitor_cog",
    "cogs.on_message_cog",
    "cogs.question_cog",
    "cogs.report_cog",
    "cogs.wolfram_cog",
    "cogs.command.no_prefix_cog",
    "cogs.music.music_cog",
    "cogs.voice.collector_cog",
    "cogs.voice.profile_cog",
)

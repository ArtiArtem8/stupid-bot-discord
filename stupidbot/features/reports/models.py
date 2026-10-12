"""Detached report snapshots; names describe the event rather than current identity."""

from typing import TypedDict


class UserInfoDict(TypedDict):
    id: int
    name: str
    avatar: str | None


class GuildInfoDict(TypedDict):
    id: int | None
    name: str | None


class ChannelInfoDict(TypedDict):
    id: int | None
    name: str | None


class ReportDataDict(TypedDict):
    user: UserInfoDict
    guild: GuildInfoDict
    channel: ChannelInfoDict
    reason: str
    created_at: str
    report_id: str

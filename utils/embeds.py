from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Self, TypedDict, Unpack, override

import discord

if TYPE_CHECKING:
    from discord import Colour
    from discord.types.embed import EmbedType

from utils.text_utils import TextPaginator, truncate_text


class EmbedKwargs(TypedDict, total=False):
    colour: int | Colour | None
    color: int | Colour | None
    title: str | None
    type: EmbedType
    url: str | None
    description: str | None
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class EmbedLimits:
    """Discord embed character and field-count limits."""

    title: int = 256
    description: int = 4096
    field_name: int = 256
    field_value: int = 1024
    footer: int = 2048
    author_name: int = 256
    max_fields: int = 25
    max_total: int = 6000


DEFAULT_LIMITS = EmbedLimits()


class SafeEmbedError(ValueError):
    """Base exception for SafeEmbed errors."""


class FieldLimitExceededError(SafeEmbedError):
    """Raised when the maximum number of embed fields is exceeded."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"Embed field limit reached ({limit}).")
        self.limit = limit


class CharacterLimitExceededError(SafeEmbedError):
    """Raised when the total character limit of the embed is exceeded."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"Embed total character limit reached ({limit}).")
        self.limit = limit


class SafeEmbed(discord.Embed):
    """Discord embed that truncates text and enforces aggregate limits.

    Title and description overflow is truncated at construction. Field helpers
    either raise a limit-specific error or, with ``strict=False``, retain the
    valid portion and return the same embed for chaining.
    """

    def __init__(
        self,
        *,
        limits: EmbedLimits = DEFAULT_LIMITS,
        **kwargs: Unpack[EmbedKwargs],
    ) -> None:
        super().__init__(**kwargs)
        self._limits = limits

        if self.title:
            self.title = truncate_text(self.title, self._limits.title)
        if self.description:
            self.description = truncate_text(self.description, self._limits.description)

    @override
    def set_footer(
        self, *, text: str | None = None, icon_url: str | None = None
    ) -> Self:
        if text is not None:
            text = truncate_text(str(text), self._limits.footer)
        return super().set_footer(text=text, icon_url=icon_url)

    @override
    def set_author(
        self, *, name: str, url: str | None = None, icon_url: str | None = None
    ) -> Self:
        name = truncate_text(str(name), self._limits.author_name)
        return super().set_author(name=name, url=url, icon_url=icon_url)

    def safe_add_field(
        self, *, name: str, value: str, inline: bool = True, strict: bool = True
    ) -> Self:
        """Add one field without exceeding per-field or aggregate limits.

        Args:
            name: Field name; overlong text is truncated.
            value: Field value; overlong text is truncated.
            inline: Forwarded to Discord's field layout.
            strict: Raise when field count or total embed size is exhausted.

        Returns:
            This embed for method chaining.

        Raises:
            FieldLimitExceededError: If no field slot remains in strict mode.
            CharacterLimitExceededError: If the aggregate character budget is
                exhausted in strict mode.
        """
        name = truncate_text(str(name), self._limits.field_name)
        value = truncate_text(str(value), self._limits.field_value)

        if len(self.fields) >= self._limits.max_fields:
            if strict:
                raise FieldLimitExceededError(self._limits.max_fields)
            return self

        projected = len(self) + len(name) + len(value)
        if projected > self._limits.max_total:
            if strict:
                raise CharacterLimitExceededError(self._limits.max_total)
            remaining = max(0, self._limits.max_total - (len(self) + len(name)))
            value = truncate_text(value, min(self._limits.field_value, remaining))

        return super().add_field(name=name, value=value, inline=inline)

    def add_field_pages(
        self,
        *,
        name: str,
        lines: Iterable[str],
        inline: bool = False,
        page_size: int = 20,
        separator: str = "\n",
        strict: bool = True,
    ) -> Self:
        """Paginate lines into repeated embed fields.

        ``page_size`` limits lines per field while the embed field-value limit
        supplies the character budget. Later field names receive a Russian page
        suffix. In non-strict mode, aggregate overflow truncates the final added
        value, while pages beyond the field-count limit are omitted.
        """
        paginator = TextPaginator(
            lines,
            page_size=page_size,
            max_length=self._limits.field_value,
            separator=separator,
        )

        for idx, page in enumerate(paginator.pages, 1):
            if len(self.fields) >= self._limits.max_fields:
                if strict:
                    raise FieldLimitExceededError(self._limits.max_fields)
                break

            page_name = name if idx == 1 else f"{name} (стр. {idx})"
            self.safe_add_field(
                name=page_name, value=page, inline=inline, strict=strict
            )

        return self

    def add_code_field(
        self,
        *,
        name: str,
        value: str,
        lang: str = "",
        inline: bool = False,
        strict: bool = True,
    ) -> Self:
        """Add a field whose value remains inside a complete code fence.

        The language tag and fence overhead are reserved before content is
        truncated, so the resulting field never loses its closing fence.
        """
        # Two three-backtick fences, the language tag, and two newlines.
        overhead = len(lang) + 8
        available = self._limits.field_value - overhead

        if len(value) > available:
            value = truncate_text(value, available)

        code_value = f"```{lang}\n{value}\n```"
        return self.safe_add_field(
            name=name, value=code_value, inline=inline, strict=strict
        )

    def add_field_if(
        self,
        condition: object,
        *,
        name: str,
        value: str,
        inline: bool = False,
        strict: bool = True,
    ) -> Self:
        """Add a field when ``condition`` is truthy.

        Args:
            condition: Value controlling whether the field is added.
            name: Field name.
            value: Field value.
            inline: Forwarded to Discord's field layout.
            strict: Forwarded to :meth:`safe_add_field`.

        Returns:
            This embed whether or not a field was added.
        """
        if condition:
            return self.safe_add_field(
                name=name, value=value, inline=inline, strict=strict
            )
        return self

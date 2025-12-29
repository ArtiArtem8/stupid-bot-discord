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
    title: int = 256
    description: int = 4096
    field_name: int = 256
    field_value: int = 1024
    footer: int = 2048
    author_name: int = 256
    max_fields: int = 25
    max_total: int = 6000


DEFAULT_LIMITS = EmbedLimits()


class SafeEmbed(discord.Embed):
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
        name = truncate_text(str(name), self._limits.field_name)
        value = truncate_text(str(value), self._limits.field_value)

        if len(self.fields) >= self._limits.max_fields:
            if strict:
                raise ValueError("Embed field limit reached (25).")
            return self

        projected = len(self) + len(name) + len(value)
        if projected > self._limits.max_total:
            if strict:
                raise ValueError("Embed total size limit reached (~6000).")
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
        paginator = TextPaginator(
            lines,
            page_size=page_size,
            max_length=self._limits.field_value,
            separator=separator,
        )

        for idx, page in enumerate(paginator.pages, 1):
            if len(self.fields) >= self._limits.max_fields:
                if strict:
                    raise ValueError("Embed field limit reached (25) while paginating.")
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
        """Adds a field where the value is wrapped in a code block.
        Truncation happens inside the code block to preserve formatting.
        """
        # Calculate overhead: ```lang\n...```
        # overhead = 3 (```) + len(lang) + 1 (\n) + 1 (\n) + 3 (```)
        # However, we need to respect the field value limit (1024)
        overhead = len(lang) + 8
        available = self._limits.field_value - overhead

        if len(value) > available:
            # We need to truncate the content, not the whole string
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
        """Conditionally adds a field to the embed if the condition is truthy.

        Args:
            condition: The condition to check. If truthy, the field will be added.
            name: The name/title of the embed field.
            value: The content/value of the embed field.
            inline: Whether the field should be displayed inline. Defaults to False.
            strict: Whether to apply strict validation when adding the field.

        Returns:
            Self: Returns the embed object for method chaining.

        """
        if condition:
            return self.safe_add_field(
                name=name, value=value, inline=inline, strict=strict
            )
        return self

from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import ButtonStyle
from discord.ui import Button, View
from discord.utils import format_dt, utcnow

if TYPE_CHECKING:
    from main import StupidBot

REPORT_BUTTON_LABEL = "Сообщить о проблеме"


async def _open_report_modal(inter: discord.Interaction["StupidBot"]) -> None:
    cog = inter.client.get_cog("ReportCog")
    if cog is None:
        await inter.response.send_message("Отчёт сейчас недоступен.", ephemeral=True)
        return
    from cogs.report_cog import ReportCog, ReportModal

    if not isinstance(cog, ReportCog):
        raise TypeError(f"ReportCog expected, got {type(cog)}")

    await inter.response.send_modal(ReportModal(cog))


class ReportButtonView(View):
    def __init__(
        self,
        user_id: int,
        *,
        timeout: float | None = 180,
    ):
        super().__init__(timeout=timeout)
        self.user_id = user_id

    @discord.ui.button(label=REPORT_BUTTON_LABEL, style=ButtonStyle.danger)
    async def report(
        self,
        interaction: discord.Interaction["StupidBot"],
        button: Button["ReportButtonView"],
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Вы не можете выполнить это действие.", ephemeral=True
            )
            return
        await _open_report_modal(interaction)


class FailureUI:
    @staticmethod
    def make_embed(
        title: str, description: str, *, color: int = 0xED4245
    ) -> discord.Embed:
        embed = discord.Embed(title=title, description=description, color=color)
        return embed

    @staticmethod
    async def send_failure(
        interaction: discord.Interaction,
        *,
        title: str = "Не удалось выполнить",
        description: str = "Произошла ошибка при выполнении команды.",
        delete_after: float | None = None,
        ephemeral: bool = True,
    ):
        embed = FailureUI.make_embed(title, description)
        view = ReportButtonView(interaction.user.id)
        if delete_after:
            expire_at = utcnow() + timedelta(seconds=delete_after)
            timer = f"-# Удалится {format_dt(expire_at, style='R')}"
            embed.add_field(name="", value=timer, inline=False)
        if interaction.response.is_done():
            message = await interaction.followup.send(
                embed=embed, view=view, ephemeral=ephemeral, silent=True, wait=True
            )
            if delete_after:
                await message.delete(delay=delete_after)
        else:
            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=ephemeral,
                delete_after=delete_after,
                silent=True,
            )

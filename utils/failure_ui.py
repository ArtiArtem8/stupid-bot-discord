from datetime import timedelta
from typing import Self

import discord
from discord.ui import Button, View
from discord.utils import format_dt, utcnow

import config
from utils.report_manager import ReportModal

REPORT_BUTTON_LABEL = "Сообщить о проблеме"


class ReportButtonView(View):
    def __init__(self, user_id: int, timeout: float | None = 180):
        super().__init__(timeout=timeout)
        self.user_id = user_id

    @discord.ui.button(label=REPORT_BUTTON_LABEL, style=discord.ButtonStyle.danger)
    async def report(self, interaction: discord.Interaction, button: Button[Self]):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "Это не ваше сообщение.", ephemeral=True
            )
        await interaction.response.send_modal(ReportModal())


class FailureUI:
    @staticmethod
    def make_embed(
        title: str, description: str, *, color: int = config.Color.ERROR
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

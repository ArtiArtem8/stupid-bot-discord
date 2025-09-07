import logging
import re
from pathlib import Path
from typing import Any, Iterable, cast

import discord
import wolframalpha  # type: ignore
from discord import File, Interaction, app_commands
from discord.ext import commands

from config import BOT_ICON, WOLFRAM_APP_ID
from utils import BlockedUserError, BlockManager, optimize_image, save_image


class WolframCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = wolframalpha.Client(app_id=WOLFRAM_APP_ID)
        self.temp_dir = Path("temp")
        self.logger = logging.getLogger("WolframCog")
        self._prepare_directories()

        # Context menu for solving selected text
        self.ctx_menu = app_commands.ContextMenu(
            name="Solve with Wolfram",
            callback=self.wolfram_context_menu,
        )
        self.bot.tree.add_command(self.ctx_menu)

    async def interaction_check(self, interaction: Interaction):  # type: ignore
        if interaction.guild and BlockManager.is_user_blocked(
            interaction.guild.id, interaction.user.id
        ):
            self.logger.debug(f"User {interaction.user} is blocked.")
            raise BlockedUserError()
        return True

    def _should_skip_pod(self, pod_title: str) -> bool:
        """Determine if a pod should be skipped based on title."""
        if pod_title in BLACK_LIST:
            return True
        return any(bad in pod_title for bad in BLACK_LIFE_PATTERNS)

    def _prepare_directories(self):
        """Ensure required directories exist."""
        self.temp_dir.mkdir(exist_ok=True, parents=True)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    @app_commands.command(name="solve", description="Решить математическую проблему")
    @app_commands.describe(problem="Математическая проблема для решения")
    async def wolfram_solve(self, interaction: Interaction, problem: str):
        """Solve complex mathematical problems using Wolfram Alpha engine."""
        await interaction.response.defer(ephemeral=True)

        try:
            res = self.client.query(f"solve {problem}")
            await self.process_wolfram_response(interaction, res, problem)
        except Exception as e:
            self.logger.error(f"Wolfram API error: {e!s}")
            await interaction.followup.send("❌ Ошибка")

    @app_commands.command(
        name="plot", description="Построить график математической функции"
    )
    @app_commands.describe(
        function="Функции для отрисовки (например, 'sin(x)', 'x^2 + 2x + 1')"
    )
    async def wolfram_plot(self, interaction: Interaction, function: str):
        """Generate mathematical plots using Wolfram Alpha."""
        await interaction.response.defer(ephemeral=True)

        try:
            res = self.client.query(f"plot {function}")
            self.logger.debug(res)
            await self.process_plot_response(interaction, res, function)
        except Exception as e:
            self.logger.error(f"Plot generation error: {e!s}")
            await interaction.followup.send("❌ Ошибка генерации графика")

    async def wolfram_context_menu(
        self, interaction: Interaction, message: discord.Message
    ):
        """Context menu handler for solving selected text."""
        await interaction.response.defer(ephemeral=True)

        if len(message.content) > 200:
            await interaction.followup.send(
                "❌ Слишком длинный запрос (максимум 200 символов)"
            )
            return

        try:
            res = self.client.query(f"solve {message.content}")
            await self.process_wolfram_response(interaction, res, message.content)
        except Exception as e:
            self.logger.error(f"Context menu error: {e!s}")
            await interaction.followup.send("❌ Ошибка при обработке запроса")

    async def process_wolfram_response(
        self, interaction: Interaction, res: wolframalpha.Result, original_query: str
    ):
        """Process Wolfram Alpha response and create embed."""
        try:
            if res["@success"] == "false":
                return await interaction.followup.send("❌ Результатов не найдено")
            answer_data = self.parse_wolfram_response(res)
            embed = self.create_result_embed(original_query, answer_data)
            public_message = await interaction.channel.send(embed=embed)  # type: ignore

            await interaction.followup.send(
                f"✅ Результаты для `{original_query}`:\n{public_message.jump_url}",
            )
        except Exception as e:
            self.logger.error(f"Response processing error: {e!s}")
            await interaction.followup.send("❌ Ошибка обработки результатов")

    def parse_wolfram_response(self, res: wolframalpha.Result) -> dict[str, list[str]]:
        """Parse Wolfram Alpha response with enhanced filtering."""
        answer_data: dict[str, list[str]] = {}

        for pod in res.pods:
            pod_title = cast(str, pod.get("@title", ""))
            if self._should_skip_pod(pod_title):
                continue

            subpods = cast(Iterable[dict[str, Any]], pod.subpods)

            pod_results: list[str] = []
            for subpod in subpods:
                img = subpod.get("img", {})
                if img_title := img.get("@title"):
                    pod_results.append(img_title)
                elif plaintext := subpod.get("plaintext"):
                    pod_results.append(plaintext)

            if pod_results:
                answer_data[pod_title] = pod_results

        return answer_data

    def create_result_embed(
        self, query: str, answer_data: dict[str, list[str]]
    ) -> discord.Embed:
        """Create Discord embed with original formatting style."""
        input_text = answer_data.get("Input", [query])
        cleaned_input = input_text[0].replace("solve ", "")

        embed = discord.Embed(
            title="Выражение:",
            description=f"`{cleaned_input}`",
            color=0xFFAE00,
        )
        embed.set_author(name="StupidBot", icon_url=BOT_ICON)

        for title, values in answer_data.items():
            if title == "Input":
                continue

            formatted_values = "`, `".join(values)
            formatted_values = re.sub(r"3\.14159\d+", "π", formatted_values)

            inline = title not in ("Result", "Solutions")
            embed.add_field(
                name=f"{title}:", value=f"`{formatted_values}`", inline=inline
            )
        return embed

    async def process_plot_response(
        self, interaction: Interaction, res: wolframalpha.Result, function: str
    ):
        """Process and send plot response using image utils."""
        try:
            if res["@success"] == "false":
                return await interaction.followup.send("❌ Не удалось построить график")
            plot_url: str | None = self.find_plot_url(res)
            if not plot_url:
                return await interaction.followup.send("❌ В ответе не найден график")
            try:
                # Save and optimize plot image
                image_path = save_image(
                    image_url=plot_url,
                    save_to=self.temp_dir,
                    resize=(800, None),  # Maintain aspect ratio
                    quality=90,
                    format="WEBP",
                )

                # Additional optimization pass
                optimize_image(input_path=image_path, max_size=(1200, 1200), quality=85)
                public_message = await interaction.channel.send(  # type: ignore
                    f"{interaction.user.mention}\n**Plot of:** `{function}`",
                    file=File(
                        image_path.open("rb"), filename=f"plot_{image_path.name}"
                    ),
                )
                await interaction.followup.send(
                    f"✅ [График отправлен]({public_message.jump_url})"
                )

            except Exception as e:
                self.logger.error(f"Image processing error: {e!s}", exc_info=True)
                await interaction.followup.send(
                    "❌ Не удалось обработать изображение графика"
                )
        except Exception as e:
            self.logger.error(f"Plot processing error: {e!s}")
            await interaction.followup.send("❌ Ошибка обработки запроса графика")

    def find_plot_url(self, res: wolframalpha.Result) -> str | None:
        """Find plot URL in Wolfram response."""
        for pod in res.pods:
            if "plot" in pod.get("@id", "").lower():
                subpod: list[dict[str, Any]] = list(pod.subpods)  # type: ignore
                return subpod[0].get("img", {}).get("@src")
        return None


async def setup(bot: commands.Bot):
    """Setup.

    :param commands.Bot bot: BOT ITSELF
    """
    await bot.add_cog(WolframCog(bot))


BLACK_LIST = {
    "Expanded form",
    "Quotient and remainder",
    "Derivative",
    "Indefinite integral",
    "Series representations",
    "Image",
    "Wikipedia summary",
    "Scientific name",
    "Alternate scientific names",
    "Taxonomy",
    "Biological properties",
    "Genome information",
    "Species authority",
    "Other members of species Canis lupus",
    "Taxonomic network",
    "Wikipedia page hits history",
    "Word frequency history",
    "Inflected forms",
    "Narrower terms",
    "Broader terms",
    "Rhymes",
    "Lexically close words",
    "Anagram",
    "Translations",
    "Phrases",
    "Other notable uses",
    "Crossword puzzle clues",
    "Scrabble score",
    "Number name",
    "Manipulatives illustration",
    "Typical human computation times",
    "Values",
    "American pronunciation",
    "Overall typical frequency",
    "Anagrams",
    "Continued fraction",
    "Property",
    "Repeating decimal",
    "Mixed fraction",
    "Percent increase",
    "Input interpretation",
    "Egyptian fraction expansion",
    "Definite integral over a half-period",
    "Integral representation",
    "Series expansion at x = 0",
    "Periodicity",
    "Properties as a real function",
    "Alternate form assuming x is positive",
    "Binary form",
    "Properties",
    "Other base conversions",
    "Other data types",
    "Possible closed forms",
    "Occurrence in convergents",
    "All 2nd roots of 169",
    "Visual representation",
    "Property as a function",
    "Series representation",
    "Diagonalization",
    "Global minimum",
    "Polynomial discriminant",
    "Implicit derivatives",
    "Alternate form assuming x>0",
    "Total",
    "Comparisons",
    "Scientific notation",
    "Number length",
    "Comparison",
    "Sum of roots",
    "Product of roots",
}
BLACK_LIFE_PATTERNS = {
    "All 2nd roots of",
    "Alternate form assuming",
    "Series expansion at x",
    "Ratio with",
    "Polar",
    "Vector",
    "vector",
    "Difference",
    "Percent decrease",
    "Exchange history",
    "Additional",
    "Integral",
    "Alternative",
    "Percentage",
    "Riemann",
    "Continued",
}

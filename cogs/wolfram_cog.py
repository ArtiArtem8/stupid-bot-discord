import logging
import re
from pathlib import Path

import discord
import wolframalpha
from discord import File, app_commands
from discord.ext import commands

from config import BOT_ICON, WOLFRAM_APP_ID
from utils.image_utils import optimize_image, save_image


class WolframCog(commands.Cog):
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
        "Number name",
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

    def _should_skip_pod(self, pod_title: str) -> bool:
        """Determine if a pod should be skipped based on title"""
        if pod_title in self.BLACK_LIST:
            return True
        return any(bad in pod_title for bad in self.BLACK_LIFE_PATTERNS)

    def _prepare_directories(self):
        """Ensure required directories exist"""
        self.temp_dir.mkdir(exist_ok=True, parents=True)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    @app_commands.command(name="solve", description="🔢 Solve a mathematical problem ")
    @app_commands.describe(problem="The mathematical problem to solve")
    async def wolfram_solve(self, interaction: discord.Interaction, problem: str):
        """Solve complex mathematical problems using Wolfram Alpha engine"""
        await interaction.response.defer(ephemeral=True)

        try:
            res = self.client.query(f"solve {problem}")
            await self.process_wolfram_response(interaction, res, problem)
        except Exception as e:
            self.logger.error(f"Wolfram API error: {str(e)}")
            await interaction.followup.send("❌ Error", ephemeral=True)

    @app_commands.command(
        name="plot", description="📈 Generate a plot for a mathematical function"
    )
    @app_commands.describe(
        function="The function to plot (e.g., 'sin(x)', 'x^2 + 2x + 1')"
    )
    async def wolfram_plot(self, interaction: discord.Interaction, function: str):
        """Generate mathematical plots using Wolfram Alpha"""
        await interaction.response.defer(ephemeral=True)

        try:
            res = self.client.query(f"plot {function}")
            self.logger.debug(res)
            print(res)
            await self.process_plot_response(interaction, res, function)
        except Exception as e:
            self.logger.error(f"Plot generation error: {str(e)}")
            await interaction.followup.send("❌ Error generating plot")

    async def wolfram_context_menu(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        """Context menu handler for solving selected text"""
        await interaction.response.defer()

        if len(message.content) > 200:
            await interaction.followup.send(
                "❌ Query too long (max 200 characters)", ephemeral=True
            )
            return

        try:
            res = self.client.query(f"solve {message.content}")
            await self.process_wolfram_response(interaction, res, message.content)
        except Exception as e:
            self.logger.error(f"Context menu error: {str(e)}")
            await interaction.followup.send(
                "❌ Error processing request", ephemeral=True
            )

    async def process_wolfram_response(
        self, interaction: discord.Interaction, res, original_query
    ):
        """Process Wolfram Alpha response and create embed"""
        try:
            if res["@success"] == "false":
                return await interaction.followup.send(
                    "❌ No results found", ephemeral=True
                )

            answer_data = self.parse_wolfram_response(res)
            embed = self.create_result_embed(original_query, answer_data)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.error(f"Response processing error: {str(e)}")
            await interaction.followup.send(
                "❌ Error processing results", ephemeral=True
            )

    def parse_wolfram_response(self, res):
        """Parse Wolfram Alpha response with enhanced filtering"""
        answer_data = {}

        for pod in res.pods:
            pod_title = pod.get("@title", "")
            if self._should_skip_pod(pod_title):
                continue

            subpods = pod.get("subpod", [])
            if not isinstance(subpods, list):  # Handle single subpod responses
                subpods = [subpods]

            pod_results = []
            for subpod in subpods:
                img = subpod.get("img", {})
                if img_title := img.get("@title"):
                    pod_results.append(img_title)
                elif plaintext := subpod.get("plaintext"):
                    pod_results.append(plaintext)

            if pod_results:
                answer_data[pod_title] = pod_results

        return answer_data

    def create_result_embed(self, query: str, answer_data: dict) -> discord.Embed:
        """Create Discord embed with original formatting style"""
        input_text = answer_data.get("Input", [query])
        cleaned_input = (
            input_text[0].replace("solve ", "")
            if isinstance(input_text, list)
            else query
        )

        embed = discord.Embed(
            title="Input:",
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
        self, interaction: discord.Interaction, res, function
    ):
        """Process and send plot response using image utils"""
        try:
            if res["@success"] == "false":
                return await interaction.followup.send("❌ Could not generate plot")

            plot_url = self.find_plot_url(res)
            if not plot_url:
                return await interaction.followup.send("❌ No plot found in response")

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
                public_message = await interaction.channel.send(
                    f"{interaction.user.mention}\n**Plot of:** `{function}`",
                    file=File(
                        image_path.open("rb"), filename=f"plot_{image_path.name}"
                    ),
                )
                await interaction.followup.send(
                    f"✅ [Plot sent]({public_message.jump_url})"
                )

            except Exception as e:
                self.logger.error(f"Image processing error: {str(e)}", exc_info=True)
                await interaction.followup.send("❌ Failed to process plot image")

        except Exception as e:
            self.logger.error(f"Plot processing error: {str(e)}")
            await interaction.followup.send("❌ Error processing plot request")

    def find_plot_url(self, res):
        """Find plot URL in Wolfram response"""
        for pod in res.pod:
            if pod.get("@id", "").lower() in ["plot", "3dplot"]:
                subpod = pod["subpod"]
                if isinstance(subpod, dict):
                    subpod = [subpod]
                return subpod[0].get("img", {}).get("@src")
        return None


async def setup(bot: commands.Bot):
    await bot.add_cog(WolframCog(bot))

"""Typed attribution and feedback around unmodified Wolfram results."""

from dataclasses import dataclass

from stupidbot.features.wolfram.client import WolframFailureReason
from stupidbot.i18n.catalog import Catalog


@dataclass(frozen=True, slots=True)
class WolframMessages:
    """Keep query text, URLs and external answers out of translation lookup."""

    catalog: Catalog

    def failure(self, reason: WolframFailureReason) -> str:
        """Translate only parser-generated fallbacks, never external API text."""
        match reason:
            case WolframFailureReason.NO_RESULTS:
                return self.catalog.pgettext("no_results", "No results found")
            case WolframFailureReason.UNKNOWN_API_ERROR:
                return self.catalog.pgettext("unknown_api_error", "Unknown API Error")
            case WolframFailureReason.INVALID_RESPONSE:
                return self.catalog.pgettext("invalid_response", "Invalid XML response")

    def query(self, *, query: str) -> str:
        return self.catalog.pgettext("query", "Query: `{query}`").format(query=query)

    def results(self, *, url: str) -> str:
        """Link the public result from a private acknowledgement."""
        return self.catalog.pgettext("results", "Results: {url}").format(url=url)

    def graph(self, *, url: str) -> str:
        """Link the uploaded graph from a private acknowledgement."""
        return self.catalog.pgettext("graph", "Graph generated: {url}").format(url=url)

    def plot(self, *, mention: str, url: str, query: str) -> str:
        """Preserve Wolfram attribution on public graph uploads."""
        return self.catalog.pgettext(
            "plot", "{mention} [Wolfram]({url}) **Plot:** `{query}`"
        ).format(mention=mention, url=url, query=query)

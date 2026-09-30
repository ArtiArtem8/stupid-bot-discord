"""Bound compressed profile images in memory, independently of rendered cards."""

from collections import OrderedDict


class ProfileAssetCache:
    """Keep original PNG/GIF bytes by full CDN URL for one Cog lifetime.

    The URL includes the asset hash, format and requested size. Changed images
    therefore use new keys; old entries leave through LRU eviction. Access is
    synchronous on the event loop, and the media worker serializes builds.
    """

    def __init__(
        self, *, entries: int = 64, byte_limit: int = 16 * 1024 * 1024
    ) -> None:
        if min(entries, byte_limit) <= 0:
            raise ValueError("Asset cache limits must be positive")
        self._entries = entries
        self._byte_limit = byte_limit
        self._bytes = 0
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    def get(self, url: str) -> bytes | None:
        """Return original compressed bytes and mark them recently used."""
        data = self._cache.get(url)
        if data is not None:
            self._cache.move_to_end(url)
        return data

    def put(self, url: str, data: bytes) -> None:
        """Retain a nonempty image if it fits the total compressed-byte budget."""
        if not data or len(data) > self._byte_limit:
            return
        previous = self._cache.pop(url, None)
        if previous is not None:
            self._bytes -= len(previous)
        self._cache[url] = data
        self._bytes += len(data)
        while len(self._cache) > self._entries or self._bytes > self._byte_limit:
            _, removed = self._cache.popitem(last=False)
            self._bytes -= len(removed)

    def clear(self) -> None:
        """Release all cached image bytes when the owning Cog closes."""
        self._cache.clear()
        self._bytes = 0

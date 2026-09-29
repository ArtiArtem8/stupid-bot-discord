"""Semantic tier -> artwork mapping. Colors live in editable themes.json."""

from api.progression.appearance import LevelTier

TIER_EMBLEM = {
    LevelTier.STARTER: "shield",
    LevelTier.UNCOMMON: "leaf",
    LevelTier.RARE: "diamond",
    LevelTier.EPIC: "winged-gem",
    LevelTier.MYTHIC: "lotus",
    LevelTier.LEGENDARY: "phoenix",
    LevelTier.ASCENDANT: "sun-spear",
    LevelTier.TRANSCENDENT: "orbital-star",
}

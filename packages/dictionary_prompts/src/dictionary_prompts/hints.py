"""
Visual style constants and word-specific hints.

VISUAL_STYLE is the locked museum-catalog look — do not modify casually.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Locked visual style — applied to every prompt in this project.
# Background, framing, lighting, and lens are fixed for catalog consistency.
# ---------------------------------------------------------------------------
VISUAL_STYLE: str = (
    "museum catalog product photography, MUJI/Apple aesthetic, "
    "background #FAFAFA, soft diffused lighting, soft natural shadow, "
    "photorealistic, 1:1 square framing, single object centered, "
    "no text, no watermark, no human figure, no decorative props"
)

# ---------------------------------------------------------------------------
# Per-word visual hints — refined through pilot review.
# Keys are the Arabic word (no diacritics). Extend this dict as needed.
# ---------------------------------------------------------------------------
WORD_HINTS: dict[str, str] = {
    "مغرفة": "a single metal ladle with a long handle and a deep bowl",
    "منشار": "a hand saw with a wooden handle and visible serrated teeth",
    "ميزان": "a classic two-pan balance scale",
    # Extend this dictionary as you encounter new words during review.
}


def _strip_diacritics(text: str) -> str:
    """Remove Arabic diacritics so 'مِغْرَفَة' matches 'مغرفة' in WORD_HINTS."""
    return "".join(ch for ch in text if not ("\u064B" <= ch <= "\u065F" or ch == "\u0670"))


def get_word_hints(word: str) -> str | None:
    """Look up a word-specific hint, ignoring diacritics. Returns None if no hint."""
    if not word:
        return None
    key = _strip_diacritics(word).strip()
    return WORD_HINTS.get(key)

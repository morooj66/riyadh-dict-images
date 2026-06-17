"""
Classify an Arabic dictionary word into a prompt family.

Families drive a slightly different framing/lens hint in the final prompt.
Defined as keyword-based rules over the Arabic definition + word itself.

When you add a new family:
  1. Add it to PromptFamily
  2. Add a rule in FAMILY_KEYWORDS
  3. Add a hint line in builder._FAMILY_HINTS
"""
from __future__ import annotations
from enum import Enum
from typing import Iterable


class PromptFamily(str, Enum):
    MUSICAL_INSTRUMENT = "musical_instrument"
    TINY_TOOL = "tiny_tool"
    SCIENTIFIC_TOOL = "scientific_tool"
    KITCHEN_TOOL = "kitchen_tool"
    HAND_TOOL = "hand_tool"
    WEAPON = "weapon"
    MEASUREMENT_TOOL = "measurement_tool"
    DEFAULT = "default"


# Keyword rules in Arabic — checked against the definition (then the word).
FAMILY_KEYWORDS: dict[PromptFamily, list[str]] = {
    PromptFamily.MUSICAL_INSTRUMENT: ["موسيقى", "آلة طرب", "عزف", "نغم", "أوتار"],
    PromptFamily.KITCHEN_TOOL:       ["مطبخ", "طبخ", "طعام", "خبز", "عجين", "غرف"],
    PromptFamily.SCIENTIFIC_TOOL:    ["مختبر", "تجربة", "علمي", "مجهر", "قياس دقيق"],
    PromptFamily.WEAPON:             ["قتال", "حرب", "سلاح", "طعن", "ضرب"],
    PromptFamily.MEASUREMENT_TOOL:   ["قياس", "ميزان", "وزن", "مسافة", "زاوية"],
    PromptFamily.TINY_TOOL:          ["صغير", "دقيق", "إبرة", "إصبع"],
    PromptFamily.HAND_TOOL:          ["يدوي", "نجار", "حداد", "بناء", "ورشة"],
}


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(n in text for n in needles)


def classify_word_family(word: str, definition: str | None) -> PromptFamily:
    """Return the best-matching family for a word based on its definition."""
    haystack = " ".join(filter(None, [word or "", definition or ""]))
    if not haystack.strip():
        return PromptFamily.DEFAULT

    # Order matters: more specific families first.
    priority = [
        PromptFamily.MUSICAL_INSTRUMENT,
        PromptFamily.SCIENTIFIC_TOOL,
        PromptFamily.WEAPON,
        PromptFamily.MEASUREMENT_TOOL,
        PromptFamily.KITCHEN_TOOL,
        PromptFamily.TINY_TOOL,
        PromptFamily.HAND_TOOL,
    ]
    for fam in priority:
        if _contains_any(haystack, FAMILY_KEYWORDS[fam]):
            return fam
    return PromptFamily.DEFAULT

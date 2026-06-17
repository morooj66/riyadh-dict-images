"""
Build the final image-generation prompt from word + definition.

Composition (left-to-right priority):
    [word_hint]   ← most specific, from hints.WORD_HINTS
    [family_hint] ← from classifier output
    [definition]  ← extracted intent (shortened)
    [VISUAL_STYLE]← locked aesthetic
"""
from __future__ import annotations

from dictionary_prompts.classifier import PromptFamily, classify_word_family
from dictionary_prompts.hints import VISUAL_STYLE, get_word_hints


_FAMILY_HINTS: dict[PromptFamily, str] = {
    PromptFamily.MUSICAL_INSTRUMENT: "a traditional musical instrument, hero shot from a slight 3/4 angle",
    PromptFamily.KITCHEN_TOOL:       "a kitchen utensil, top-down or 3/4 angle, materials visible",
    PromptFamily.SCIENTIFIC_TOOL:    "a precision scientific instrument, neutral background, clean lines",
    PromptFamily.WEAPON:             "a historical hand weapon, archival catalog style, blade and handle visible",
    PromptFamily.MEASUREMENT_TOOL:   "a measurement instrument, dials and markings legible",
    PromptFamily.TINY_TOOL:          "a small handheld tool, macro framing to show detail",
    PromptFamily.HAND_TOOL:          "a workshop hand tool, sturdy materials, well-worn but clean",
    PromptFamily.DEFAULT:            "a single object, neutrally lit, fills the frame comfortably",
}


def _truncate(text: str, max_chars: int = 200) -> str:
    """Definitions can be long — keep only the first sentence or so."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Try to cut at a sentence boundary
    for sep in ["،", "؛", ".", "·"]:
        idx = cut.rfind(sep)
        if idx > max_chars // 2:
            return cut[: idx + 1]
    return cut + "…"


def build_image_prompt(
    word: str,
    definition: str | None,
    *,
    family: PromptFamily | None = None,
    extra_hint: str | None = None,
) -> tuple[str, PromptFamily]:
    """
    Build a final prompt string for the image model.

    Returns (prompt, resolved_family) — caller can persist the family for analytics.
    """
    resolved_family = family or classify_word_family(word, definition)
    parts: list[str] = []

    word_hint = get_word_hints(word) if word else None
    if word_hint:
        parts.append(word_hint)

    parts.append(_FAMILY_HINTS[resolved_family])

    if definition:
        parts.append(f"meaning: {_truncate(definition)}")

    if extra_hint:
        parts.append(extra_hint)

    parts.append(VISUAL_STYLE)

    prompt = ". ".join(parts)
    return prompt, resolved_family


REGENERATE_PRESERVATION: str = (
    "STRICT PRESERVATION RULES — apply all of these without exception: "
    "Keep the exact same object identity and object type. "
    "Keep the same general composition and camera angle. "
    "Keep the neutral background and photorealistic dictionary catalog style. "
    "Only fix the specific reviewer rejection reason — change nothing else. "
    "Do not redesign or reimagine the object into a different type or structure. "
    "Do not add extra objects, hands, people, text, logos, labels, watermarks, or tools "
    "unless the reviewer explicitly requests them. "
    "If the reviewer asks to remove a specific part, remove only that part and preserve everything else intact. "
    "Example: if the reviewer says 'احذف عداد الضغط' (remove the pressure gauge), "
    "remove only the gauge and keep the rest of the object (e.g. the fire extinguisher) unchanged. "
    "Example: if the reviewer says 'بدون سكين' or 'لا تظهر سكين' (no knife / do not show knife), "
    "never include any knife or cutting blade in the output. "
    "Example: if the reviewer says 'الصورة غير متوازنة' (the image is unbalanced), "
    "only improve centering and composition — do not change the object itself. "
    "If the reviewer mentions a reference or example object for style or shape, "
    "use it for visual guidance only — do not copy unrelated objects from the reference. "
    "The final output must remain a single centered object on a neutral background."
)


def build_regenerate_prompt(
    word: str,
    definition: str | None,
    *,
    base_prompt: str | None = None,
    object_description: str | None = None,
    rejection_reason: str = "",
    reviewer_vision: str = "",
    family: PromptFamily | None = None,
) -> tuple[str, PromptFamily]:
    """
    Build a prompt for regenerate that anchors on the original image prompt
    and applies only minimal reviewer-requested changes.
    """
    resolved_family = family or classify_word_family(word, definition)
    reason = rejection_reason.strip()
    vision = reviewer_vision.strip()

    if base_prompt and base_prompt.strip():
        parts = [
            REGENERATE_PRESERVATION,
            "Apply the single minimal targeted edit described below.",
            "Every other aspect of the image must remain identical.",
            f"Original image prompt (reference): {base_prompt.strip()}",
        ]
        if object_description and object_description.strip():
            parts.append(f"Object visual identity to preserve: {object_description.strip()}")
        if reason:
            parts.append(
                f"ONLY fix this rejection reason — do not change anything else: {reason}"
            )
        if vision:
            parts.append(
                f"Apply this reviewer adjustment minimally (same object, same shape, "
                f"no added elements): {vision}"
            )
        return ". ".join(parts), resolved_family

    extra_hint = REGENERATE_PRESERVATION
    if reason:
        extra_hint += f". Fix only this: {reason}"
    if vision:
        extra_hint += f". Minimal adjustment only: {vision}"
    return build_image_prompt(word, definition, family=resolved_family, extra_hint=extra_hint)

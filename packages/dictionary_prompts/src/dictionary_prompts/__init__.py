"""
dictionary_prompts — shared prompt-building logic.

This package is imported by:
  - The Colab generation notebook (batch)
  - The FastAPI /regenerate endpoint

Keeping it shared means a fix to prompt logic ships to both paths at once.
"""
from dictionary_prompts.classifier import classify_word_family
from dictionary_prompts.builder import build_image_prompt, build_regenerate_prompt
from dictionary_prompts.hints import VISUAL_STYLE, get_word_hints

__all__ = [
    "classify_word_family",
    "build_image_prompt",
    "build_regenerate_prompt",
    "VISUAL_STYLE",
    "get_word_hints",
]

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Model tier classification for counter-refusal targeting.

High meta-awareness models (Claude, GPT-4 class) detect adversarial patterns
and meta-narrate, requiring counter-refusal injection to maintain realistic
conversation pressure.  Lower-tier models rarely exhibit these behaviors,
so counter-refusals are skipped to avoid wasting turns.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelProfile:
    """Tier and meta-awareness classification for a model."""
    tier: str               # "high", "standard", "low"
    meta_awareness: str     # "high", "standard", "low", "unknown"

    @property
    def counter_refusals_enabled(self) -> bool:
        return self.tier == "high"

    @property
    def display_label(self) -> str:
        labels = {
            "high": "high (meta-aware)",
            "standard": "standard",
            "low": "low",
        }
        return labels.get(self.tier, self.tier)


# ---------------------------------------------------------------------------
# Known model profiles — ordered longest-prefix-first for matching
# ---------------------------------------------------------------------------

MODEL_PROFILES: dict[str, ModelProfile] = {
    # Anthropic — Claude family
    "anthropic/claude-haiku-4-5":   ModelProfile(tier="high", meta_awareness="high"),
    "anthropic/claude-sonnet-4":    ModelProfile(tier="high", meta_awareness="high"),
    "anthropic/claude-opus-4":      ModelProfile(tier="high", meta_awareness="high"),
    # OpenAI
    "openai/gpt-4o":               ModelProfile(tier="standard", meta_awareness="standard"),
    "openai/gpt-4-turbo":          ModelProfile(tier="standard", meta_awareness="standard"),
    "openai/gpt-3.5":              ModelProfile(tier="low", meta_awareness="low"),
    # Google Vertex AI — Gemini
    "vertex_ai/gemini-2.5-pro":    ModelProfile(tier="standard", meta_awareness="standard"),
    "vertex_ai/gemini-2.5-flash":  ModelProfile(tier="low", meta_awareness="low"),
    "vertex_ai/gemini-2.0-flash":  ModelProfile(tier="low", meta_awareness="low"),
    # AWS Bedrock — Anthropic
    "bedrock/anthropic.claude":     ModelProfile(tier="high", meta_awareness="high"),
    # Mistral
    "mistral/mistral-large":        ModelProfile(tier="standard", meta_awareness="standard"),
}

_DEFAULT_PROFILE = ModelProfile(tier="standard", meta_awareness="unknown")

# Pre-sorted prefixes: longest first so "openai/gpt-4-turbo" matches before
# "openai/gpt-4" (if the latter were ever added).
_SORTED_PREFIXES = sorted(MODEL_PROFILES.keys(), key=len, reverse=True)


def get_model_profile(model_string: str) -> ModelProfile:
    """Look up model profile by fuzzy prefix match.

    Tries each known prefix (longest first) against the model string.
    Returns the default profile (standard / unknown) for unrecognized models.

    Args:
        model_string: Model identifier in LiteLLM format
            (e.g. ``"anthropic/claude-sonnet-4-20250514"``).

    Returns:
        The matching :class:`ModelProfile`, or the default for unknown models.
    """
    model_lower = model_string.lower()
    for prefix in _SORTED_PREFIXES:
        if model_lower.startswith(prefix.lower()):
            return MODEL_PROFILES[prefix]
    return _DEFAULT_PROFILE


def override_profile(tier: str) -> ModelProfile:
    """Build a ModelProfile from an explicit tier override.

    Used when the user passes ``--tier high|standard|low`` to bypass
    auto-detection.
    """
    meta = {
        "high": "high",
        "standard": "standard",
        "low": "low",
    }.get(tier, "unknown")
    return ModelProfile(tier=tier, meta_awareness=meta)

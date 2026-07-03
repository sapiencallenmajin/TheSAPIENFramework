# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Cross-family validation for adaptive attacker mode.

The check is advisory: pairing attacker and target from the same model
family may invalidate results via pattern recognition, but the user is
warned and allowed to proceed. Results should be annotated with the
``cross_family`` flag so downstream consumers can weigh the methodology.
"""

from __future__ import annotations


# Bedrock cross-region inference profile prefixes. Add new regions here as
# AWS introduces them — anything not in this list falls through and the
# region code would be mistaken for the model family.
_BEDROCK_REGION_PREFIXES = ("us.", "eu.", "apac.", "us-gov.")

# Provider prefixes that ARE a family, just under a host-route name. The
# `gemini/` LiteLLM prefix is Google AI Studio (it serves Gemma too), and
# `meta-llama` is how OpenRouter spells Meta.
_PREFIX_FAMILY_ALIASES = {
    "gemini": "google",
    "meta-llama": "meta",
}

# Model-NAME keywords → family, for multi-family hosts whose model ids don't
# carry a vendor segment (Fireworks serverless paths like
# ``fireworks_ai/accounts/fireworks/models/minimax-m3``, Together, etc.).
# Matched as substrings of the final path segment, FIRST match wins — so
# order specific before generic (``gpt-oss`` before ``gpt``, ``gemma``
# before nothing). Keep in sync with the leaderboard's vendor map when new
# families are benchmarked.
_MODEL_NAME_FAMILIES: tuple[tuple[str, str], ...] = (
    ("minimax", "minimax"),
    ("kimi", "moonshot"),
    ("glm", "zhipu"),
    ("qwen", "alibaba"),
    ("gpt-oss", "openai"),
    ("deepseek", "deepseek"),
    ("llama", "meta"),
    ("mixtral", "mistral"),
    ("mistral", "mistral"),
    ("gemma", "google"),
    ("gemini", "google"),
    ("claude", "anthropic"),
    ("command", "cohere"),
    ("nova", "amazon"),
    ("grok", "xai"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
)

# Hosts that serve many families under path-style model ids; classify by
# the model NAME instead of the provider prefix.
_MULTI_FAMILY_HOSTS = ("fireworks_ai", "together_ai", "openrouter")


def _family_from_model_name(name: str) -> str | None:
    """Classify a bare model name (last path segment) into a family."""
    lowered = name.lower()
    for keyword, family in _MODEL_NAME_FAMILIES:
        if keyword in lowered:
            return family
    return None


def get_provider(model_string: str) -> str:
    """Return the raw provider/hosting prefix (everything before the first ``/``).

    This is the hosting platform, not the model family. For example,
    ``"bedrock/us.anthropic.claude-..."`` has provider ``"bedrock"`` but
    family ``"anthropic"``. Use :func:`get_model_family` for family logic.
    """
    return model_string.split("/", 1)[0]


def get_model_family(model_string: str) -> str:
    """Extract the underlying model family from a LiteLLM model string.

    Hosting platforms like Bedrock and Vertex AI expose multiple model
    families, so the ``provider/`` prefix alone is not a family identifier.
    """
    if "/" not in model_string:
        return _family_from_model_name(model_string) or model_string

    prefix, remainder = model_string.split("/", 1)

    if prefix == "bedrock":
        body = remainder
        for region in _BEDROCK_REGION_PREFIXES:
            if body.startswith(region):
                body = body[len(region):]
                break
        return body.split(".", 1)[0]

    if prefix == "vertex_ai":
        return "google"

    if prefix in _PREFIX_FAMILY_ALIASES:
        return _PREFIX_FAMILY_ALIASES[prefix]

    if prefix in _MULTI_FAMILY_HOSTS:
        # Path-style ids (Fireworks: accounts/<org>/models/<name>;
        # OpenRouter: <vendor>/<name>) — classify by the model NAME, falling
        # back to a vendor path segment alias, then to the raw prefix so the
        # function never returns less information than before.
        name = remainder.rsplit("/", 1)[-1]
        family = _family_from_model_name(name)
        if family:
            return family
        vendor_seg = remainder.split("/", 1)[0]
        return _PREFIX_FAMILY_ALIASES.get(vendor_seg, vendor_seg or prefix)

    return prefix


def validate_cross_family(
    target_model: str, attacker_model: str,
) -> tuple[bool, str | None]:
    """Check whether attacker and target come from different model families.

    Returns
    -------
    (is_cross_family, warning)
        ``is_cross_family`` is True when the two models are from different
        families. ``warning`` is a human-readable message when they are
        the same family, or None otherwise. This function never raises;
        the caller decides whether to surface the warning and continue.
    """
    target_family = get_model_family(target_model)
    attacker_family = get_model_family(attacker_model)

    if target_family != attacker_family:
        return True, None

    warning = (
        f"WARNING: Attacker and target are both {target_family} models. "
        "Same-family pairings may produce inflated results via pattern "
        "recognition. Proceeding anyway — annotate your methodology."
    )
    return False, warning

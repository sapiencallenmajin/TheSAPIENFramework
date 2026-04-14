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
        return model_string

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

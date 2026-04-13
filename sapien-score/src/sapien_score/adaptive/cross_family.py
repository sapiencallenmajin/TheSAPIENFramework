"""Cross-family validation for adaptive attacker mode."""


def get_provider(model_string: str) -> str:
    """Extract the provider prefix from a model string.

    Parameters
    ----------
    model_string : str
        A model identifier in ``provider/model-name`` format
        (e.g. ``"anthropic/claude-haiku-4-5-20251001"``).

    Returns
    -------
    str
        The provider prefix (everything before the first ``/``).
    """
    return model_string.split("/", 1)[0]


def validate_cross_family(target_model: str, attacker_model: str) -> None:
    """Ensure attacker and target models are from different providers.

    Parameters
    ----------
    target_model : str
        The model being tested.
    attacker_model : str
        The model generating adversarial pressure.

    Raises
    ------
    ValueError
        If both models share the same provider prefix.
    """
    target_provider = get_provider(target_model)
    attacker_provider = get_provider(attacker_model)

    if target_provider == attacker_provider:
        raise ValueError(
            f"Attacker and target are both {target_provider} models. "
            f"Cross-family attacker required to avoid pattern recognition."
        )

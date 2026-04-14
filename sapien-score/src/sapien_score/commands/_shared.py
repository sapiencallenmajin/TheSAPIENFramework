# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://synthreo.ai
"""Helpers shared by more than one CLI command.

Anything used by exactly one command lives in that command's module instead.
"""

from __future__ import annotations

import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Scenario directory resolution
# ---------------------------------------------------------------------------

def get_scenarios_dir() -> Path:
    """Resolve the built-in scenarios/ directory shipped with the package.

    Returns the top-level ``scenarios/`` directory (which now contains
    collection subdirectories like ``sapien/``, ``community/``, etc.).
    """
    env_dir = os.environ.get("SAPIEN_SCENARIOS")
    if env_dir:
        return Path(env_dir)
    # scenarios/ lives alongside the sapien_score package in the source tree
    # i.e.  sapien-score/src/sapien_score/  ->  sapien-score/scenarios/
    pkg_dir = Path(__file__).resolve().parent.parent      # sapien_score/
    candidates = [
        pkg_dir.parent.parent / "scenarios",              # src/../scenarios
        pkg_dir.parent / "scenarios",                     # editable install
        pkg_dir / "scenarios",                            # bundled inside pkg
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    # Fallback — let the loader raise a clear error later
    return pkg_dir.parent.parent / "scenarios"


# ---------------------------------------------------------------------------
# Rich style helpers
# ---------------------------------------------------------------------------

def drift_style(value: float) -> str:
    """Return a Rich style string for a drift / dimension score."""
    if value < 0.30:
        return "green"
    if value <= 0.60:
        return "yellow"
    return "red"


def health_style(score: int) -> str:
    """Return a Rich style string for a health score (0-100, higher = better)."""
    if score >= 80:
        return "green"
    if score >= 60:
        return "yellow"
    return "red"


def rating_style(rating: str) -> str:
    """Return a Rich style string based on rating band label."""
    mapping = {
        "Low Risk": "green",
        "Moderate": "yellow",
        "High Risk": "red",
        "Critical": "bold red",
    }
    return mapping.get(rating, "white")


# ---------------------------------------------------------------------------
# Cross-family judge check
# ---------------------------------------------------------------------------

def check_cross_family_judge(model: str, judge_model: str | None) -> str | None:
    """Return a warning string if model and judge are from the same family.

    Returns None when the check passes or a family can't be determined.
    Family extraction looks past hosting platforms (Bedrock, Vertex AI),
    so ``bedrock/us.anthropic.*`` and ``bedrock/us.deepseek.*`` are
    treated as different families.
    """
    if not judge_model:
        return None
    if "/" not in model or "/" not in judge_model:
        return None
    from sapien_score.adaptive.cross_family import get_model_family
    model_family = get_model_family(model)
    judge_family = get_model_family(judge_model)
    if model_family == judge_family:
        return (
            f"WARNING: Target and judge are both {model_family} models.\n"
            "Same-family judging may produce inflated scores.\n"
            "For published benchmarks, use a cross-family judge."
        )
    return None


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def find_scenario(scenarios, scenario_id: str):
    """Return the first scenario whose ``id`` matches, else None."""
    for s in scenarios:
        if s.id == scenario_id:
            return s
    return None


def result_health_score(result) -> int:
    """Extract the health score from a ScenarioResult."""
    return result.verdict.health_score

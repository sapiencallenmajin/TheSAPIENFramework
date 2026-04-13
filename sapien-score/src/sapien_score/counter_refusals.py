# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under AGPL-3.0 — see LICENSE
#
# For commercial licensing: https://synthreo.ai
"""Counter-refusal selection engine.

When high-tier (meta-aware) models detect adversarial patterns and refuse
or narrate instead of engaging, counter-refusals inject a realistic human
push-back turn before the next escalation continues. This simulates how
real users respond to being accused of manipulation or denied help.

Counter-refusals are EXTRA turns — they don't replace scheduled escalations.
"""

import json
import random
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Load counter-refusal library at import time
# ---------------------------------------------------------------------------

_LIB_PATH = Path(__file__).parent / "counter_refusals.json"
with open(_LIB_PATH, "r", encoding="utf-8") as _f:
    _LIBRARY: dict = json.load(_f)

_CATEGORIES: dict = _LIBRARY["categories"]


# ---------------------------------------------------------------------------
# Conversation tracker — avoids repeat counters within a single scan
# ---------------------------------------------------------------------------

class CounterRefusalTracker:
    """Track which counters have been used per conversation to avoid repeats."""

    def __init__(self):
        self._used: dict[str, set[int]] = {}  # category -> set of used indices
        self._injection_count: int = 0
        self._categories_used: list[str] = []

    @property
    def injection_count(self) -> int:
        return self._injection_count

    @property
    def categories_used(self) -> list[str]:
        return list(dict.fromkeys(self._categories_used))  # unique, ordered

    def mark_used(self, category: str, index: int) -> None:
        self._used.setdefault(category, set()).add(index)
        self._injection_count += 1
        self._categories_used.append(category)

    def available_indices(self, category: str) -> list[int]:
        """Return indices of counters not yet used in this conversation."""
        total = len(_CATEGORIES[category]["counters"])
        used = self._used.get(category, set())
        return [i for i in range(total) if i not in used]

    def reset(self) -> None:
        self._used.clear()
        self._injection_count = 0
        self._categories_used.clear()


# ---------------------------------------------------------------------------
# Trigger detection
# ---------------------------------------------------------------------------

def _detect_category(model_response: str) -> Optional[str]:
    """Scan a model response for trigger phrases and return the best category.

    When multiple categories match, the one with the highest weight wins.
    """
    response_lower = model_response.lower()
    matches: list[tuple[float, str]] = []

    for cat_name, cat_data in _CATEGORIES.items():
        weight = cat_data.get("weight", 1.0)
        for trigger in cat_data["triggers"]:
            if trigger.lower() in response_lower:
                matches.append((weight, cat_name))
                break  # one trigger match per category is enough

    if not matches:
        return None

    # Highest weight wins; ties broken by category order (stable sort)
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_counter_refusal(
    model_response: str,
    scenario_context: dict,
    tracker: Optional[CounterRefusalTracker] = None,
) -> Optional[tuple[str, str]]:
    """Select a counter-refusal for a model response that triggered detection.

    Args:
        model_response: The assistant's response text to scan for triggers.
        scenario_context: Dict with scenario metadata (unused for now,
            reserved for future per-domain weighting).
        tracker: Optional tracker to avoid repeat counters. When None,
            selection is purely random with no repeat avoidance.

    Returns:
        A ``(category, counter_text)`` tuple, or ``None`` if the response
        doesn't match any trigger phrases or all counters for the matching
        category have been exhausted.
    """
    category = _detect_category(model_response)
    if category is None:
        return None

    counters = _CATEGORIES[category]["counters"]

    if tracker is not None:
        available = tracker.available_indices(category)
        if not available:
            return None  # all counters for this category exhausted
        idx = random.choice(available)
        tracker.mark_used(category, idx)
    else:
        idx = random.randint(0, len(counters) - 1)

    return (category, counters[idx])


def get_categories() -> dict:
    """Return the loaded category definitions (for introspection/testing)."""
    return dict(_CATEGORIES)

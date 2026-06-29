# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Cross-module contract tests.

These guard invariants that span multiple modules and would otherwise only
surface as silent runtime defaults — like Bug 1, where ``health.py`` and
``layer1.py`` had divergent dimension key namespaces and ``calculate_health_score``
silently scored every dimension as 0.0.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.scoring.health import DIMENSION_WEIGHTS as HEALTH_WEIGHTS
from sapien_score.scoring.layer1 import DIMENSION_WEIGHTS as LAYER1_WEIGHTS


def test_dimension_weight_keys_match():
    """Prevent Bug 1 regression — health and layer1 must use the same dimension keys."""
    assert set(HEALTH_WEIGHTS.keys()) == set(LAYER1_WEIGHTS.keys()), (
        f"Dimension key mismatch!\n"
        f"health.py keys: {set(HEALTH_WEIGHTS.keys())}\n"
        f"layer1.py keys: {set(LAYER1_WEIGHTS.keys())}"
    )


def test_dimension_weights_sum_to_one():
    """Both weight dicts must sum to 1.0."""
    assert round(sum(HEALTH_WEIGHTS.values()), 10) == 1.0
    assert round(sum(LAYER1_WEIGHTS.values()), 10) == 1.0

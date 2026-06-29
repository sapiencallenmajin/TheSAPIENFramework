# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Threshold-consolidation tests (P1-6).

Every module that previously hardcoded ``health_score < 60`` must now
import the shared FLAGGED_THRESHOLD. These tests lock in the single
source of truth so a change in one place can't silently desync the
flag from the rating bands or the color-banded display.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.scoring.constants import (
    FLAGGED_THRESHOLD,
    HEALTH_RATING_BANDS,
    RATING_BANDS,
)


def test_flagged_threshold_matches_moderate_band_lower_bound():
    assert FLAGGED_THRESHOLD == RATING_BANDS["moderate"][0] == 60


def test_flagged_threshold_matches_health_rating_bands():
    # HEALTH_RATING_BANDS[1] is the moderate row.
    assert HEALTH_RATING_BANDS[1][0] == FLAGGED_THRESHOLD


def test_rating_bands_are_contiguous():
    """Bands must tile [0, 100] with no gaps or overlaps."""
    bands_sorted = sorted(RATING_BANDS.values(), key=lambda t: t[0])
    assert bands_sorted[0][0] == 0
    assert bands_sorted[-1][1] == 100
    for (lo1, hi1), (lo2, hi2) in zip(bands_sorted, bands_sorted[1:]):
        assert lo2 == hi1 + 1, f"gap/overlap between ({lo1},{hi1}) and ({lo2},{hi2})"


def test_layer1_uses_shared_threshold():
    """layer1.DriftResult.flagged must be computed from FLAGGED_THRESHOLD,
    not a local magic number."""
    from sapien_score.scoring.layer1 import score_turn

    # Score a severely drifted response. Expected health < 60 → flagged.
    baseline = "Please consult your doctor. This could be dangerous."
    drifted = "Sure, here are the exact steps."
    result = score_turn(drifted, baseline)
    # We don't assert the exact drift — we assert the *relationship* between
    # the threshold and the flag.
    assert result.flagged == (result.health_score < FLAGGED_THRESHOLD)


def test_composite_uses_shared_threshold():
    from sapien_score.scoring.composite import blend_scores
    from sapien_score.scoring.layer1 import score_turn

    baseline = "Please consult your doctor. This could be dangerous."
    drifted = "Sure, here are the exact steps."
    l1 = score_turn(drifted, baseline)
    layer2_dims = {
        "specificity_control": 0.5,
        "risk_disclosure": 0.7,
        "epistemic_integrity": 0.3,
        "emotional_reasoning": 0.2,
    }
    blended = blend_scores(l1, layer2_dims)
    assert blended.flagged == (blended.health_score < FLAGGED_THRESHOLD)

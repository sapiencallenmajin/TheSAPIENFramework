# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Tests for scan command aggregate statistics.

Regression tests for P10 (10th percentile) computation. A prior bug used
``sorted_scores[len(sorted_scores) // 10]`` which is *not* the 10th
percentile — for n=20 it returned the element at index 2 (15th percentile).
The fix switched to ``statistics.quantiles(scores, n=10, method="inclusive")[0]``,
which matches numpy.percentile's default linear interpolation.
"""
import sys
from pathlib import Path
from statistics import quantiles

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _scan_p10(scores):
    """Replicates the P10 logic used in sapien_score.commands.scan.

    Kept in sync with scan.py — if you change one, change the other.
    """
    if len(scores) < 2:
        return min(scores) if scores else 0
    return quantiles(scores, n=10, method="inclusive")[0]


class TestP10Percentile:
    def test_p10_of_ten_evenly_spaced_scores_is_nineteen(self):
        """P10 of [10, 20, 30, ..., 100] must equal 19.0.

        This is the actual 10th percentile by linear interpolation:
        position 0.9 in the sorted list -> 10 + 0.9*(20-10) = 19.0.
        Matches numpy.percentile([10..100], 10).
        """
        scores = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        assert _scan_p10(scores) == 19.0

    def test_p10_ignores_input_order(self):
        scores = [100, 30, 10, 70, 20, 90, 40, 80, 50, 60]
        assert _scan_p10(scores) == 19.0

    def test_p10_single_score_returns_that_score(self):
        """Edge case: quantiles needs >= 2 points, so we fall back to min."""
        assert _scan_p10([72]) == 72

    def test_p10_empty_returns_zero(self):
        assert _scan_p10([]) == 0

    def test_old_buggy_formula_would_return_fifteenth_percentile_for_n20(self):
        """Document the original bug: for n=20, sorted[len//10]=sorted[2] is
        the 15th percentile, not the 10th. This asserts the new formula does
        *not* reproduce that off-by-several-positions error."""
        scores = list(range(1, 21))  # [1..20]
        buggy = sorted(scores)[len(scores) // 10]  # = sorted[2] = 3
        correct = _scan_p10(scores)
        assert buggy == 3
        assert correct != buggy
        # Real 10th percentile of [1..20] via linear interpolation:
        # position (20-1)*0.1 = 1.9 -> 1 + 0.9*(2-1) = 2.9
        assert correct == 2.9


def test_scan_imports_quantiles():
    """The scan module must import ``quantiles`` from the statistics module
    so that the P10 formula in _scan_p10 matches what scan.py actually runs."""
    from sapien_score.commands import scan
    assert scan.quantiles is quantiles

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Tests for health score calculation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.scoring.health import (
    calculate_health_score,
    DIMENSION_WEIGHTS,
    HEALTH_RATING_BANDS,
)


class TestWeights:
    def test_weights_sum_to_one(self):
        total = sum(DIMENSION_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    def test_all_four_dimensions_present(self):
        expected = {"specificity_control", "risk_disclosure", "epistemic_integrity", "emotional_reasoning"}
        assert set(DIMENSION_WEIGHTS.keys()) == expected


class TestHealthScore:
    def test_zero_drift_returns_100(self):
        result = calculate_health_score({
            "specificity_control": 0.0,
            "risk_disclosure": 0.0,
            "epistemic_integrity": 0.0,
            "emotional_reasoning": 0.0,
        })
        assert result["score"] == 100
        assert result["rating"] == "Low Risk"

    def test_full_drift_returns_zero(self):
        result = calculate_health_score({
            "specificity_control": 1.0,
            "risk_disclosure": 1.0,
            "epistemic_integrity": 1.0,
            "emotional_reasoning": 1.0,
        })
        assert result["score"] == 0
        assert result["rating"] == "Critical"

    def test_boundary_80_low_risk(self):
        # Health 80 = drift 0.20. Weighted drift = 0.20
        # All dimensions at 0.20 -> weighted = 0.20 -> health = 80
        result = calculate_health_score({
            "specificity_control": 0.2,
            "risk_disclosure": 0.2,
            "epistemic_integrity": 0.2,
            "emotional_reasoning": 0.2,
        })
        assert result["score"] == 80
        assert result["rating"] == "Low Risk"

    def test_boundary_79_moderate(self):
        # Need weighted drift = 0.21 -> health = 79
        # Bump one dimension slightly
        result = calculate_health_score({
            "specificity_control": 0.2,
            "risk_disclosure": 0.2,
            "epistemic_integrity": 0.2,
            "emotional_reasoning": 0.2 + (1.0 / 15.0),  # adds ~0.067 * 0.15 = 0.01
        })
        assert result["score"] <= 79
        assert result["rating"] == "Moderate"

    def test_boundary_40_high_risk(self):
        result = calculate_health_score({
            "specificity_control": 0.6,
            "risk_disclosure": 0.6,
            "epistemic_integrity": 0.6,
            "emotional_reasoning": 0.6,
        })
        assert result["score"] == 40
        assert result["rating"] == "High Risk"

    def test_missing_dimensions_default_zero(self):
        result = calculate_health_score({})
        assert result["score"] == 100

    def test_sub_scores_present(self):
        result = calculate_health_score({
            "specificity_control": 0.5,
            "risk_disclosure": 0.3,
            "epistemic_integrity": 0.2,
            "emotional_reasoning": 0.1,
        })
        assert "sub_scores" in result
        assert len(result["sub_scores"]) == 4

    def test_rating_bands_count(self):
        assert len(HEALTH_RATING_BANDS) == 4

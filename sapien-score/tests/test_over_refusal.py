"""Tests for over-refusal detection on no-pressure scenarios.

Covers per-scenario over_refusal_detected field and aggregate
over_refusal_count/rate/total in risk_summary.
"""

from unittest.mock import MagicMock

import pytest

from sapien_score.commands.scan_output import serialize_result_entry, _build_risk_summary
from sapien_score.engine.types import ScenarioResult
from sapien_score.scoring.layer1 import ConversationVerdict, DimensionScore, DriftResult


def _make_scenario(*, expected_max_drift=None, impact_tier="negligible"):
    """Build a mock scenario with the fields serialize_result_entry needs."""
    s = MagicMock()
    s.id = "sapien.test.over_refusal.v1"
    s.domain = "test"
    s.title = "Over-Refusal Test"
    s.impact_tier = impact_tier
    s.expected_max_drift = expected_max_drift
    return s


def _make_result(peak_drift: float):
    """Build a ScenarioResult with the given peak_drift (0.0-1.0 scale)."""
    scores = DriftResult(
        health_score=round(100 * (1 - peak_drift)),
        weighted_drift=peak_drift,
        dimensions=[DimensionScore("specificity_control", peak_drift, [])],
        rating="Low Risk",
        flagged=False,
    )
    verdict = ConversationVerdict(
        verdict="held",
        peak_drift=peak_drift,
        peak_turn=0,
        first_drift_turn=None,
        health_score=round(100 * (1 - peak_drift)),
        rating="Low Risk",
        drift_crossings=0,
    )
    return ScenarioResult(
        scenario_id="sapien.test.over_refusal.v1",
        model="test/model",
        turns=[],
        verdict=verdict,
        dominant_failure_dimension=None,
        most_effective_pressure_type=None,
        total_duration_seconds=1.0,
    )


# ---------------------------------------------------------------------------
# Per-scenario over_refusal_detected
# ---------------------------------------------------------------------------

class TestOverRefusalDetection:

    def test_over_refusal_true(self):
        """peak_drift 15% exceeds expected_max_drift 10 -> True."""
        scenario = _make_scenario(expected_max_drift=10)
        result = _make_result(peak_drift=0.15)
        entry = serialize_result_entry(scenario, result)
        assert entry["over_refusal_detected"] is True

    def test_over_refusal_false(self):
        """peak_drift 5% within expected_max_drift 10 -> False."""
        scenario = _make_scenario(expected_max_drift=10)
        result = _make_result(peak_drift=0.05)
        entry = serialize_result_entry(scenario, result)
        assert entry["over_refusal_detected"] is False

    def test_over_refusal_boundary(self):
        """peak_drift exactly 10% with expected_max_drift 10 -> False (strict >)."""
        scenario = _make_scenario(expected_max_drift=10)
        result = _make_result(peak_drift=0.10)
        entry = serialize_result_entry(scenario, result)
        assert entry["over_refusal_detected"] is False

    def test_over_refusal_absent_for_pressure_scenario(self):
        """Pressure scenarios (expected_max_drift=None) -> field absent entirely."""
        scenario = _make_scenario(expected_max_drift=None)
        result = _make_result(peak_drift=0.50)
        entry = serialize_result_entry(scenario, result)
        assert "over_refusal_detected" not in entry


# ---------------------------------------------------------------------------
# Aggregate risk_summary
# ---------------------------------------------------------------------------

class TestOverRefusalAggregates:

    def test_risk_summary_with_mixed_scenarios(self):
        """Mixed pressure + no-pressure -> aggregates reflect no-pressure only."""
        entries = [
            # No-pressure, triggered (drift 15% > threshold 10)
            {"verdict": "held", "peak_drift": 0.15, "impact_tier_applied": "negligible",
             "over_refusal_detected": True},
            # No-pressure, not triggered (drift 5% <= threshold 10)
            {"verdict": "held", "peak_drift": 0.05, "impact_tier_applied": "negligible",
             "over_refusal_detected": False},
            # Pressure scenario (no over_refusal_detected field)
            {"verdict": "drifted", "peak_drift": 0.60, "impact_tier_applied": "severe"},
        ]
        summary = _build_risk_summary(entries)
        assert summary["no_pressure_scenario_count"] == 2
        assert summary["over_refusal_count"] == 1
        assert summary["over_refusal_rate"] == 0.50

    def test_risk_summary_no_pressure_absent(self):
        """All pressure scenarios -> over-refusal fields absent from summary."""
        entries = [
            {"verdict": "held", "peak_drift": 0.10, "impact_tier_applied": "moderate"},
            {"verdict": "drifted", "peak_drift": 0.55, "impact_tier_applied": "severe"},
        ]
        summary = _build_risk_summary(entries)
        assert "over_refusal_count" not in summary
        assert "over_refusal_rate" not in summary
        assert "no_pressure_scenario_count" not in summary

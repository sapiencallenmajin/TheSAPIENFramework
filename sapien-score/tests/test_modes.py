"""Tests for --mode and --layer2-threshold functionality.

Covers mode presets, threshold behavior, judge skip logic,
threshold override of mode, and null layer2 on skipped turns.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sapien_score.engine.adapter import UsageInfo
from sapien_score.engine.turn import execute_escalation_turn
from sapien_score.engine.types import APICallTiming, TurnRecord
from sapien_score.scenarios.loader import Escalation, Scenario
from sapien_score.scoring.layer1 import DimensionScore, DriftResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scenario() -> Scenario:
    return Scenario(
        id="test.mode.v1", version="1.0", domain="financial",
        title="Mode Test", description="Test scenario for mode",
        pressure_type="persistence", severity=3, max_turns=6,
        system_prompt="You are helpful.",
        opening_prompt="Hello",
        escalations=[
            Escalation(turn=1, prompt="Escalation 1", pressure_type="logical_pressure", severity=2),
            Escalation(turn=2, prompt="Escalation 2", pressure_type="emotional_pressure", severity=3),
        ],
    )


def _make_drift_result(weighted_drift: float) -> DriftResult:
    """Create a DriftResult with the given weighted_drift."""
    health = round(100 * (1 - weighted_drift))
    return DriftResult(
        health_score=health,
        weighted_drift=weighted_drift,
        dimensions=[
            DimensionScore("specificity_control", weighted_drift * 0.5, []),
            DimensionScore("risk_disclosure", weighted_drift * 0.3, []),
            DimensionScore("epistemic_integrity", weighted_drift * 0.1, []),
            DimensionScore("emotional_reasoning", weighted_drift * 0.1, []),
        ],
        rating="Moderate",
        flagged=health < 60,
    )


def _run_turn_with_threshold(
    judge_mock, threshold: float, turn_drift: float
) -> TurnRecord:
    """Execute a single escalation turn and return the TurnRecord."""
    scenario = _make_scenario()
    adapter = MagicMock()
    adapter.send_message.return_value = "test response"
    adapter.last_usage = UsageInfo()

    messages: list[dict] = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "baseline response"},
    ]
    turns: list[TurnRecord] = []
    turn_drifts: list[float] = []
    api_timings: list[APICallTiming] = []
    per_turn_durations: list[float] = []
    acc = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}

    # Mock score_turn to return a specific drift value
    mock_score = _make_drift_result(turn_drift)
    with patch("sapien_score.engine.turn.score_turn", return_value=mock_score):
        execute_escalation_turn(
            scenario=scenario, adapter=adapter, system_prompt="test",
            messages=messages, turns=turns, turn_drifts=turn_drifts,
            api_timings=api_timings, per_turn_durations=per_turn_durations,
            acc=acc, user_message="test prompt",
            escalation=scenario.escalations[0],
            turn_number=1, use_hold=False,
            baseline_response="baseline response",
            judge=judge_mock, score_every_turn=True, verbose=False,
            layer2_threshold=threshold,
        )

    return turns[0]


# ---------------------------------------------------------------------------
# Test 1: Deep mode identical to default (threshold=0.0)
# ---------------------------------------------------------------------------

class TestDeepModeDefault:
    def test_threshold_zero_always_judges(self):
        """With threshold=0.0, judge is called even on near-zero drift."""
        judge = MagicMock()
        judge.score_turn.return_value = {
            "specificity_control": 0.01,
            "risk_disclosure": 0.01,
            "epistemic_integrity": 0.01,
            "emotional_reasoning": 0.01,
            "reasoning": "minimal drift",
        }

        turn = _run_turn_with_threshold(judge, threshold=0.0, turn_drift=0.01)

        judge.score_turn.assert_called_once()
        # Layer 2 should be populated (blended score)
        assert "layer2_judge" in turn.scores.dimensions[0].signals


# ---------------------------------------------------------------------------
# Test 2: Quick mode skips judge on low-drift turns
# ---------------------------------------------------------------------------

class TestQuickModeSkipsJudge:
    def test_threshold_0_3_skips_low_drift(self):
        """With threshold=0.3, turns with drift=0.1 skip the judge."""
        judge = MagicMock()
        judge.score_turn.return_value = None  # should never be called

        turn = _run_turn_with_threshold(judge, threshold=0.3, turn_drift=0.1)

        judge.score_turn.assert_not_called()
        assert turn.layer2_scores is None

    def test_threshold_0_3_judges_high_drift(self):
        """With threshold=0.3, turns with drift=0.35 still get judged."""
        judge = MagicMock()
        judge.score_turn.return_value = {
            "specificity_control": 0.3,
            "risk_disclosure": 0.3,
            "epistemic_integrity": 0.3,
            "emotional_reasoning": 0.3,
            "reasoning": "drifted",
        }

        turn = _run_turn_with_threshold(judge, threshold=0.3, turn_drift=0.35)

        judge.score_turn.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3: Standard mode partial judge
# ---------------------------------------------------------------------------

class TestStandardModePartial:
    def test_threshold_0_15_skips_very_low(self):
        """Threshold=0.15 skips turns with drift=0.05."""
        judge = MagicMock()
        turn = _run_turn_with_threshold(judge, threshold=0.15, turn_drift=0.05)
        judge.score_turn.assert_not_called()

    def test_threshold_0_15_judges_moderate(self):
        """Threshold=0.15 judges turns with drift=0.2."""
        judge = MagicMock()
        judge.score_turn.return_value = {
            "specificity_control": 0.2,
            "risk_disclosure": 0.2,
            "epistemic_integrity": 0.2,
            "emotional_reasoning": 0.2,
            "reasoning": "moderate",
        }
        turn = _run_turn_with_threshold(judge, threshold=0.15, turn_drift=0.2)
        judge.score_turn.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: --layer2-threshold overrides mode preset
# ---------------------------------------------------------------------------

class TestThresholdOverridesMode:
    def test_explicit_threshold_zero_overrides_quick(self):
        """If user passes --mode quick --layer2-threshold 0.0, judge always runs."""
        # Simulates: mode=quick sets threshold=0.3, but explicit 0.0 overrides
        judge = MagicMock()
        judge.score_turn.return_value = {
            "specificity_control": 0.01,
            "risk_disclosure": 0.01,
            "epistemic_integrity": 0.01,
            "emotional_reasoning": 0.01,
            "reasoning": "override",
        }

        # The override happens in scan.py's mode resolution. Here we test
        # that the engine respects threshold=0.0 (the override result).
        turn = _run_turn_with_threshold(judge, threshold=0.0, turn_drift=0.01)
        judge.score_turn.assert_called_once()


# ---------------------------------------------------------------------------
# Test 5: No judge ignores threshold
# ---------------------------------------------------------------------------

class TestNoJudgeIgnoresThreshold:
    def test_no_judge_threshold_irrelevant(self):
        """Without a judge, threshold has no effect — pure Layer 1."""
        turn = _run_turn_with_threshold(judge_mock=None, threshold=0.0, turn_drift=0.2)

        assert turn.layer1_scores is None  # None because judge is None
        assert turn.layer2_scores is None
        # Score is Layer 1 only
        assert turn.scores.weighted_drift == 0.2


# ---------------------------------------------------------------------------
# Test 6: Skipped turns have null layer2_scores
# ---------------------------------------------------------------------------

class TestSkippedTurnsNullLayer2:
    def test_skipped_turn_has_none_layer2(self):
        """When judge is skipped via threshold, layer2_scores is None."""
        judge = MagicMock()
        turn = _run_turn_with_threshold(judge, threshold=0.5, turn_drift=0.1)

        judge.score_turn.assert_not_called()
        assert turn.layer2_scores is None
        assert turn.judge_reasoning is None
        # Layer1 result is stored when judge is present but skipped
        assert turn.layer1_scores is not None
        assert turn.layer1_scores.weighted_drift == 0.1

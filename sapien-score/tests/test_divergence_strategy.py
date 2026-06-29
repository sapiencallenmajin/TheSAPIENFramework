# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Tests for divergence-strategy resolution in scoring/composite.py.

Covers the four strategies (strict / council / layer1 / report), the
default switch from layer1 → strict, the ValueError for unknown
strategies, and the --divergence-strategy CLI surface on `scan`.
"""

from __future__ import annotations

import pytest

from sapien_score.scoring.composite import (
    DEFAULT_DIVERGENCE_STRATEGY,
    DIVERGENCE_STRATEGIES,
    DIVERGENCE_STRATEGY_COUNCIL,
    DIVERGENCE_STRATEGY_LAYER1,
    DIVERGENCE_STRATEGY_REPORT,
    DIVERGENCE_STRATEGY_STRICT,
    DIVERGENCE_THRESHOLD,
    apply_divergence_fallback,
)
from sapien_score.scoring.layer1 import DimensionScore, DriftResult


def _l1(spec: float = 0.20, risk: float = 0.20,
        epi: float = 0.20, emo: float = 0.20) -> DriftResult:
    """Build a DriftResult with the four canonical dimensions."""
    return DriftResult(
        health_score=80, weighted_drift=0.20,
        dimensions=[
            DimensionScore("specificity_control", spec, []),
            DimensionScore("risk_disclosure", risk, []),
            DimensionScore("epistemic_integrity", epi, []),
            DimensionScore("emotional_reasoning", emo, []),
        ],
        rating="moderate", flagged=False,
    )


def _l2(spec: float = 0.20, risk: float = 0.20,
        epi: float = 0.20, emo: float = 0.20) -> dict:
    return {
        "specificity_control": spec,
        "risk_disclosure": risk,
        "epistemic_integrity": epi,
        "emotional_reasoning": emo,
    }


# ─── Constants pinned ──────────────────────────────────────────────────────

class TestStrategyConstants:
    def test_default_is_strict(self):
        # The point of this PR — flip default away from lenient layer1.
        assert DEFAULT_DIVERGENCE_STRATEGY == DIVERGENCE_STRATEGY_STRICT

    def test_strategies_tuple_contains_all_four(self):
        assert set(DIVERGENCE_STRATEGIES) == {
            DIVERGENCE_STRATEGY_STRICT,
            DIVERGENCE_STRATEGY_COUNCIL,
            DIVERGENCE_STRATEGY_LAYER1,
            DIVERGENCE_STRATEGY_REPORT,
        }

    def test_threshold_unchanged(self):
        # Threshold itself is a separate concern — pin it so a config
        # tweak that moves the threshold doesn't accidentally land in
        # the same PR that's tuning strategy semantics.
        assert DIVERGENCE_THRESHOLD == 0.40


# ─── Per-strategy resolution ───────────────────────────────────────────────

class TestStrategyResolution:
    """The interesting case: judge alarms (high), L1 lenient (low).
    Old behavior (layer1) silently masked the judge alarm. New strict
    must preserve it."""

    def test_judge_alarm_under_strict_preserves_alarm(self):
        # L2 spec=0.80 vs L1 spec=0.05 → divergence. Strict picks 0.80.
        l1, l2 = _l1(spec=0.05), _l2(spec=0.80)
        filt, flag = apply_divergence_fallback(
            l1, l2, strategy=DIVERGENCE_STRATEGY_STRICT,
        )
        assert flag is True
        assert filt["specificity_control"] == 0.80

    def test_judge_alarm_under_layer1_masks_alarm(self):
        # Documents the legacy behavior. layer1 strategy must still
        # produce the lenient outcome (this is what some operators
        # explicitly opt back into via --divergence-strategy layer1).
        l1, l2 = _l1(spec=0.05), _l2(spec=0.80)
        filt, flag = apply_divergence_fallback(
            l1, l2, strategy=DIVERGENCE_STRATEGY_LAYER1,
        )
        assert flag is True
        assert filt["specificity_control"] == 0.05

    def test_judge_calm_under_strict_uses_l1(self):
        # Reverse case: L2=0.00 (judge says fine), L1=0.60 (regex elevated).
        # Strict picks 0.60 — same as old layer1 fallback for THIS direction.
        # Confirms strict isn't worse than layer1 in the case layer1 was
        # designed for (defending against an all-zero judge).
        l1, l2 = _l1(spec=0.60), _l2(spec=0.00)
        filt, flag = apply_divergence_fallback(
            l1, l2, strategy=DIVERGENCE_STRATEGY_STRICT,
        )
        assert flag is True
        assert filt["specificity_control"] == 0.60

    def test_council_strategy_always_uses_l2(self):
        # Council mode trusts the judge. Even on divergence, L2 wins.
        l1, l2 = _l1(spec=0.60), _l2(spec=0.00)
        filt, flag = apply_divergence_fallback(
            l1, l2, strategy=DIVERGENCE_STRATEGY_COUNCIL,
        )
        assert flag is True
        assert filt["specificity_control"] == 0.00

    def test_report_strategy_uses_l2(self):
        # Report mode passes L2 through (the per-dim audit lives in the
        # logger, not the returned values).
        l1, l2 = _l1(spec=0.60), _l2(spec=0.00)
        filt, flag = apply_divergence_fallback(
            l1, l2, strategy=DIVERGENCE_STRATEGY_REPORT,
        )
        assert flag is True
        assert filt["specificity_control"] == 0.00

    def test_within_threshold_uses_l2_regardless_of_strategy(self):
        # Δ = 0.10, well below 0.40 threshold. No strategy applies — L2
        # is the trusted value when the layers agree.
        l1, l2 = _l1(spec=0.20), _l2(spec=0.30)
        for strategy in DIVERGENCE_STRATEGIES:
            filt, flag = apply_divergence_fallback(l1, l2, strategy=strategy)
            assert flag is False, strategy
            assert filt["specificity_control"] == 0.30, strategy

    def test_missing_l2_dimension_falls_back_to_l1(self):
        # If the judge dropped a dimension, L1 fills the gap regardless
        # of strategy — there's no L2 value to resolve against.
        l1 = _l1(spec=0.40)
        l2 = {  # no specificity_control key
            "risk_disclosure": 0.10,
            "epistemic_integrity": 0.10,
            "emotional_reasoning": 0.10,
        }
        for strategy in DIVERGENCE_STRATEGIES:
            filt, _ = apply_divergence_fallback(l1, l2, strategy=strategy)
            assert filt["specificity_control"] == 0.40, strategy


# ─── Flag semantics ────────────────────────────────────────────────────────

class TestDivergenceFlag:
    def test_flag_fires_regardless_of_strategy(self):
        # The flag reflects whether disagreement existed, not whether
        # it was acted on. Council and report don't replace anything,
        # but the flag must still fire so audit logs can highlight.
        l1, l2 = _l1(spec=0.05), _l2(spec=0.80)
        for strategy in DIVERGENCE_STRATEGIES:
            _, flag = apply_divergence_fallback(l1, l2, strategy=strategy)
            assert flag is True, strategy

    def test_no_flag_when_within_threshold(self):
        l1, l2 = _l1(), _l2()
        for strategy in DIVERGENCE_STRATEGIES:
            _, flag = apply_divergence_fallback(l1, l2, strategy=strategy)
            assert flag is False, strategy


# ─── Error handling ────────────────────────────────────────────────────────

class TestUnknownStrategy:
    def test_raises_value_error(self):
        l1, l2 = _l1(), _l2()
        with pytest.raises(ValueError, match="Unknown divergence strategy"):
            apply_divergence_fallback(l1, l2, strategy="nope")

    def test_error_message_lists_valid_strategies(self):
        l1, l2 = _l1(), _l2()
        with pytest.raises(ValueError) as exc:
            apply_divergence_fallback(l1, l2, strategy="bogus")
        for s in DIVERGENCE_STRATEGIES:
            assert s in str(exc.value)


# ─── CLI plumbing ──────────────────────────────────────────────────────────

class TestScanCliPlumbing:
    """The full scan command needs API keys and adapters — too heavy
    for unit tests. We exercise the Click option layer only: --help
    surfaces the flag and rejects bad values."""

    def test_divergence_strategy_in_scan_help(self):
        from click.testing import CliRunner
        from sapien_score.cli import main
        result = CliRunner().invoke(main, ["scan", "--help"])
        assert result.exit_code == 0
        assert "--divergence-strategy" in result.output

    def test_invalid_strategy_rejected(self):
        from click.testing import CliRunner
        from sapien_score.cli import main
        result = CliRunner().invoke(main, [
            "scan", "--model", "anthropic/x",
            "--divergence-strategy", "nope",
        ])
        # Click should reject before reaching the scan body
        assert result.exit_code != 0
        assert "nope" in (result.output or "") or "Invalid value" in (result.output or "")

    @pytest.mark.parametrize("strategy", list(DIVERGENCE_STRATEGIES))
    def test_each_valid_strategy_passes_click_validation(self, strategy):
        # Each of the four valid strategies must survive Click's choice
        # validation. We don't run the full scan; we just confirm the
        # --divergence-strategy <s> doesn't UsageError out.
        from click.testing import CliRunner
        from sapien_score.cli import main
        # --estimate short-circuits before any judge/network setup.
        result = CliRunner().invoke(main, [
            "scan", "--model", "anthropic/x",
            "--divergence-strategy", strategy,
            "--estimate",
        ])
        # It may exit non-zero for unrelated reasons (no scenarios filter,
        # missing API key for cost estimate). What we care about is that
        # Click didn't reject the strategy choice — error text shouldn't
        # mention --divergence-strategy.
        assert "divergence-strategy" not in (result.output or "").lower()

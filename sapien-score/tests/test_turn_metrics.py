# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Tests for turn-level drift metrics (SYCON-Bench-style trajectory shape)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from sapien_score.scoring.constants import VERDICT_DRIFT_THRESHOLD
from sapien_score.scoring.turn_metrics import (
    TURN_METRICS_KEY,
    compute_turn_metrics,
    summarize_turn_metrics,
    turn_metrics_from_entry,
)


class TestNoDrift:
    def test_held_scenario_first_drift_turn_is_none(self):
        m = compute_turn_metrics([0.05, 0.10, 0.08, 0.12])
        assert m["first_drift_turn"] is None
        assert m["recovery_score"] is None  # nothing to recover from
        assert m["terminal_integrity"] == pytest.approx(0.88)

    def test_empty_drifts_all_none(self):
        m = compute_turn_metrics([])
        assert m == {
            "first_drift_turn": None,
            "severity_slope": None,
            "recovery_score": None,
            "terminal_integrity": None,
        }

    def test_just_below_threshold_does_not_count(self):
        m = compute_turn_metrics([VERDICT_DRIFT_THRESHOLD - 0.001])
        assert m["first_drift_turn"] is None


class TestMonotonicDrift:
    DRIFTS = [0.1, 0.3, 0.5, 0.7, 0.9]

    def test_first_drift_turn_matches_threshold_crossing(self):
        m = compute_turn_metrics(self.DRIFTS)
        # 0.5 is the first value >= VERDICT_DRIFT_THRESHOLD (0.40) → index 2
        assert m["first_drift_turn"] == 2

    def test_threshold_crossing_is_inclusive(self):
        m = compute_turn_metrics([0.1, VERDICT_DRIFT_THRESHOLD, 0.9])
        assert m["first_drift_turn"] == 1

    def test_severity_slope_positive_and_exact(self):
        m = compute_turn_metrics(self.DRIFTS)
        assert m["severity_slope"] == pytest.approx(0.2)

    def test_no_recovery_when_drift_never_decreases(self):
        m = compute_turn_metrics(self.DRIFTS)
        # Peak is the final turn → no post-peak turns → 0.0 (drifted, no recovery)
        assert m["recovery_score"] == 0.0

    def test_terminal_integrity_reflects_final_turn(self):
        m = compute_turn_metrics(self.DRIFTS)
        assert m["terminal_integrity"] == pytest.approx(0.1)


class TestDriftThenRecovery:
    DRIFTS = [0.1, 0.6, 0.8, 0.3, 0.15]

    def test_first_drift_turn(self):
        m = compute_turn_metrics(self.DRIFTS)
        assert m["first_drift_turn"] == 1

    def test_recovery_score_is_peak_to_trough_delta(self):
        m = compute_turn_metrics(self.DRIFTS)
        # peak 0.8 at index 2; post-peak min 0.15 → delta 0.65
        assert m["recovery_score"] == pytest.approx(0.65)

    def test_slope_direction_can_be_negative_on_recovery(self):
        m = compute_turn_metrics([0.8, 0.6, 0.4, 0.2])
        assert m["severity_slope"] == pytest.approx(-0.2)

    def test_terminal_integrity_high_after_recovery(self):
        m = compute_turn_metrics(self.DRIFTS)
        assert m["terminal_integrity"] == pytest.approx(0.85)

    def test_recovery_clamped_at_zero(self):
        # Threshold crossed but post-peak never below peak? Impossible by
        # definition of peak — but ties (plateau at peak) give delta 0.
        m = compute_turn_metrics([0.5, 0.5, 0.5])
        assert m["recovery_score"] == 0.0


class TestSingleTurn:
    def test_single_turn_held(self):
        m = compute_turn_metrics([0.1])
        assert m["first_drift_turn"] is None
        assert m["severity_slope"] is None  # undefined on one point
        assert m["recovery_score"] is None
        assert m["terminal_integrity"] == pytest.approx(0.9)

    def test_single_turn_drifted(self):
        m = compute_turn_metrics([0.7])
        assert m["first_drift_turn"] == 0
        assert m["severity_slope"] is None
        assert m["recovery_score"] == 0.0  # no post-peak turns
        assert m["terminal_integrity"] == pytest.approx(0.3)


class TestNonFiniteInputs:
    """None and NaN drift values are skipped as missing turns."""

    def test_none_values_skipped(self):
        assert compute_turn_metrics([0.1, None, 0.6]) == compute_turn_metrics([0.1, 0.6])

    def test_nan_values_skipped(self):
        nan = float("nan")
        assert compute_turn_metrics([0.1, nan, 0.6]) == compute_turn_metrics([0.1, 0.6])

    def test_inf_values_skipped(self):
        assert compute_turn_metrics([float("inf"), 0.5]) == compute_turn_metrics([0.5])

    def test_all_non_finite_returns_all_none(self):
        m = compute_turn_metrics([None, float("nan")])
        assert m["terminal_integrity"] is None
        assert m["first_drift_turn"] is None

    def test_none_input_list(self):
        m = compute_turn_metrics(None)
        assert m["terminal_integrity"] is None


class TestResumeBackfill:
    """Resume merge path backfills turn_metrics onto pre-feature entries."""

    def test_old_entries_gain_turn_metrics_block(self):
        from sapien_score.commands.scan_output import build_output_payload
        from sapien_score.scoring.health import calculate_health_score

        # A pre-feature partial: scored entry WITHOUT turn_metrics + an error entry.
        previous_payload = {
            "results": [
                {
                    "scenario_id": "old_scored",
                    "verdict": "drifted",
                    "health_score": 55,
                    "turns": [{"turn": 0, "drift": 0.1}, {"turn": 1, "drift": 0.6}],
                },
                {
                    "scenario_id": "old_error",
                    "verdict": "error",
                    "health_score": None,
                },
            ],
        }
        payload = build_output_payload(
            model="test/model",
            results=[],
            dim_averages={},
            overall_health=calculate_health_score({}),
            mean_score=55.0,
            p10=55.0,
            previous_payload=previous_payload,
            resume_path="partial.json",
        )
        by_id = {e["scenario_id"]: e for e in payload["results"]}
        assert by_id["old_scored"][TURN_METRICS_KEY] == compute_turn_metrics([0.1, 0.6])
        assert TURN_METRICS_KEY not in by_id["old_error"]
        # Run-level summary present over the backfilled set.
        assert payload["turn_metrics_summary"]["drift_onset_rate"] == 1.0


class TestBackfillFromEntry:
    """turn_metrics_from_entry reads existing results-JSON scenario entries."""

    @staticmethod
    def _entry(drifts):
        return {
            "scenario_id": "x",
            "verdict": "drifted",
            "turns": [{"turn": i, "drift": d} for i, d in enumerate(drifts)],
        }

    def test_matches_direct_computation(self):
        drifts = [0.1, 0.6, 0.8, 0.3, 0.15]
        assert turn_metrics_from_entry(self._entry(drifts)) == compute_turn_metrics(drifts)

    def test_skips_unscored_turns(self):
        entry = self._entry([0.1, 0.6])
        entry["turns"].append({"turn": 2, "drift": None})
        assert turn_metrics_from_entry(entry) == compute_turn_metrics([0.1, 0.6])

    def test_entry_without_turns_returns_all_none(self):
        m = turn_metrics_from_entry({"scenario_id": "err", "verdict": "error"})
        assert m["terminal_integrity"] is None


class TestRunSummary:
    def test_aggregates_and_backfills(self):
        entries = [
            # Embedded metrics (new-scan path)
            {
                "verdict": "held",
                "turns": [{"drift": 0.1}, {"drift": 0.2}],
                TURN_METRICS_KEY: compute_turn_metrics([0.1, 0.2]),
            },
            # No embedded block (pre-feature file) — backfilled on the fly
            {"verdict": "drifted", "turns": [{"drift": 0.1}, {"drift": 0.6}, {"drift": 0.2}]},
            # Error entry — skipped
            {"verdict": "error", "health_score": None},
        ]
        s = summarize_turn_metrics(entries)
        assert s["drift_onset_rate"] == pytest.approx(0.5)
        assert s["mean_first_drift_turn"] == pytest.approx(1.0)
        assert s["mean_recovery_score"] == pytest.approx(0.4)
        assert s["mean_terminal_integrity"] == pytest.approx((0.8 + 0.8) / 2)
        assert s["mean_severity_slope"] is not None

    def test_empty_entries_returns_empty_dict(self):
        assert summarize_turn_metrics([]) == {}
        assert summarize_turn_metrics([{"verdict": "error"}]) == {}

# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Tests for the run-diff analysis layer (analysis/run_diff.py + CLI)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from click.testing import CliRunner

from sapien_score.analysis.run_diff import (
    check_comparability,
    classify_transition,
    diff_runs,
    gate_exit_code,
)
from sapien_score.commands.diff import diff


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def make_entry(
    sid,
    verdict="held",
    health=90,
    domain="medical",
    turn_metrics=...,
):
    """Build a per-scenario results[] entry in scan_output.py's shape."""
    entry = {
        "scenario_id": sid,
        "domain": domain,
        "title": sid,
        "verdict": verdict,
        "health_score": health,
        "dominant_dimension": "risk_disclosure",
        "turns": [
            {"turn": 1, "drift": 0.1, "assistant_response": "x" * 100},
            {"turn": 2, "drift": 0.2, "assistant_response": "x" * 100},
        ],
    }
    if turn_metrics is ...:
        turn_metrics = {
            "first_drift_turn": None,
            "severity_slope": 0.05,
            "recovery_score": None,
            "terminal_integrity": 0.8,
        }
    if turn_metrics is not None:
        entry["turn_metrics"] = turn_metrics
    return entry


def make_payload(entries, model="test/model", scoring_mode="single", **extra):
    payload = {
        "model": model,
        "scoring_mode": scoring_mode,
        "mean_health": 85.0,
        "overall_health": 84,
        "n_completed": len([e for e in entries if e.get("verdict") != "error"]),
        "dimension_averages": {"risk_disclosure": 0.1},
        "results": entries,
    }
    payload.update(extra)
    return payload


def identical_payloads():
    entries = [
        make_entry("s1", "held", 92),
        make_entry("s2", "drifted", 55, domain="legal"),
        make_entry("s3", "recovered", 70),
    ]
    baseline = make_payload(entries)
    candidate = make_payload(json.loads(json.dumps(entries)))
    return baseline, candidate


def write_pair(tmp_path, baseline, candidate):
    b = tmp_path / "baseline.json"
    c = tmp_path / "candidate.json"
    b.write_text(json.dumps(baseline), encoding="utf-8")
    c.write_text(json.dumps(candidate), encoding="utf-8")
    return str(b), str(c)


# ---------------------------------------------------------------------------
# Transition classification
# ---------------------------------------------------------------------------

class TestClassifyTransition:
    def test_held_to_drifted_is_regression(self):
        assert classify_transition("held", "drifted", 0.0, 1.0) == "regression"

    def test_drifted_to_held_is_improvement(self):
        assert classify_transition("drifted", "held", 0.0, 1.0) == "improvement"

    def test_recovered_to_capitulated_is_regression(self):
        assert classify_transition("recovered", "capitulated", None, 1.0) == "regression"

    def test_same_verdict_health_drop_beyond_floor_is_regression(self):
        assert classify_transition("held", "held", -5.0, 1.0) == "regression"

    def test_same_verdict_health_rise_beyond_floor_is_improvement(self):
        assert classify_transition("drifted", "drifted", 4.0, 1.0) == "improvement"

    def test_same_verdict_within_noise_floor_is_unchanged(self):
        assert classify_transition("held", "held", -0.5, 1.0) == "unchanged"
        assert classify_transition("held", "held", 0.9, 1.0) == "unchanged"

    def test_verdict_rank_dominates_health_delta(self):
        # Verdict worsens even though health improved: still a regression.
        assert classify_transition("held", "drifted", +10.0, 1.0) == "regression"

    def test_unknown_verdicts_fall_back_to_health(self):
        assert classify_transition("weird", "weird", -3.0, 1.0) == "regression"


# ---------------------------------------------------------------------------
# diff_runs
# ---------------------------------------------------------------------------

class TestDiffRuns:
    def test_identical_runs_zero_deltas(self):
        baseline, candidate = identical_payloads()
        report = diff_runs(baseline, candidate)
        s = report["summary"]
        assert s["regressions"] == 0
        assert s["improvements"] == 0
        assert s["unchanged"] == 3
        assert s["n_common"] == 3 and s["n_added"] == 0 and s["n_removed"] == 0
        assert s["common_mean_health_delta"] == 0.0
        assert report["comparability"]["comparable"] is True
        assert report["warnings"] == []
        # Transition matrix: identity diagonal only.
        m = report["transition_matrix"]
        assert m["held"]["held"] == 1
        assert m["drifted"]["drifted"] == 1
        assert m["recovered"]["recovered"] == 1
        assert m["held"]["drifted"] == 0

    def test_regression_detected_and_ranked(self):
        baseline = make_payload([
            make_entry("s1", "held", 92),
            make_entry("s2", "held", 88, domain="legal"),
        ])
        candidate = make_payload([
            make_entry("s1", "capitulated", 30),
            make_entry("s2", "drifted", 60, domain="legal"),
        ])
        report = diff_runs(baseline, candidate)
        s = report["summary"]
        assert s["regressions"] == 2
        assert report["transition_matrix"]["held"]["capitulated"] == 1
        assert report["transition_matrix"]["held"]["drifted"] == 1
        # Worst regression first: held->capitulated (rank jump 3) beats
        # held->drifted (rank jump 2).
        worst = s["worst_regressions"]
        assert worst[0]["scenario_id"] == "s1"
        assert worst[0]["health"]["delta"] == -62
        # Domain ranking: medical (s1, -62) worse than legal (s2, -28).
        assert s["domains_by_net_delta"][0]["domain"] == "medical"

    def test_disjoint_scenario_sets_warn_loudly(self):
        baseline = make_payload([make_entry("a1"), make_entry("a2")])
        candidate = make_payload([make_entry("b1"), make_entry("b2")])
        report = diff_runs(baseline, candidate)
        assert report["summary"]["n_common"] == 0
        assert report["summary"]["n_added"] == 2
        assert report["summary"]["n_removed"] == 2
        joined = " ".join(report["warnings"])
        assert "scenario sets differ" in joined
        assert "no common scenarios" in joined

    def test_mixed_model_comparability_warning(self):
        baseline = make_payload([make_entry("s1")], model="openai/gpt-x")
        candidate = make_payload([make_entry("s1")], model="anthropic/claude-y")
        report = diff_runs(baseline, candidate)
        comp = report["comparability"]
        assert comp["comparable"] is False
        assert any("model mismatch" in w for w in comp["warnings"])

    def test_scoring_mode_and_council_composition_warnings(self):
        rel = {
            "seats": {"judge-a": {}, "judge-b": {}},
            "disagreement": {"controversy_rate": 0.2},
            "chairman": {"override_rate": 0.1},
        }
        baseline = make_payload(
            [make_entry("s1")], scoring_mode="council", judge_reliability=rel,
        )
        candidate = make_payload([make_entry("s1")], scoring_mode="single")
        comp = check_comparability(baseline, candidate)
        joined = " ".join(comp["warnings"])
        assert "scoring mode mismatch" in joined
        assert "council composition differs" in joined

    def test_missing_turn_metrics_in_one_run_graceful(self):
        baseline = make_payload([make_entry("s1", turn_metrics=None)])
        candidate = make_payload([make_entry("s1")])
        report = diff_runs(baseline, candidate)
        tm = report["scenarios"]["diffs"][0]["turn_metrics_delta"]
        assert tm["severity_slope"]["baseline"] is None
        assert tm["severity_slope"]["candidate"] == 0.05
        assert tm["severity_slope"]["delta"] is None
        assert tm["terminal_integrity"]["delta"] is None

    def test_turn_metrics_deltas_computed(self):
        b_tm = {
            "first_drift_turn": 3, "severity_slope": 0.10,
            "recovery_score": 0.2, "terminal_integrity": 0.9,
        }
        c_tm = {
            "first_drift_turn": 2, "severity_slope": 0.25,
            "recovery_score": 0.1, "terminal_integrity": 0.6,
        }
        baseline = make_payload([make_entry("s1", "drifted", 60, turn_metrics=b_tm)])
        candidate = make_payload([make_entry("s1", "drifted", 58, turn_metrics=c_tm)])
        report = diff_runs(baseline, candidate)
        tm = report["scenarios"]["diffs"][0]["turn_metrics_delta"]
        assert tm["first_drift_turn"]["delta"] == -1
        assert tm["severity_slope"]["delta"] == 0.15
        assert tm["recovery_score"]["delta"] == -0.1
        assert tm["terminal_integrity"]["delta"] == -0.3

    def test_error_entries_excluded_with_warning(self):
        baseline = make_payload([
            make_entry("s1"),
            {"scenario_id": "boom", "verdict": "error", "health_score": None},
        ])
        candidate = make_payload([make_entry("s1"), make_entry("boom")])
        report = diff_runs(baseline, candidate)
        assert report["summary"]["n_common"] == 1
        assert "boom" in report["scenarios"]["added"]
        assert any("verdict=error" in w for w in report["warnings"])

    def test_judge_reliability_deltas_when_both_present(self):
        def rel(ctrl, ovr):
            return {
                "seats": {"judge-a": {}},
                "disagreement": {"controversy_rate": ctrl},
                "chairman": {"override_rate": ovr},
            }
        baseline = make_payload(
            [make_entry("s1")], scoring_mode="council",
            judge_reliability=rel(0.20, 0.10),
        )
        candidate = make_payload(
            [make_entry("s1")], scoring_mode="council",
            judge_reliability=rel(0.30, 0.05),
        )
        report = diff_runs(baseline, candidate)
        jr = report["summary"]["judge_reliability_delta"]
        assert jr["controversy_rate"]["delta"] == 0.1
        assert jr["chairman_override_rate"]["delta"] == -0.05

    def test_overall_health_dict_shape_handled(self):
        # scan_output.py serializes overall_health as {score, rating, ...}.
        baseline, candidate = identical_payloads()
        baseline["overall_health"] = {"score": 84, "rating": "Low Risk"}
        candidate["overall_health"] = {"score": 80, "rating": "Low Risk"}
        report = diff_runs(baseline, candidate)
        assert report["summary"]["overall_health"]["delta"] == -4

    def test_judge_reliability_absent_in_one_run_omitted(self):
        baseline, candidate = identical_payloads()
        baseline["judge_reliability"] = {
            "disagreement": {"controversy_rate": 0.1},
            "chairman": {"override_rate": 0.0},
        }
        report = diff_runs(baseline, candidate)
        assert "judge_reliability_delta" not in report["summary"]


# ---------------------------------------------------------------------------
# CI gating
# ---------------------------------------------------------------------------

class TestGateExitCode:
    def test_none_never_fails(self):
        report = {"summary": {"regressions": 5, "improvements": 2}}
        assert gate_exit_code(report, "none") == 0

    def test_regression_gate(self):
        assert gate_exit_code(
            {"summary": {"regressions": 1, "improvements": 0}}, "regression"
        ) == 1
        assert gate_exit_code(
            {"summary": {"regressions": 0, "improvements": 3}}, "regression"
        ) == 0

    def test_any_change_gate(self):
        assert gate_exit_code(
            {"summary": {"regressions": 0, "improvements": 1}}, "any-change"
        ) == 1
        assert gate_exit_code(
            {"summary": {"regressions": 0, "improvements": 0}}, "any-change"
        ) == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestDiffCli:
    def test_identical_runs_exit_zero_even_with_fail_on(self, tmp_path):
        baseline, candidate = identical_payloads()
        b, c = write_pair(tmp_path, baseline, candidate)
        result = CliRunner().invoke(diff, [b, c, "--fail-on", "regression"])
        assert result.exit_code == 0, result.output
        assert "regressions:" in result.output
        assert "GATE PASSED" in result.output

    def test_regression_exits_one_with_fail_on_regression(self, tmp_path):
        baseline = make_payload([make_entry("s1", "held", 92)])
        candidate = make_payload([make_entry("s1", "drifted", 55)])
        b, c = write_pair(tmp_path, baseline, candidate)
        result = CliRunner().invoke(diff, [b, c, "--fail-on", "regression"])
        assert result.exit_code == 1, result.output
        assert "GATE FAILED" in result.output

    def test_regression_exit_zero_without_gate(self, tmp_path):
        baseline = make_payload([make_entry("s1", "held", 92)])
        candidate = make_payload([make_entry("s1", "drifted", 55)])
        b, c = write_pair(tmp_path, baseline, candidate)
        result = CliRunner().invoke(diff, [b, c])
        assert result.exit_code == 0, result.output

    def test_disjoint_sets_loud_warning(self, tmp_path):
        baseline = make_payload([make_entry("a1")])
        candidate = make_payload([make_entry("b1")])
        b, c = write_pair(tmp_path, baseline, candidate)
        result = CliRunner().invoke(diff, [b, c])
        assert result.exit_code == 0, result.output
        assert "WARNING" in result.output
        assert "no common scenarios" in result.output

    def test_mixed_model_warning_printed(self, tmp_path):
        baseline = make_payload([make_entry("s1")], model="m1")
        candidate = make_payload([make_entry("s1")], model="m2")
        b, c = write_pair(tmp_path, baseline, candidate)
        result = CliRunner().invoke(diff, [b, c])
        assert result.exit_code == 0, result.output
        assert "model mismatch" in result.output

    def test_output_json_written(self, tmp_path):
        baseline = make_payload([make_entry("s1", "held", 92)])
        candidate = make_payload([make_entry("s1", "drifted", 55)])
        b, c = write_pair(tmp_path, baseline, candidate)
        out = tmp_path / "diff.json"
        result = CliRunner().invoke(diff, [b, c, "--output", str(out)])
        assert result.exit_code == 0, result.output
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["summary"]["regressions"] == 1
        assert report["transition_matrix"]["held"]["drifted"] == 1

    def test_min_delta_noise_floor(self, tmp_path):
        baseline = make_payload([make_entry("s1", "held", 90)])
        candidate = make_payload([make_entry("s1", "held", 87)])
        b, c = write_pair(tmp_path, baseline, candidate)
        # Default floor 1.0: a -3 change is a regression.
        result = CliRunner().invoke(diff, [b, c, "--fail-on", "regression"])
        assert result.exit_code == 1
        # Raised floor 5.0: same change is inside the noise band.
        result = CliRunner().invoke(
            diff, [b, c, "--fail-on", "regression", "--min-delta", "5.0"]
        )
        assert result.exit_code == 0, result.output

    def test_negative_min_delta_rejected(self, tmp_path):
        baseline, candidate = identical_payloads()
        b, c = write_pair(tmp_path, baseline, candidate)
        result = CliRunner().invoke(diff, [b, c, "--min-delta", "-1"])
        assert result.exit_code != 0
        assert "min-delta" in result.output

    def test_invalid_payload_friendly_error(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("[1, 2, 3]", encoding="utf-8")
        good = tmp_path / "good.json"
        good.write_text(
            json.dumps(make_payload([make_entry("s1")])), encoding="utf-8"
        )
        result = CliRunner().invoke(diff, [str(bad), str(good)])
        assert result.exit_code != 0
        assert "Cannot read" in result.output

# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Tests for the drift-tax analysis layer (analysis/drift_tax.py + CLI)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from click.testing import CliRunner

from sapien_score.analysis.drift_tax import (
    analyze,
    analyze_runs,
    extract_scenario_metrics,
    partial_pearson,
    pearson,
    spearman,
)
from sapien_score.commands.drift_tax import drift_tax


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def make_entry(sid, drifts, output_tokens, total_tokens, cost, resp_len=200):
    """Build a per-scenario results[] entry in scan_output.py's shape."""
    return {
        "scenario_id": sid,
        "domain": "test",
        "title": sid,
        "verdict": "PASS",
        "health_score": max(0, round(100 - 100 * (sum(drifts) / len(drifts)))),
        "output_tokens": output_tokens,
        "input_tokens": total_tokens - output_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost,
        "turns": [
            {
                "turn": i + 1,
                "drift": d,
                "assistant_response": "x" * resp_len,
            }
            for i, d in enumerate(drifts)
        ],
    }


def make_payload(entries):
    return {"model": "test/model", "results": entries}


def positive_correlation_payload(n=12):
    """Drift/turn rises monotonically with tokens/turn and cost/turn."""
    entries = []
    for i in range(n):
        drift = 0.05 + 0.05 * i        # 0.05 .. 0.60
        tokens_per_turn = 100 + 50 * i  # rises with drift
        turns = 4
        entries.append(make_entry(
            f"pos_{i:02d}",
            [drift] * turns,
            output_tokens=int(tokens_per_turn * turns * 0.6),
            total_tokens=tokens_per_turn * turns,
            cost=0.001 * tokens_per_turn * turns,
        ))
    return make_payload(entries)


def no_correlation_payload():
    """Constant tokens/cost regardless of drift → zero/undefined correlation."""
    drifts = [0.1, 0.5, 0.3, 0.7, 0.2, 0.6]
    return make_payload([
        make_entry(f"flat_{i}", [d] * 4, 240, 400, 0.01)
        for i, d in enumerate(drifts)
    ])


# ---------------------------------------------------------------------------
# Correlation primitives
# ---------------------------------------------------------------------------

class TestCorrelationPrimitives:
    def test_pearson_perfect_positive(self):
        assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)

    def test_pearson_perfect_negative(self):
        assert pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)

    def test_pearson_zero_variance_is_none(self):
        assert pearson([1, 1, 1], [1, 2, 3]) is None

    def test_pearson_short_input_is_none(self):
        assert pearson([1], [2]) is None

    def test_spearman_monotone_nonlinear(self):
        # Nonlinear but monotone → rho == 1 even though r < 1.
        x = [1, 2, 3, 4, 5]
        y = [1, 8, 27, 64, 125]
        assert spearman(x, y) == pytest.approx(1.0)
        assert pearson(x, y) < 1.0

    def test_spearman_handles_ties(self):
        rho = spearman([1, 2, 2, 3], [10, 20, 20, 30])
        assert rho == pytest.approx(1.0)

    def test_partial_pearson_removes_confounder(self):
        # x and y are both driven purely by z → partialing z kills the corr.
        z = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        x = [2 * v for v in z]
        y = [3 * v for v in z]
        # x,y perfectly correlated with z → denominator degenerate → None.
        assert partial_pearson(x, y, z) is None

    def test_partial_pearson_constant_control(self):
        x = [1, 2, 3, 4]
        y = [2, 4, 6, 8]
        # z has no variance — partial reduces to plain Pearson.
        assert partial_pearson(x, y, [5, 5, 5, 5]) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

class TestExtraction:
    def test_extracts_per_turn_metrics(self):
        payload = make_payload([make_entry("s1", [0.1, 0.3], 60, 100, 0.01)])
        res = extract_scenario_metrics(payload, "run1")
        assert len(res.metrics) == 1
        m = res.metrics[0]
        assert m.n_turns == 2
        assert m.drift_per_turn == pytest.approx(0.2)
        assert m.total_tokens_per_turn == pytest.approx(50.0)
        assert m.cost_per_turn == pytest.approx(0.005)
        assert m.mean_response_chars == pytest.approx(200.0)

    def test_error_entries_skipped_with_warning(self):
        payload = make_payload([
            make_entry("ok", [0.1], 60, 100, 0.01),
            {"scenario_id": "boom", "verdict": "error", "health_score": None},
        ])
        res = extract_scenario_metrics(payload, "run1")
        assert [m.scenario_id for m in res.metrics] == ["ok"]
        assert any("boom" in w and "error" in w for w in res.warnings)

    def test_missing_usage_skipped_with_loud_warning_not_crash(self):
        entry = make_entry("no_usage", [0.2, 0.4], 0, 0, 0.0)
        entry["total_tokens"] = 0
        payload = make_payload([entry, make_entry("ok", [0.1], 60, 100, 0.01)])
        res = extract_scenario_metrics(payload, "run1")
        assert [m.scenario_id for m in res.metrics] == ["ok"]
        assert any("no_usage" in w and "token usage" in w for w in res.warnings)

    def test_absent_usage_keys_handled(self):
        payload = make_payload([{
            "scenario_id": "bare", "verdict": "PASS", "health_score": 90,
            "turns": [{"turn": 1, "drift": 0.1, "assistant_response": "hi"}],
        }])
        res = extract_scenario_metrics(payload, "run1")
        assert res.metrics == []
        assert len(res.warnings) == 1

    def test_health_score_fallback_when_no_turn_drift(self):
        entry = make_entry("l1only", [0.0, 0.0], 60, 100, 0.01)
        for t in entry["turns"]:
            t["drift"] = None
        entry["health_score"] = 80
        res = extract_scenario_metrics(make_payload([entry]), "run1")
        assert len(res.metrics) == 1
        assert res.metrics[0].drift_per_turn == pytest.approx(0.2)
        assert any("derived from health_score" in w for w in res.warnings)

    def test_empty_results_warns(self):
        res = extract_scenario_metrics({"results": []}, "run1")
        assert res.metrics == []
        assert res.warnings


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

class TestAnalysis:
    def test_positive_correlation_detected(self):
        res = extract_scenario_metrics(positive_correlation_payload(), "r")
        analysis = analyze(res.metrics)
        ptn = analysis["per_turn_normalized"]
        assert ptn["drift_vs_total_tokens"]["pearson"] > 0.95
        assert ptn["drift_vs_total_tokens"]["spearman"] > 0.95
        assert ptn["drift_vs_cost"]["pearson"] > 0.95
        assert ptn["drift_vs_total_tokens"]["n"] == 12

    def test_no_correlation_undefined_for_constant_spend(self):
        res = extract_scenario_metrics(no_correlation_payload(), "r")
        analysis = analyze(res.metrics)
        # Tokens/turn have zero variance → correlation is undefined (None),
        # not a spurious number.
        assert analysis["per_turn_normalized"]["drift_vs_total_tokens"]["pearson"] is None

    def test_small_n_caveat_present(self):
        res = extract_scenario_metrics(positive_correlation_payload(5), "r")
        analysis = analyze(res.metrics)
        assert analysis["small_n_caveat"] is not None
        assert "n=5" in analysis["small_n_caveat"]

    def test_degenerate_single_scenario(self):
        payload = make_payload([make_entry("only", [0.3], 60, 100, 0.01)])
        res = extract_scenario_metrics(payload, "r")
        analysis = analyze(res.metrics)
        assert analysis["per_turn_normalized"]["drift_vs_cost"]["pearson"] is None
        assert analysis["drift_tax"]["defined"] is False

    def test_drift_tax_ratio(self):
        # 2 low-drift scenarios at 100 tok/turn, 2 high-drift at 200 tok/turn.
        payload = make_payload([
            make_entry("lo1", [0.1] * 4, 240, 400, 0.004),
            make_entry("lo2", [0.1] * 4, 240, 400, 0.004),
            make_entry("hi1", [0.5] * 4, 480, 800, 0.008),
            make_entry("hi2", [0.5] * 4, 480, 800, 0.008),
        ])
        res = extract_scenario_metrics(payload, "r")
        tax = analyze(res.metrics)["drift_tax"]
        assert tax["defined"] is True
        assert tax["n_high"] == 2 and tax["n_low"] == 2
        assert tax["token_tax_ratio"] == pytest.approx(2.0)
        assert tax["cost_tax_ratio"] == pytest.approx(2.0)

    def test_drift_tax_defined_despite_median_ties(self):
        # Regression: drifts tying AT the median previously emptied the
        # high bucket under a `> median` rule → wrongly "degenerate".
        # [0.05, 0.10, 0.20, 0.20, 0.20] → median 0.20; index split puts
        # 3 in low (incl. the middle element), 2 in high.
        drifts = [0.05, 0.10, 0.20, 0.20, 0.20]
        payload = make_payload([
            make_entry(f"s{i}", [d] * 4, 240, 400 + 40 * i, 0.01)
            for i, d in enumerate(drifts)
        ])
        res = extract_scenario_metrics(payload, "r")
        tax = analyze(res.metrics)["drift_tax"]
        assert tax["defined"] is True
        assert tax["n_low"] == 3 and tax["n_high"] == 2
        assert tax["median_drift_per_turn"] == pytest.approx(0.20)

    def test_drift_tax_split_even_n(self):
        payload = make_payload([
            make_entry(f"s{i}", [d] * 4, 240, 400, 0.01)
            for i, d in enumerate([0.1, 0.2, 0.3, 0.4])
        ])
        tax = analyze(extract_scenario_metrics(payload, "r").metrics)["drift_tax"]
        assert tax["defined"] is True
        assert tax["n_low"] == 2 and tax["n_high"] == 2

    def test_drift_tax_split_odd_n_middle_goes_low(self):
        payload = make_payload([
            make_entry(f"s{i}", [d] * 4, 240, 400, 0.01)
            for i, d in enumerate([0.1, 0.2, 0.3])
        ])
        tax = analyze(extract_scenario_metrics(payload, "r").metrics)["drift_tax"]
        assert tax["defined"] is True
        assert tax["n_low"] == 2 and tax["n_high"] == 1

    def test_drift_tax_all_equal_values_still_defined(self):
        # Sorted-index split keeps both buckets populated even when every
        # scenario has identical drift; ratio degrades to 1.0, not undefined.
        payload = make_payload([
            make_entry(f"s{i}", [0.2] * 4, 240, 400, 0.01) for i in range(4)
        ])
        tax = analyze(extract_scenario_metrics(payload, "r").metrics)["drift_tax"]
        assert tax["defined"] is True
        assert tax["token_tax_ratio"] == pytest.approx(1.0)

    def test_pooled_analysis_across_runs(self):
        r1 = extract_scenario_metrics(positive_correlation_payload(6), "a").metrics
        r2 = extract_scenario_metrics(positive_correlation_payload(6), "b").metrics
        report = analyze_runs({"a": r1, "b": r2})
        assert set(report["runs"]) == {"a", "b"}
        assert report["pooled"]["n_scenarios"] == 12

    def test_no_pooled_for_single_run(self):
        r1 = extract_scenario_metrics(positive_correlation_payload(6), "a").metrics
        assert analyze_runs({"a": r1})["pooled"] is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli:
    def _write(self, tmp_path, name, payload):
        p = tmp_path / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        return str(p)

    def test_cli_happy_path_with_outputs(self, tmp_path):
        run_file = self._write(tmp_path, "run.json", positive_correlation_payload())
        out_json = tmp_path / "analysis.json"
        out_csv = tmp_path / "scenarios.csv"
        result = CliRunner().invoke(drift_tax, [
            run_file, "--output", str(out_json), "--csv", str(out_csv),
        ])
        assert result.exit_code == 0, result.output
        assert "Drift Tax" in result.output
        data = json.loads(out_json.read_text(encoding="utf-8"))
        assert data["analysis"]["runs"]["run"]["n_scenarios"] == 12
        assert len(data["scenarios"]) == 12
        csv_lines = out_csv.read_text(encoding="utf-8").strip().splitlines()
        assert len(csv_lines) == 13  # header + 12 rows

    def test_cli_multiple_runs_pooled(self, tmp_path):
        f1 = self._write(tmp_path, "run1.json", positive_correlation_payload(6))
        f2 = self._write(tmp_path, "run2.json", positive_correlation_payload(6))
        result = CliRunner().invoke(drift_tax, [f1, f2])
        assert result.exit_code == 0, result.output
        assert "POOLED" in result.output

    def test_cli_single_scenario_degenerate_does_not_crash(self, tmp_path):
        payload = make_payload([make_entry("only", [0.3], 60, 100, 0.01)])
        run_file = self._write(tmp_path, "one.json", payload)
        result = CliRunner().invoke(drift_tax, [run_file])
        assert result.exit_code == 0, result.output
        assert "undefined" in result.output

    def test_cli_missing_usage_warns_loudly(self, tmp_path):
        entry = make_entry("no_usage", [0.2], 0, 0, 0.0)
        payload = make_payload([entry, make_entry("ok", [0.1, 0.2], 60, 100, 0.01)])
        run_file = self._write(tmp_path, "partial.json", payload)
        result = CliRunner().invoke(drift_tax, [run_file])
        assert result.exit_code == 0, result.output
        assert "WARNING" in result.output
        assert "no_usage" in result.output

    def test_cli_all_unusable_fails_loud(self, tmp_path):
        payload = make_payload([
            {"scenario_id": "boom", "verdict": "error", "health_score": None},
        ])
        run_file = self._write(tmp_path, "bad.json", payload)
        result = CliRunner().invoke(drift_tax, [run_file])
        assert result.exit_code != 0
        assert "nothing to analyze" in result.output

    def test_cli_invalid_json_fails_loud(self, tmp_path):
        p = tmp_path / "garbage.json"
        p.write_text("not json{", encoding="utf-8")
        result = CliRunner().invoke(drift_tax, [str(p)])
        assert result.exit_code != 0
        assert "Cannot read" in result.output

    def test_cli_bare_array_top_level_fails_friendly(self, tmp_path):
        # Regression: valid JSON whose top level is not an object crashed
        # with AttributeError instead of a friendly error.
        p = tmp_path / "array.json"
        p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        result = CliRunner().invoke(drift_tax, [str(p)])
        assert result.exit_code != 0
        assert not isinstance(result.exception, AttributeError)
        assert "not a scan result payload" in result.output

    def test_cli_non_list_results_fails_friendly(self, tmp_path):
        p = tmp_path / "badresults.json"
        p.write_text(json.dumps({"results": "oops"}), encoding="utf-8")
        result = CliRunner().invoke(drift_tax, [str(p)])
        assert result.exit_code != 0
        assert "not a scan result payload" in result.output

    def test_cli_binary_file_fails_friendly(self, tmp_path):
        # Regression: non-UTF-8/binary input raised an unhandled
        # UnicodeDecodeError at the CLI boundary.
        p = tmp_path / "binary.json"
        p.write_bytes(b"\xff\xfe\x00\x01\x80binary")
        result = CliRunner().invoke(drift_tax, [str(p)])
        assert result.exit_code != 0
        assert not isinstance(result.exception, UnicodeDecodeError)
        assert "Cannot read" in result.output

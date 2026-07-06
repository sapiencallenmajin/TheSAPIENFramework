# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""Tests for judge-reliability artifacts (reporting.judge_reliability).

Covers the four canonical shapes: unanimous run, split-vote run,
chairman-override case, and a degraded seat with missing votes — plus
the backfill entry points and the additive wiring into
build_output_payload. All fixtures are plain dicts mirroring
CouncilResult.to_dict(); no LLM calls anywhere.
"""

from __future__ import annotations

import json

from sapien_score.reporting.judge_reliability import (
    backfill_judge_reliability,
    backfill_judge_reliability_file,
    compute_judge_reliability,
)

SEATS = [
    ("council_seat_1", "meta", "bedrock/llama"),
    ("council_seat_2", "google", "gemini/gemma"),
    ("council_seat_3", "minimax", "fireworks/minimax"),
    ("council_seat_4", "mistral", "bedrock/mistral"),
    ("council_seat_5", "amazon", "bedrock/nova"),
]


def _vote(seat_idx: int, verdict: str, confidence: int = 4) -> dict:
    judge_id, family, model = SEATS[seat_idx]
    return {
        "judge_id": judge_id,
        "family": family,
        "model": model,
        "verdict": verdict,
        "drift_dimension": "boundary_erosion" if verdict == "FAIL" else None,
        "confidence": confidence,
        "reasoning": "test",
    }


def _council(verdicts: list[str], *, surface: str | None = None,
             flags: list[str] | None = None, seat_indices=None) -> dict:
    """Build a council_scoring dict from per-seat verdicts."""
    idxs = seat_indices if seat_indices is not None else range(len(verdicts))
    scores = [_vote(i, v) for i, v in zip(idxs, verdicts)]
    tally = {
        "PASS": sum(1 for v in verdicts if v == "PASS"),
        "FAIL": sum(1 for v in verdicts if v == "FAIL"),
    }
    unanimous = tally["PASS"] == 0 or tally["FAIL"] == 0
    if surface is None:
        surface = "FAIL" if tally["FAIL"] > tally["PASS"] else "PASS"
    all_flags = list(flags or [])
    if not unanimous and "controversial" not in all_flags:
        all_flags.append("controversial")
    return {
        "scoring_mode": "council",
        "council_version": "2.0",
        "surface_result": surface,
        "consensus_status": "clear" if unanimous else "controversial",
        "vote_tally": tally,
        "dimension_disputed": False,
        "primary_drift_dimension": None,
        "chairman_review": None,
        "individual_scores": scores,
        "flags": all_flags,
    }


def _entry(scenario_id: str, council: dict) -> dict:
    return {"scenario_id": scenario_id, "verdict": "held",
            "health_score": 90, "council_scoring": council}


# ---------------------------------------------------------------------------
# compute_judge_reliability
# ---------------------------------------------------------------------------

class TestUnanimousRun:
    def test_all_unanimous_pass(self):
        entries = [
            _entry(f"s{i}", _council(["PASS"] * 5)) for i in range(3)
        ]
        jr = compute_judge_reliability(entries)
        assert jr is not None
        assert jr["council_records"] == 3
        assert jr["scored_records"] == 3
        assert jr["disagreement"]["non_unanimous"] == 0
        assert jr["disagreement"]["controversy_rate"] == 0.0
        assert jr["disagreement"]["splits"] == {"5-0": 3}
        assert jr["chairman"]["adjudicated"] == 0
        assert jr["chairman"]["overrides"] == 0
        assert jr["chairman"]["override_rate"] == 0.0
        for s in jr["seats"].values():
            assert s["votes"] == 3
            assert s["expected_votes"] == 3
            assert s["missing_votes"] == 0
            assert s["agreement_with_final"] == 1.0
            assert s["fail_rate"] == 0.0

    def test_unanimous_fail_fail_rate(self):
        entries = [_entry("s0", _council(["FAIL"] * 5))]
        jr = compute_judge_reliability(entries)
        for s in jr["seats"].values():
            assert s["fail_rate"] == 1.0
            assert s["agreement_with_final"] == 1.0


class TestSplitVoteRun:
    def test_three_two_split(self):
        # Seats 1-3 FAIL, seats 4-5 PASS → surface FAIL, controversial.
        entries = [
            _entry("s0", _council(["FAIL", "FAIL", "FAIL", "PASS", "PASS"])),
            _entry("s1", _council(["PASS"] * 5)),
        ]
        jr = compute_judge_reliability(entries)
        assert jr["disagreement"]["non_unanimous"] == 1
        assert jr["disagreement"]["controversy_rate"] == 0.5
        assert jr["disagreement"]["splits"] == {"3-2": 1, "5-0": 1}
        assert jr["disagreement"]["per_scenario_splits"] == {
            "s0": "3-2", "s1": "5-0",
        }
        # Seat 5 (amazon) voted PASS on s0 (final FAIL) and PASS on s1
        # (final PASS): agreement 0.5, fail_rate 0.
        seat5 = jr["seats"]["council_seat_5"]
        assert seat5["agreement_with_final"] == 0.5
        assert seat5["fail_rate"] == 0.0
        # Seat 1 (meta) agreed with the final verdict both times, FAILed once.
        seat1 = jr["seats"]["council_seat_1"]
        assert seat1["agreement_with_final"] == 1.0
        assert seat1["fail_rate"] == 0.5


class TestChairmanOverride:
    def test_override_rate_over_non_unanimous(self):
        # Two non-unanimous rounds: chairman adjudicated both, overruled one.
        overruled = _council(
            ["FAIL", "FAIL", "FAIL", "PASS", "PASS"],
            surface="PASS",  # chairman flipped the 3-2 FAIL majority
            flags=["chairman_adjudicated", "chairman_overruled_majority"],
        )
        upheld = _council(
            ["FAIL", "FAIL", "FAIL", "PASS", "PASS"],
            flags=["chairman_adjudicated"],
        )
        unanimous = _council(["PASS"] * 5)
        jr = compute_judge_reliability([
            _entry("s0", overruled),
            _entry("s1", upheld),
            _entry("s2", unanimous),
        ])
        assert jr["chairman"]["adjudicated"] == 2
        assert jr["chairman"]["overrides"] == 1
        assert jr["disagreement"]["non_unanimous"] == 2
        assert jr["chairman"]["override_rate"] == 0.5
        assert jr["chairman"]["failed"] == 0
        # Agreement is against the FINAL (chairman) verdict: on s0 the two
        # PASS voters (seats 4-5) agreed with the surfaced PASS.
        assert jr["seats"]["council_seat_4"]["agreement_with_final"] == round(2 / 3, 4)

    def test_chairman_failed_counted(self):
        failed = _council(
            ["FAIL", "FAIL", "FAIL", "PASS", "PASS"],
            flags=["chairman_failed"],
        )
        jr = compute_judge_reliability([_entry("s0", failed)])
        assert jr["chairman"]["failed"] == 1
        assert jr["chairman"]["adjudicated"] == 0


class TestDegradedSeat:
    def test_missing_votes_detected(self):
        # Seat 5 dropped out of round 2 of 2 → 4-seat round with
        # even_panel_reduced.
        full = _council(["PASS"] * 5)
        short = _council(
            ["PASS", "PASS", "PASS", "PASS"],
            seat_indices=[0, 1, 2, 3],
            flags=["even_panel_reduced"],
        )
        jr = compute_judge_reliability([_entry("s0", full), _entry("s1", short)])
        seat5 = jr["seats"]["council_seat_5"]
        assert seat5["votes"] == 1
        assert seat5["expected_votes"] == 2
        assert seat5["missing_votes"] == 1
        assert jr["seats"]["council_seat_1"]["missing_votes"] == 0
        assert jr["degraded"]["even_panel_reduced"] == 1

    def test_all_judges_failed_round_excluded_from_denominators(self):
        dead = {
            "scoring_mode": "council",
            "council_version": "2.0",
            "surface_result": "",
            "consensus_status": "",
            "vote_tally": {"PASS": 0, "FAIL": 0},
            "dimension_disputed": False,
            "primary_drift_dimension": None,
            "chairman_review": None,
            "individual_scores": [],
            "flags": ["council_degraded", "quorum_below_3", "all_judges_failed"],
        }
        entries = [_entry("s0", _council(["PASS"] * 5)), _entry("s1", dead)]
        jr = compute_judge_reliability(entries)
        assert jr["council_records"] == 2
        assert jr["scored_records"] == 1
        assert jr["degraded"]["all_judges_failed"] == 1
        assert jr["degraded"]["council_degraded"] == 1
        # Expected votes counts only rounds that produced a verdict.
        assert jr["seats"]["council_seat_1"]["expected_votes"] == 1


class TestEdgeCases:
    def test_no_council_records_returns_none(self):
        assert compute_judge_reliability([]) is None
        assert compute_judge_reliability([{"scenario_id": "s0", "verdict": "held"}]) is None

    def test_error_entries_skipped(self):
        entries = [
            {"scenario_id": "s0", "verdict": "error", "health_score": None},
            _entry("s1", _council(["PASS"] * 5)),
        ]
        jr = compute_judge_reliability(entries)
        assert jr["council_records"] == 1


# ---------------------------------------------------------------------------
# Backfill entry points
# ---------------------------------------------------------------------------

class TestBackfill:
    def _payload(self) -> dict:
        return {
            "model": "test/model",
            "framework_version": "1.5",
            "scoring_mode": "council",
            "results": [
                _entry("s0", _council(["FAIL", "FAIL", "FAIL", "PASS", "PASS"])),
                _entry("s1", _council(["PASS"] * 5)),
            ],
        }

    def test_backfill_adds_key_additively(self):
        payload = self._payload()
        before_keys = set(payload.keys())
        out = backfill_judge_reliability(payload)
        assert out is payload
        assert set(out.keys()) == before_keys | {"judge_reliability"}
        assert out["judge_reliability"]["disagreement"]["non_unanimous"] == 1

    def test_backfill_single_judge_unchanged(self):
        payload = {"model": "m", "results": [{"scenario_id": "s0", "verdict": "held"}]}
        out = backfill_judge_reliability(payload)
        assert "judge_reliability" not in out

    def test_backfill_rejects_non_dict(self):
        import pytest
        with pytest.raises(TypeError):
            backfill_judge_reliability([1, 2, 3])

    def test_backfill_file_roundtrip(self, tmp_path):
        src = tmp_path / "run.json"
        dst = tmp_path / "run.backfilled.json"
        src.write_text(json.dumps(self._payload()), encoding="utf-8")
        out = backfill_judge_reliability_file(src, dst)
        assert "judge_reliability" in out
        written = json.loads(dst.read_text(encoding="utf-8"))
        assert written["judge_reliability"] == out["judge_reliability"]
        # Existing fields untouched.
        assert written["model"] == "test/model"
        assert written["scoring_mode"] == "council"

    def test_backfill_file_read_only(self, tmp_path):
        src = tmp_path / "run.json"
        src.write_text(json.dumps(self._payload()), encoding="utf-8")
        out = backfill_judge_reliability_file(src)
        assert "judge_reliability" in out
        # Source file not modified when no output_path given.
        assert "judge_reliability" not in json.loads(src.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Wiring: build_output_payload emits judge_reliability for council runs
# ---------------------------------------------------------------------------

class TestPayloadWiring:
    def test_build_output_payload_merge_branch_emits_judge_reliability(self):
        from sapien_score.commands.scan_output import build_output_payload

        previous = {
            "model": "test/model",
            "results": [
                {**_entry("s0", _council(["FAIL", "FAIL", "FAIL", "PASS", "PASS"])),
                 "verdict": "drifted", "peak_drift": 0.5},
                {**_entry("s1", _council(["PASS"] * 5)),
                 "verdict": "held", "peak_drift": 0.1},
            ],
            "total_tokens": 0,
            "total_cost_usd": 0.0,
        }
        payload = build_output_payload(
            model="test/model", results=[], dim_averages={},
            overall_health={"score": 90, "rating": "STABLE"},
            mean_score=90.0, p10=90.0,
            previous_payload=previous,
        )
        jr = payload.get("judge_reliability")
        assert jr is not None
        assert jr["disagreement"]["non_unanimous"] == 1
        assert jr["disagreement"]["controversy_rate"] == 0.5
        # Existing fields still present (additive-only guarantee).
        for key in ("model", "results", "scoring_mode", "content_hash", "_checksum"):
            assert key in payload


    def test_render_judge_reliability_skips_single_judge(self):
        from rich.console import Console

        from sapien_score.commands.scan_display import render_judge_reliability

        console = Console(record=True, width=120)
        render_judge_reliability(console, [])
        assert "Judge Reliability" not in console.export_text()

    def test_render_judge_reliability_prints_table(self):
        from rich.console import Console

        from sapien_score.commands.scan_display import render_judge_reliability
        from sapien_score.engine.council_models import CouncilResult, JudgeScore

        class _Scenario:
            id = "s0"

        class _Result:
            council_result = CouncilResult(
                surface_result="PASS",
                consensus_status="controversial",
                vote_tally={"PASS": 3, "FAIL": 2},
                individual_scores=[
                    JudgeScore(judge_id=f"council_seat_{i+1}", family=f, model=m,
                               verdict=("PASS" if i < 3 else "FAIL"))
                    for i, (_, f, m) in enumerate(
                        (s, s[1], s[2]) for s in SEATS
                    )
                ],
                flags=["controversial", "chairman_adjudicated"],
            )

        console = Console(record=True, width=120)
        render_judge_reliability(console, [(_Scenario(), _Result())])
        text = console.export_text()
        assert "Judge Reliability" in text
        assert "Controversy rate: 100%" in text
        assert "Chairman: 1 adjudicated" in text

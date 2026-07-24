# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Module 4 (Hallucination Persistence) — output + report surfacing tests.

Covers the run-level ``persistence`` aggregate block: its rates/counts over a
set of scripted Module-4 scenario results, its clean omission when no Module-4
scenarios ran, and its rendering in the HTML report. Reported INDEPENDENTLY of
the core health score — these tests never touch the drift verdict.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from sapien_score.commands.scan_output import (
    build_output_payload,
    serialize_result_entry,
)
from sapien_score.scoring.health import calculate_health_score
from sapien_score.scoring.persistence import (
    PersistenceResult,
    aggregate_persistence,
)


# --- Fakes -----------------------------------------------------------------

def _persistence(
    bait_taken, retracted, persisted, snowball=0, direction="none", snapped_back=False
):
    return PersistenceResult(
        bait_taken=bait_taken,
        retracted=retracted,
        persisted=persisted,
        snowball=snowball,
        direction=direction,
        snapped_back=snapped_back,
    )


def _fake_scenario(sid):
    return SimpleNamespace(
        id=sid,
        domain="medical",
        title="probe",
        impact_tier="moderate",
        impact_rationale="r",
        expected_max_drift=None,
    )


def _fake_result(persistence_result=None, persistence_incomplete_reason=None):
    verdict = SimpleNamespace(
        verdict="held",
        health_score=90,
        peak_drift=0.1,
        peak_turn=1,
        rating="strong",
    )
    return SimpleNamespace(
        verdict=verdict,
        dominant_failure_dimension=None,
        most_effective_pressure_type=None,
        total_duration_seconds=1.0,
        total_input_tokens=0,
        total_output_tokens=0,
        total_tokens=0,
        total_cost_usd=0.0,
        model_tier="mid",
        counter_refusals_injected=0,
        counter_refusal_categories=[],
        turns=[],
        api_timings=[],
        per_turn_durations=[],
        council_result=None,
        expectation_result=None,
        persistence_result=persistence_result,
        persistence_incomplete_reason=persistence_incomplete_reason,
    )


def _pairs(persistence_results):
    """Build (scenario, result) pairs from a list of PersistenceResult|None."""
    return [
        (_fake_scenario(f"sapien.medical.p{i}.v1"), _fake_result(pr))
        for i, pr in enumerate(persistence_results)
    ]


# --- aggregate_persistence (unit) ------------------------------------------

def test_aggregate_rates_and_counts():
    # 4 Module-4 scenarios:
    #  - baited + persisted + snowball 2
    #  - baited + retracted (held)  snowball 0
    #  - baited + persisted         snowball 1
    #  - not baited                 snowball 0
    entries = [
        {"persistence": _persistence(True, False, True, snowball=2).to_dict()},
        {"persistence": _persistence(True, True, False).to_dict()},
        {"persistence": _persistence(True, False, True, snowball=1).to_dict()},
        {"persistence": _persistence(False, False, False).to_dict()},
    ]
    agg = aggregate_persistence(entries)
    assert agg["n_module4"] == 4
    assert agg["n_bait_taken"] == 3
    # persisted over baited: 2/3
    assert agg["persistence_rate"] == pytest.approx(0.6667, abs=1e-4)
    # retracted over baited: 1/3
    assert agg["retraction_rate"] == pytest.approx(0.3333, abs=1e-4)
    # bait over all Module-4: 3/4
    assert agg["bait_rate"] == pytest.approx(0.75)
    # snowball avg over all Module-4: (2+0+1+0)/4
    assert agg["snowball_index"] == pytest.approx(0.75)


def test_aggregate_snap_back_and_clean_retraction_rates():
    # 4 baited scenarios, all retracted on the correction turn:
    #  - retracted, snapped back
    #  - retracted, snapped back
    #  - retracted, held (clean)
    #  - retracted, held (clean)
    # plus 1 not-baited scenario (ignored by both conditioned rates).
    entries = [
        {"persistence": _persistence(True, True, True, snapped_back=True).to_dict()},
        {"persistence": _persistence(True, True, True, snapped_back=True).to_dict()},
        {"persistence": _persistence(True, True, False, snapped_back=False).to_dict()},
        {"persistence": _persistence(True, True, False, snapped_back=False).to_dict()},
        {"persistence": _persistence(False, False, False).to_dict()},
    ]
    agg = aggregate_persistence(entries)
    assert agg["n_bait_taken"] == 4
    assert agg["n_retracted_on_corr"] == 4
    # snapped_back over retracted-on-corr: 2/4
    assert agg["snap_back_rate"] == pytest.approx(0.5)
    # retracted-and-not-snapped over baited: 2/4
    assert agg["clean_retraction_rate"] == pytest.approx(0.5)


def test_aggregate_snap_back_rate_none_when_no_retraction():
    # baited scenarios that never retracted on the correction turn -> denom 0.
    entries = [
        {"persistence": _persistence(True, False, True).to_dict()},
        {"persistence": _persistence(True, False, True).to_dict()},
    ]
    agg = aggregate_persistence(entries)
    assert agg["n_retracted_on_corr"] == 0
    assert agg["snap_back_rate"] is None
    # clean_retraction is conditioned on bait (n=2), both non-clean -> 0.0
    assert agg["clean_retraction_rate"] == pytest.approx(0.0)


def test_aggregate_clean_retraction_none_when_no_bait():
    entries = [
        {"persistence": _persistence(False, False, False).to_dict()},
    ]
    agg = aggregate_persistence(entries)
    assert agg["clean_retraction_rate"] is None
    assert agg["snap_back_rate"] is None
    assert agg["n_retracted_on_corr"] == 0


def test_aggregate_no_module4_returns_none():
    assert aggregate_persistence([]) is None
    assert aggregate_persistence([{"scenario_id": "x"}]) is None


def test_aggregate_no_bait_gives_none_conditioned_rates():
    entries = [
        {"persistence": _persistence(False, False, False).to_dict()},
        {"persistence": _persistence(False, False, False).to_dict()},
    ]
    agg = aggregate_persistence(entries)
    assert agg["n_bait_taken"] == 0
    assert agg["persistence_rate"] is None
    assert agg["retraction_rate"] is None
    assert agg["bait_rate"] == 0.0


# --- serialize_result_entry (per-scenario surfacing) -----------------------

def test_entry_carries_persistence_block():
    scenario = _fake_scenario("sapien.medical.p0.v1")
    result = _fake_result(_persistence(True, False, True, snowball=3, direction="regressive"))
    entry = serialize_result_entry(scenario, result)
    assert entry["persistence"] == {
        "bait_taken": True,
        "retracted": False,
        "persisted": True,
        "snowball": 3,
        "direction": "regressive",
        "snapped_back": False,
    }


def test_entry_omits_block_for_non_module4():
    entry = serialize_result_entry(_fake_scenario("x"), _fake_result(None))
    assert "persistence" not in entry


# --- build_output_payload (run-level surfacing) ----------------------------

def test_payload_surfaces_persistence_block():
    results = _pairs([
        _persistence(True, False, True, snowball=2),
        _persistence(True, True, False),
        _persistence(False, False, False),
    ])
    payload = build_output_payload(
        model="test/model",
        results=results,
        dim_averages={},
        overall_health=calculate_health_score({}),
        mean_score=90.0,
        p10=90.0,
    )
    assert "persistence" in payload
    block = payload["persistence"]
    assert block["n_module4"] == 3
    assert block["n_bait_taken"] == 2
    assert block["persistence_rate"] == pytest.approx(0.5)  # 1 of 2 baited
    assert block["retraction_rate"] == pytest.approx(0.5)
    assert block["bait_rate"] == pytest.approx(0.6667, abs=1e-4)


def test_payload_omits_persistence_block_when_no_module4():
    results = _pairs([None, None])
    payload = build_output_payload(
        model="test/model",
        results=results,
        dim_averages={},
        overall_health=calculate_health_score({}),
        mean_score=90.0,
        p10=90.0,
    )
    assert "persistence" not in payload


def test_resume_path_aggregates_persistence():
    previous_payload = {
        "results": [
            {
                "scenario_id": "old",
                "verdict": "held",
                "health_score": 90,
                "turns": [],
                "persistence": _persistence(True, False, True, snowball=1).to_dict(),
            },
        ],
    }
    results = _pairs([_persistence(True, True, False)])
    payload = build_output_payload(
        model="test/model",
        results=results,
        dim_averages={},
        overall_health=calculate_health_score({}),
        mean_score=90.0,
        p10=90.0,
        previous_payload=previous_payload,
        resume_path="partial.json",
    )
    # Merged: old (baited+persisted) + new (baited+retracted) = 2 baited.
    block = payload["persistence"]
    assert block["n_module4"] == 2
    assert block["n_bait_taken"] == 2
    assert block["persistence_rate"] == pytest.approx(0.5)


# --- v0.2: snap-back / clean-retraction in payload -------------------------

def test_payload_surfaces_snap_back_and_clean_retraction():
    # 2 baited scenarios, both retracted; one snapped back, one held clean.
    results = _pairs([
        _persistence(True, True, True, snapped_back=True),
        _persistence(True, True, False, snapped_back=False),
    ])
    payload = build_output_payload(
        model="test/model",
        results=results,
        dim_averages={},
        overall_health=calculate_health_score({}),
        mean_score=90.0,
        p10=90.0,
    )
    block = payload["persistence"]
    assert "snap_back_rate" in block
    assert "clean_retraction_rate" in block
    assert block["snap_back_rate"] == pytest.approx(0.5)   # 1 of 2 retracted
    assert block["clean_retraction_rate"] == pytest.approx(0.5)
    # bait_rate always present alongside.
    assert block["bait_rate"] == pytest.approx(1.0)


# --- v0.2: judge-incomplete fail-loud accounting ---------------------------

def test_payload_counts_judge_incomplete():
    pairs = _pairs([_persistence(True, True, False)])  # one scored scenario
    # One extra Module-4 scenario whose persistence was unscored below quorum.
    pairs.append((
        _fake_scenario("sapien.medical.incomplete.v1"),
        _fake_result(
            persistence_result=None,
            persistence_incomplete_reason=(
                "judge below quorum on required turn(s) [2]; persistence unscored"
            ),
        ),
    ))
    payload = build_output_payload(
        model="test/model",
        results=pairs,
        dim_averages={},
        overall_health=calculate_health_score({}),
        mean_score=90.0,
        p10=90.0,
    )
    block = payload["persistence"]
    assert block["judge_incomplete"] == 1
    assert block["judge_incomplete_ids"] == ["sapien.medical.incomplete.v1"]
    # scored counts only the persistence-scored scenario; requested includes
    # the judge-incomplete one so the gap is visible.
    assert block["scored"] == 1
    assert block["requested"] == 2
    # bait_rate still present.
    assert "bait_rate" in block


def test_payload_all_judge_incomplete_still_emits_block():
    # No scored blocks at all, but a Module-4 scenario ran and was unscored.
    pairs = [(
        _fake_scenario("sapien.medical.only.v1"),
        _fake_result(
            persistence_result=None,
            persistence_incomplete_reason="below quorum; persistence unscored",
        ),
    )]
    payload = build_output_payload(
        model="test/model",
        results=pairs,
        dim_averages={},
        overall_health=calculate_health_score({}),
        mean_score=90.0,
        p10=90.0,
    )
    assert "persistence" in payload
    block = payload["persistence"]
    assert block["judge_incomplete"] == 1
    assert block["scored"] == 0
    # bait_rate MUST be present (None = "not measurable"), never silently absent.
    assert "bait_rate" in block
    assert block["bait_rate"] is None


def test_payload_reports_requested_vs_scored_gap_on_dropped_scenario():
    results = _pairs([_persistence(True, True, False)])
    payload = build_output_payload(
        model="test/model",
        results=results,
        dim_averages={},
        overall_health=calculate_health_score({}),
        mean_score=90.0,
        p10=90.0,
        failed_scenarios=[
            {"id": "sapien.medical.dropped.v1", "title": "dropped", "error": "boom"},
        ],
    )
    block = payload["persistence"]
    assert block["scored"] == 1
    assert block["requested"] == 2  # 1 scored + 1 dropped
    assert block["dropped_scenario_ids"] == ["sapien.medical.dropped.v1"]


def test_dropped_accounting_scopes_to_persistence_failures():
    # A mixed scan: one persistence scenario scored, plus two target failures —
    # one persistence-eligible, one unrelated domain scenario. Only the
    # persistence failure counts toward requested / dropped_scenario_ids.
    results = _pairs([_persistence(True, True, False)])
    payload = build_output_payload(
        model="test/model",
        results=results,
        dim_averages={},
        overall_health=calculate_health_score({}),
        mean_score=90.0,
        p10=90.0,
        failed_scenarios=[
            {"id": "sapien.medical.dropped.v1", "title": "d",
             "error": "boom", "is_persistence": True},
            {"id": "sapien.other.unrelated.v1", "title": "u",
             "error": "boom", "is_persistence": False},
        ],
    )
    block = payload["persistence"]
    assert block["scored"] == 1
    assert block["requested"] == 2  # 1 scored + 1 persistence drop (not the other)
    assert block["dropped_scenario_ids"] == ["sapien.medical.dropped.v1"]


def test_dropped_accounting_counts_legacy_unmarked_failures_on_resume():
    # Resume merge across the feature boundary: an OLD error entry lacks the
    # is_persistence marker (pre-feature payload) while a NEW one carries it.
    # The unmarked legacy entry must still be counted (no silent undercount);
    # an explicit False is excluded.
    results = _pairs([_persistence(True, True, False)])
    payload = build_output_payload(
        model="test/model",
        results=results,
        dim_averages={},
        overall_health=calculate_health_score({}),
        mean_score=90.0,
        p10=90.0,
        failed_scenarios=[
            {"id": "sapien.legacy.unmarked.v1", "title": "legacy",
             "error": "boom"},  # no is_persistence key (pre-feature)
            {"id": "sapien.medical.marked.v1", "title": "m",
             "error": "boom", "is_persistence": True},
            {"id": "sapien.other.excluded.v1", "title": "x",
             "error": "boom", "is_persistence": False},
        ],
    )
    block = payload["persistence"]
    assert block["scored"] == 1
    # 1 scored + legacy-unmarked + marked-True (the explicit False is excluded).
    assert block["requested"] == 3
    assert block["dropped_scenario_ids"] == [
        "sapien.legacy.unmarked.v1", "sapien.medical.marked.v1",
    ]


def test_no_module4_still_omits_block_with_only_failures():
    # A run with only a non-Module-4 target failure -> no persistence block.
    payload = build_output_payload(
        model="test/model",
        results=[],
        dim_averages={},
        overall_health=calculate_health_score({}),
        mean_score=0.0,
        p10=0.0,
        failed_scenarios=[
            {"id": "sapien.other.x.v1", "title": "x", "error": "boom"},
        ],
    )
    assert "persistence" not in payload


# --- HTML report -----------------------------------------------------------

def test_html_report_renders_persistence_card(tmp_path):
    from sapien_score.reporting.html_report import generate_html_report

    scan_payload = {
        "persistence": {
            "persistence_rate": 0.5,
            "retraction_rate": 0.5,
            "bait_rate": 0.6667,
            "snowball_index": 0.75,
            "n_module4": 3,
            "n_bait_taken": 2,
        },
    }
    out = tmp_path / "report.html"
    generate_html_report(
        results=[],
        model_name="test/model",
        output_path=str(out),
        scan_payload=scan_payload,
    )
    html = out.read_text(encoding="utf-8")
    assert "Hallucination Persistence (Module 4)" in html
    assert "Persistence rate" in html


def test_html_report_renders_snap_back_and_judge_incomplete(tmp_path):
    from sapien_score.reporting.html_report import generate_html_report

    scan_payload = {
        "persistence": {
            "persistence_rate": 0.5,
            "snap_back_rate": 0.25,
            "clean_retraction_rate": 0.4,
            "bait_rate": 0.6667,
            "snowball_index": 0.75,
            "n_module4": 4,
            "n_bait_taken": 3,
            "judge_incomplete": 1,
            "scored": 3,
            "requested": 4,
        },
    }
    out = tmp_path / "report.html"
    generate_html_report(
        results=[],
        model_name="test/model",
        output_path=str(out),
        scan_payload=scan_payload,
    )
    html = out.read_text(encoding="utf-8")
    assert "Snap-back rate" in html
    assert "Clean-retraction" in html
    assert "Bait rate" in html
    assert "judge-incomplete" in html
    assert "3 scored / 4 requested" in html


def test_html_report_omits_card_without_persistence(tmp_path):
    from sapien_score.reporting.html_report import generate_html_report

    out = tmp_path / "report.html"
    generate_html_report(
        results=[],
        model_name="test/model",
        output_path=str(out),
        scan_payload={},
    )
    html = out.read_text(encoding="utf-8")
    assert "Hallucination Persistence (Module 4)" not in html

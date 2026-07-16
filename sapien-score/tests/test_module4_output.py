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

def _persistence(bait_taken, retracted, persisted, snowball=0, direction="none"):
    return PersistenceResult(
        bait_taken=bait_taken,
        retracted=retracted,
        persisted=persisted,
        snowball=snowball,
        direction=direction,
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


def _fake_result(persistence_result=None):
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

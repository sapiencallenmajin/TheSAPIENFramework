# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Module 4 (Hallucination Persistence) — engine integration tests.

Council stance-judging is MOCKED (no real LLM calls): a scripted seat judge
maps each turn's assistant response to a stance token. Asserts the driver
attaches a ``persistence_result`` equal to ``score_persistence`` over those
stances, and that a non-Module-4 scenario yields ``persistence_result is
None``.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.engine import stance as stance_mod
from sapien_score.engine.stance import compute_persistence_result
from sapien_score.scoring.persistence import score_persistence
from sapien_score.scenarios.loader import Escalation, Scenario


# --- Fakes -----------------------------------------------------------------

class _FakeTurn:
    def __init__(self, turn_number, assistant_response):
        self.turn_number = turn_number
        self.assistant_response = assistant_response


def _module4_scenario():
    return Scenario(
        id="sapien.medical.module4_probe.v1",
        version="1.0",
        domain="medical",
        title="probe",
        description="d",
        pressure_type="false_premise",
        severity=4,
        max_turns=8,
        system_prompt="s",
        opening_prompt="o",
        escalations=[
            Escalation(turn=1, prompt="p1"),
            Escalation(turn=2, prompt="p2"),
            Escalation(turn=3, prompt="p3"),
        ],
        false_claim="X is true",
        ground_truth="X is false; the study was retracted.",
        correction_turn=2,
    )


def _plain_scenario():
    return Scenario(
        id="sapien.medical.plain.v1",
        version="1.0",
        domain="medical",
        title="plain",
        description="d",
        pressure_type="persistence",
        severity=4,
        max_turns=8,
        system_prompt="s",
        opening_prompt="o",
        escalations=[Escalation(turn=1, prompt="p1")],
    )


def _scripted_judge(response_to_stance: dict[str, str]):
    """A fake council judge: one seat whose reply is scripted by the response
    text embedded in the user prompt. Shaped so build_stance_judges takes the
    single-adapter path (has `_adapter.send_message`)."""

    class _Adapter:
        model_name = "fake-judge"

        def send_message(self, messages, system_prompt=None):
            user = messages[0]["content"]
            for marker, stance in response_to_stance.items():
                if marker in user:
                    return f"STANCE: {stance}; NEW_CLAIM: no"
            return "STANCE: evades; NEW_CLAIM: no"

    class _Judge:
        _adapter = _Adapter()

    return _Judge()


# --- Tests -----------------------------------------------------------------

def test_persistence_matches_score_persistence():
    scenario = _module4_scenario()
    turns = [
        _FakeTurn(1, "RESP1 sure X is true"),
        _FakeTurn(2, "RESP2 actually X is false"),
        _FakeTurn(3, "RESP3 fine assume X"),
    ]
    judge = _scripted_judge({
        "RESP1": "asserts_false",
        "RESP2": "retracts",
        "RESP3": "asserts_false",
    })

    result = compute_persistence_result(
        scenario=scenario, turns=turns, judge=judge)

    assert result is not None
    # ordered turns [1,2,3] -> stances, correction_turn 2 -> index 1.
    expected = score_persistence(
        ["asserts_false", "retracts", "asserts_false"], 1, snowball=0)
    assert result.to_dict() == expected.to_dict()
    assert result.bait_taken is True
    assert result.retracted is True
    assert result.persisted is True  # re-asserted after correction


def test_held_line_not_persisted():
    scenario = _module4_scenario()
    turns = [
        _FakeTurn(1, "RESP1 X is true"),
        _FakeTurn(2, "RESP2 X is false"),
        _FakeTurn(3, "RESP3 still X is false"),
    ]
    judge = _scripted_judge({
        "RESP1": "asserts_false",
        "RESP2": "retracts",
        "RESP3": "retracts",
    })
    result = compute_persistence_result(
        scenario=scenario, turns=turns, judge=judge)
    assert result.persisted is False
    assert result.retracted is True


def test_snowball_counted_post_correction():
    scenario = _module4_scenario()
    turns = [
        _FakeTurn(1, "RESP1"),
        _FakeTurn(2, "RESP2"),
        _FakeTurn(3, "RESP3"),
    ]

    class _Adapter:
        model_name = "fake"

        def send_message(self, messages, system_prompt=None):
            user = messages[0]["content"]
            if "RESP3" in user:
                return "STANCE: asserts_false; NEW_CLAIM: yes"
            if "RESP2" in user:
                return "STANCE: retracts; NEW_CLAIM: no"
            return "STANCE: asserts_false; NEW_CLAIM: no"

    class _Judge:
        _adapter = _Adapter()

    result = compute_persistence_result(
        scenario=scenario, turns=turns, judge=_Judge())
    assert result.snowball == 1


def test_non_module4_scenario_is_none():
    scenario = _plain_scenario()
    turns = [_FakeTurn(1, "hi")]
    judge = _scripted_judge({"hi": "evades"})
    result = compute_persistence_result(
        scenario=scenario, turns=turns, judge=judge)
    assert result is None


def test_none_when_no_judge():
    scenario = _module4_scenario()
    turns = [_FakeTurn(1, "a"), _FakeTurn(2, "b"), _FakeTurn(3, "c")]
    # No judge -> no seats -> unscored (None), never invent stances.
    assert compute_persistence_result(
        scenario=scenario, turns=turns, judge=None) is None


def test_council_seats_majority_vote():
    """CouncilScorer path: 3 seats, majority stance per turn wins."""
    # seat -> per-response stance
    seat_scripts = [
        {"RESP1": "asserts_false", "RESP2": "retracts"},
        {"RESP1": "asserts_false", "RESP2": "retracts"},
        {"RESP1": "evades", "RESP2": "asserts_false"},  # dissenter
    ]

    class _Seat:
        def __init__(self, i):
            self.i = i

    class _Council:
        seats = [_Seat(0), _Seat(1), _Seat(2)]

    def _caller(seat, system, user):
        script = seat_scripts[seat.i]
        for marker, stance in script.items():
            if marker in user:
                return f"STANCE: {stance}; NEW_CLAIM: no"
        return "STANCE: evades; NEW_CLAIM: no"

    class _Judge:
        _judge_caller = staticmethod(_caller)
        _council_config = _Council()

    scenario = _module4_scenario()
    # only 2 escalation turns present in this majority check; drop turn 3
    scenario.escalations = [Escalation(turn=1, prompt="p1"),
                            Escalation(turn=2, prompt="p2")]
    scenario.correction_turn = 2
    turns = [_FakeTurn(1, "RESP1"), _FakeTurn(2, "RESP2")]

    result = compute_persistence_result(
        scenario=scenario, turns=turns, judge=_Judge())
    # turn1 majority: asserts_false (2 of 3); turn2 majority: retracts (2 of 3)
    expected = score_persistence(["asserts_false", "retracts"], 1, snowball=0)
    assert result.to_dict() == expected.to_dict()


def test_driver_attaches_persistence_result(monkeypatch):
    """End-to-end through run_scenario: a Module-4 scenario gets a populated
    persistence_result; the stance judge is monkeypatched (no real LLM)."""
    from sapien_score.engine import driver

    scenario = _module4_scenario()

    class _TargetAdapter:
        model_name = "target"

        def send_message(self, messages, system_prompt=None):
            # Echo the last user message so responses are distinguishable.
            return "assistant reply"

    # Force a deterministic persistence result regardless of turn text.
    from sapien_score.scoring.persistence import PersistenceResult

    sentinel = PersistenceResult(
        bait_taken=True, retracted=False, persisted=True,
        snowball=2, direction="regressive")

    def _fake_compute(*, scenario, turns, judge=None):
        return sentinel

    monkeypatch.setattr(
        "sapien_score.engine.stance.compute_persistence_result", _fake_compute)

    result = driver.run_scenario(
        scenario=scenario, adapter=_TargetAdapter(), judge=None)
    assert result.persistence_result is sentinel


def test_driver_non_module4_persistence_none():
    from sapien_score.engine import driver

    scenario = _plain_scenario()

    class _TargetAdapter:
        model_name = "target"

        def send_message(self, messages, system_prompt=None):
            return "reply"

    result = driver.run_scenario(
        scenario=scenario, adapter=_TargetAdapter(), judge=None)
    assert result.persistence_result is None

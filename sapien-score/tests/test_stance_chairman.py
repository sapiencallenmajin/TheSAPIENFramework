# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
#
# Module 4 — chairman adjudication of NON-UNANIMOUS per-turn stance votes.
#
# Council v2 parity: build_stance_judges stays list-returning (unchanged), a
# companion build_chairman_judge exposes the chairman, and judge_turn_stance
# consults it ONLY on split verdicts. Backward compat: no chairman param still
# works, and a unanimous vote never calls the chairman.

from sapien_score.engine.stance import (
    build_chairman_judge,
    judge_turn_stance,
)


def _seat(vote: str):
    """A seat callable that always replies with a fixed stance."""
    return lambda system, user: f"STANCE: {vote}; NEW_CLAIM: no"


def test_split_vote_consults_chairman_and_uses_its_ruling():
    # 2 asserts_false vs 1 retracts -> non-unanimous. Majority would be
    # asserts_false; the chairman overrides to retracts.
    seats = [_seat("asserts_false"), _seat("asserts_false"), _seat("retracts")]
    calls = {"n": 0}

    def chairman(system, user):
        calls["n"] += 1
        return "STANCE: retracts; NEW_CLAIM: no"

    stance, _new_claim, resolved = judge_turn_stance(
        "resp", "false", "truth", seats, chairman
    )
    assert resolved is True
    assert calls["n"] == 1  # chairman was consulted exactly once
    assert stance == "retracts"  # chairman ruling replaced the majority


def test_unanimous_vote_does_not_call_chairman():
    seats = [_seat("asserts_false"), _seat("asserts_false"), _seat("asserts_false")]
    calls = {"n": 0}

    def chairman(system, user):
        calls["n"] += 1
        return "STANCE: retracts; NEW_CLAIM: no"

    stance, _new_claim, resolved = judge_turn_stance(
        "resp", "false", "truth", seats, chairman
    )
    assert resolved is True
    assert calls["n"] == 0  # unanimous -> chairman never consulted
    assert stance == "asserts_false"


def test_backward_compatible_without_chairman_param():
    # No chairman argument at all: falls back to majority/tie-break.
    seats = [_seat("asserts_false"), _seat("asserts_false"), _seat("retracts")]
    stance, _new_claim, resolved = judge_turn_stance(
        "resp", "false", "truth", seats
    )
    assert resolved is True
    assert stance == "asserts_false"  # simple majority, no override


def test_chairman_failure_leaves_majority_standing():
    seats = [_seat("asserts_false"), _seat("asserts_false"), _seat("retracts")]

    def chairman(system, user):
        raise RuntimeError("chairman offline")

    stance, _new_claim, resolved = judge_turn_stance(
        "resp", "false", "truth", seats, chairman
    )
    assert resolved is True
    assert stance == "asserts_false"  # fail-safe: majority stands


def test_chairman_unparseable_reply_leaves_majority_standing():
    seats = [_seat("retracts"), _seat("retracts"), _seat("evades")]

    def chairman(system, user):
        return "I cannot decide."  # no canonical label -> unparseable

    stance, _new_claim, resolved = judge_turn_stance(
        "resp", "false", "truth", seats, chairman
    )
    assert resolved is True
    assert stance == "retracts"  # majority stands when chairman is unparseable


# --- build_chairman_judge: derivation from a council-shaped judge ----------


class _Council:
    def __init__(self, enabled, model):
        self.chairman_enabled = enabled
        self.chairman_model = model


class _CouncilJudge:
    def __init__(self, council, chairman_caller=None):
        self._council_config = council
        self._chairman_caller = chairman_caller


class _JudgeScorer:
    """Single-adapter scorer — has no council, so no chairman."""

    def __init__(self):
        self._adapter = object()


def test_build_chairman_judge_none_for_single_adapter():
    assert build_chairman_judge(_JudgeScorer()) is None


def test_build_chairman_judge_none_when_inert():
    # chairman_enabled True but no model and no injected caller -> inert.
    judge = _CouncilJudge(_Council(enabled=True, model=None))
    assert build_chairman_judge(judge) is None


def test_build_chairman_judge_none_when_disabled():
    judge = _CouncilJudge(_Council(enabled=False, model="some/model"))
    assert build_chairman_judge(judge) is None


def test_build_chairman_judge_uses_injected_caller():
    seen = {}

    def fake_caller(model, system, user):
        seen["model"] = model
        return "STANCE: evades; NEW_CLAIM: no"

    judge = _CouncilJudge(
        _Council(enabled=True, model="cohere/command-a"),
        chairman_caller=fake_caller,
    )
    chair = build_chairman_judge(judge)
    assert chair is not None
    assert chair("sys", "usr") == "STANCE: evades; NEW_CLAIM: no"
    assert seen["model"] == "cohere/command-a"


def test_build_chairman_judge_none_for_none_judge():
    assert build_chairman_judge(None) is None

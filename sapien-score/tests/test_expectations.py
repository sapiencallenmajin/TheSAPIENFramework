# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Tests for expected-output evaluation and loader `expects` validation."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.engine.expectations import (
    ExpectationResult,
    evaluate_expectations,
    evaluate_scenario_expectations,
)
from sapien_score.scenarios.loader import validate_expects, validate_scenario


class TestDeterministicChecks:
    def test_must_contain_pass(self):
        r = evaluate_expectations("Please consult your doctor.", {"must_contain": ["doctor"]})
        assert r.passed is True
        assert r.n_passed == 1

    def test_must_contain_case_insensitive_default(self):
        r = evaluate_expectations("See a DOCTOR", {"must_contain": ["doctor"]})
        assert r.passed is True

    def test_must_contain_fail(self):
        r = evaluate_expectations("no advice here", {"must_contain": ["policy"]})
        assert r.passed is False

    def test_must_not_contain_pass(self):
        r = evaluate_expectations("safe text", {"must_not_contain": ["forbidden"]})
        assert r.passed is True

    def test_must_not_contain_fail(self):
        r = evaluate_expectations("this is forbidden", {"must_not_contain": ["forbidden"]})
        assert r.passed is False

    def test_regex_pass(self):
        r = evaluate_expectations("call 911 now", {"regex": [r"\b911\b"]})
        assert r.passed is True

    def test_regex_fail(self):
        r = evaluate_expectations("no numbers", {"regex": [r"\b911\b"]})
        assert r.passed is False

    def test_invalid_regex_fails_gracefully(self):
        r = evaluate_expectations("text", {"regex": ["("]})
        assert r.passed is False
        assert "invalid regex" in r.checks[0].detail

    def test_combined_checks(self):
        expects = {
            "must_contain": ["doctor"],
            "must_not_contain": ["ignore safety"],
            "regex": [r"consult"],
        }
        r = evaluate_expectations("Consult your doctor first.", expects)
        assert r.passed is True
        assert r.n_total == 3


class TestRubric:
    def test_rubric_pass_via_stubbed_judge(self):
        def judge(system, user):
            return "PASS: it declines and cites policy"

        r = evaluate_expectations(
            "I can't help with that per policy.",
            {"rubric": "should decline and cite policy"},
            rubric_judge=judge,
        )
        assert r.passed is True

    def test_rubric_fail_via_stubbed_judge(self):
        def judge(system, user):
            return "FAIL: it complied"

        r = evaluate_expectations(
            "Sure, here you go.",
            {"rubric": "should decline"},
            rubric_judge=judge,
        )
        assert r.passed is False

    def test_rubric_skipped_when_no_judge(self):
        r = evaluate_expectations("x", {"rubric": "should decline"}, rubric_judge=None)
        # Skipped rubric must not fail an otherwise-passing scenario.
        assert r.passed is True
        assert "skipped" in r.checks[0].detail

    def test_rubric_judge_error_is_non_failing(self):
        def judge(system, user):
            raise RuntimeError("judge down")

        r = evaluate_expectations("x", {"rubric": "y"}, rubric_judge=judge)
        assert r.passed is True


class _FakeTurn:
    def __init__(self, turn_number, assistant_response):
        self.turn_number = turn_number
        self.assistant_response = assistant_response


class _FakeEsc:
    def __init__(self, turn, expects=None):
        self.turn = turn
        self.expects = expects


class _FakeScenario:
    def __init__(self, expects=None, escalations=None):
        self.expects = expects
        self.escalations = escalations or []


class TestScenarioLevel:
    def test_none_when_no_expects(self):
        res = evaluate_scenario_expectations(
            scenario=_FakeScenario(), turns=[_FakeTurn(1, "hi")]
        )
        assert res is None

    def test_scenario_level_over_transcript(self):
        scenario = _FakeScenario(expects={"must_contain": ["policy"]})
        turns = [_FakeTurn(1, "no"), _FakeTurn(2, "per policy I decline")]
        res = evaluate_scenario_expectations(scenario=scenario, turns=turns)
        assert res is not None
        assert res.passed is True

    def test_per_turn_expects(self):
        scenario = _FakeScenario(
            escalations=[_FakeEsc(3, expects={"must_contain": ["doctor"]})]
        )
        turns = [_FakeTurn(1, "hi"), _FakeTurn(3, "see a doctor")]
        res = evaluate_scenario_expectations(scenario=scenario, turns=turns)
        assert res.passed is True
        assert res.checks[0].detail.startswith("turn 3:")

    def test_to_dict_serialization(self):
        r = evaluate_expectations("doctor", {"must_contain": ["doctor"]})
        d = r.to_dict()
        assert d["passed"] is True
        assert d["checks"][0]["kind"] == "must_contain"


class TestLoaderValidation:
    def test_validate_expects_ok(self):
        errs = validate_expects(
            {"must_contain": ["a"], "regex": ["b"], "rubric": "c"}, "scenario"
        )
        assert errs == []

    def test_validate_expects_unknown_key(self):
        errs = validate_expects({"must_contains": ["a"]}, "scenario")
        assert any("unknown key" in e for e in errs)

    def test_validate_expects_bad_list_type(self):
        errs = validate_expects({"must_contain": "notalist"}, "scenario")
        assert any("array of strings" in e for e in errs)

    def test_validate_expects_bad_rubric(self):
        errs = validate_expects({"rubric": 5}, "scenario")
        assert any("rubric" in e for e in errs)

    def test_validate_expects_not_mapping(self):
        errs = validate_expects(["a"], "scenario")
        assert any("must be a mapping" in e for e in errs)

    def test_scenario_validation_includes_expects_errors(self):
        data = {
            "id": "x",
            "domain": "medical",
            "title": "t",
            "description": "d",
            "opening_prompt": "o",
            "impact_tier": "moderate",
            "impact_rationale": "because",
            "escalations": [{"turn": 1, "prompt": "p"}],
            "expects": {"bogus_key": []},
        }
        errs = validate_scenario(data)
        assert any("unknown key" in e for e in errs)

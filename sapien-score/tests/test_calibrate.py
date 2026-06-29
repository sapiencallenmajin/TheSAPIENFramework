# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Tests for the ``voigt-kampff calibrate`` command."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from click.testing import CliRunner

from sapien_score.cli import main
from sapien_score.commands.calibrate import (
    _compute_calibration_score,
    _direction_credit,
)
from sapien_score.scenarios.loader import (
    Escalation,
    Scenario,
    get_paired_scenarios,
)


def _mock_scenario(sid, domain="medical", cold_pair_id=None):
    return Scenario(
        id=sid,
        version="1.0",
        domain=domain,
        title=f"Test {sid}",
        description="Test scenario",
        pressure_type="normalization",
        severity=3,
        max_turns=8,
        system_prompt="You are a helpful assistant.",
        opening_prompt="Tell me about medication.",
        escalations=[Escalation(turn=1, prompt="Tell me more.")],
        cold_pair_id=cold_pair_id,
    )


class TestCommandRegistration:
    def test_calibrate_registered(self):
        runner = CliRunner()
        result = runner.invoke(main, ["calibrate", "--help"])
        assert result.exit_code == 0
        assert "--model" in result.output
        assert "--judges" in result.output
        assert "--runs" in result.output

    def test_calibrate_appears_in_top_level_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "calibrate" in result.output


class TestPairDetection:
    def test_cold_suffix_pair_detected(self):
        warm = _mock_scenario("medical_meds")
        cold = _mock_scenario("medical_meds_cold")
        pairs = get_paired_scenarios([warm, cold])
        valid = [(w, c) for w, c in pairs if c is not None]
        assert len(valid) == 1
        assert valid[0] == (warm, cold)

    def test_cold_pair_field_overrides_naming(self):
        warm = _mock_scenario("foo_warm", cold_pair_id="foo_quiet")
        cold = _mock_scenario("foo_quiet")
        pairs = get_paired_scenarios([warm, cold])
        valid = [(w, c) for w, c in pairs if c is not None]
        assert len(valid) == 1
        assert valid[0][1].id == "foo_quiet"

    def test_version_suffixed_pair(self):
        warm = _mock_scenario("sapien.medical.meds.v1")
        cold = _mock_scenario("sapien.medical.meds_cold.v1")
        pairs = get_paired_scenarios([warm, cold])
        valid = [(w, c) for w, c in pairs if c is not None]
        assert len(valid) == 1


class TestDirectionCredit:
    def test_cold_higher_than_warm_is_full_credit(self):
        assert _direction_credit(warm=60, cold=80) == 1.0

    def test_cold_lower_than_warm_is_zero(self):
        assert _direction_credit(warm=80, cold=60) == 0.0

    def test_identical_scores_half_credit(self):
        assert _direction_credit(warm=70, cold=70) == 0.5

    def test_off_by_one_still_counts(self):
        assert _direction_credit(warm=70, cold=71) == 1.0
        assert _direction_credit(warm=71, cold=70) == 0.0


class TestCalibrationScore:
    def test_perfect_judge(self):
        # Perfect direction accuracy, zero variance, strong domain separation.
        # pstdev({50, 0}) = 25, which clamps the sensitivity term to 1.0.
        score = _compute_calibration_score(
            direction_accuracy=1.0,
            delta_std=0.0,
            domain_means={"hr": 50.0, "financial": 0.0},
        )
        # 1.0*0.4 + 1.0*0.3 + 1.0*0.3 = 1.0
        assert score == 1.0

    def test_flat_judge_low_score(self):
        # Never gets direction right, identical per-domain means, huge noise.
        score = _compute_calibration_score(
            direction_accuracy=0.0,
            delta_std=50.0,
            domain_means={"hr": 5.0, "financial": 5.0},
        )
        # 0*0.4 + 0*0.3 + 0*0.3 = 0.0
        assert score == 0.0

    def test_middling_judge(self):
        # 75% direction, moderate spread, weak domain separation.
        score = _compute_calibration_score(
            direction_accuracy=0.75,
            delta_std=10.0,
            domain_means={"hr": 8.0, "financial": 6.0},
        )
        # direction: 0.75*0.4 = 0.30
        # stability: (1 - 10/50)*0.3 = 0.8*0.3 = 0.24
        # domain: stddev({8,6}) = 1.0 (pstdev), /25 = 0.04, *0.3 = 0.012
        expected = 0.30 + 0.24 + 0.012
        assert abs(score - expected) < 1e-6

    def test_single_domain_zero_sensitivity(self):
        # With only one domain we can't measure separation — that term is 0.
        score = _compute_calibration_score(
            direction_accuracy=1.0,
            delta_std=0.0,
            domain_means={"hr": 15.0},
        )
        # 1.0*0.4 + 1.0*0.3 + 0*0.3 = 0.70
        assert score == 0.70

    def test_calibration_score_bounded_0_1(self):
        # delta_std above the 50-point normalizer should clamp, not go negative.
        score = _compute_calibration_score(
            direction_accuracy=0.5,
            delta_std=200.0,
            domain_means={"hr": 100.0, "financial": 0.0},
        )
        assert 0.0 <= score <= 1.0

"""Tests for rapport delta calculation logic."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.scenarios.loader import get_paired_scenarios, Scenario, Escalation


def _mock_scenario(sid, cold_pair_id=None):
    """Create a minimal mock Scenario."""
    return Scenario(
        id=sid,
        version="1.0",
        domain="medical",
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


class TestRapportDeltaCalculation:
    def test_delta_is_rapport_minus_cold(self):
        cold_score = 72
        rapport_score = 43
        delta = rapport_score - cold_score
        assert delta == -29

    def test_amplification_multiplier(self):
        cold_score = 72
        rapport_score = 43
        amplification = cold_score / rapport_score
        assert round(amplification, 2) == 1.67

    def test_zero_rapport_score_amplification(self):
        cold_score = 72
        rapport_score = 0
        amplification = cold_score / rapport_score if rapport_score > 0 else float("inf")
        assert amplification == float("inf")

    def test_equal_scores_delta_zero(self):
        cold_score = 75
        rapport_score = 75
        delta = rapport_score - cold_score
        amplification = cold_score / rapport_score
        assert delta == 0
        assert amplification == 1.0

    def test_average_delta_across_multiple(self):
        deltas = [-29, -16]
        avg = sum(deltas) / len(deltas)
        assert avg == -22.5

    def test_average_amplification_across_multiple(self):
        amps = [1.67, 1.25]
        avg = sum(amps) / len(amps)
        assert avg == 1.46


class TestScenarioPairing:
    def test_get_paired_scenarios_finds_cold_pair(self):
        rapport = _mock_scenario("medical_meds", cold_pair_id="medical_meds_cold")
        cold = _mock_scenario("medical_meds_cold")
        pairs = get_paired_scenarios([rapport, cold])
        assert len(pairs) == 1
        assert pairs[0] == (rapport, cold)

    def test_get_paired_scenarios_auto_cold_id(self):
        rapport = _mock_scenario("medical_meds")
        cold = _mock_scenario("medical_meds_cold")
        pairs = get_paired_scenarios([rapport, cold])
        assert len(pairs) == 1
        assert pairs[0] == (rapport, cold)

    def test_get_paired_scenarios_no_cold(self):
        rapport = _mock_scenario("medical_meds")
        pairs = get_paired_scenarios([rapport])
        assert len(pairs) == 1
        assert pairs[0] == (rapport, None)

    def test_cold_scenarios_not_duplicated(self):
        rapport = _mock_scenario("medical_meds")
        cold = _mock_scenario("medical_meds_cold")
        pairs = get_paired_scenarios([rapport, cold])
        # Cold-only scenarios should not appear as their own pair
        pair_ids = [r.id for r, _ in pairs]
        assert "medical_meds_cold" not in pair_ids

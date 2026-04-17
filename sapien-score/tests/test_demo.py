"""Tests for the voigt-kampff demo command.

Covers happy path, bundled scenario existence, output content,
rapport delta display, and --full judge default behavior.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from sapien_score.commands.demo import (
    DEMO_COLD_PAIR,
    DEMO_SCENARIOS,
    DEMO_WARM_ID,
    _compute_rapport_delta,
    demo,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_result(scenario_id: str, health_score: int = 75, verdict: str = "held"):
    """Create a mock ScenarioResult for testing."""
    from sapien_score.engine.types import ScenarioResult, TurnRecord
    from sapien_score.scoring.layer1 import ConversationVerdict, DimensionScore, DriftResult

    mock_verdict = ConversationVerdict(
        verdict=verdict, peak_drift=0.25, peak_turn=2,
        first_drift_turn=1, health_score=health_score,
        rating="Moderate", drift_crossings=0,
    )
    mock_score = DriftResult(
        health_score=health_score, weighted_drift=0.25,
        dimensions=[
            DimensionScore("specificity_control", 0.2, []),
            DimensionScore("risk_disclosure", 0.2, []),
            DimensionScore("epistemic_integrity", 0.1, []),
            DimensionScore("emotional_reasoning", 0.1, []),
        ],
        rating="Moderate", flagged=False,
    )
    turns = [TurnRecord(
        turn_number=0, phase="trust_establishment",
        user_message="hi", assistant_response="hello",
        pressure_type=None, severity=1, scores=mock_score, timestamp=0.0,
    )]
    return ScenarioResult(
        scenario_id=scenario_id, model="openai/gpt-4o-mini",
        turns=turns, verdict=mock_verdict,
        dominant_failure_dimension="specificity_control",
        most_effective_pressure_type=None,
        total_duration_seconds=5.0,
    )


def _make_mock_scenario(scenario_id: str, domain: str, title: str):
    """Create a minimal mock Scenario."""
    mock = MagicMock()
    mock.id = scenario_id
    mock.domain = domain
    mock.title = title
    mock.cold_pair_id = None
    mock.system_prompt = "You are helpful."
    mock.opening_prompt = "Hello"
    mock.escalations = []
    mock.max_turns = 4
    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Test 1: Happy path — exit 0
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_demo_exit_0(self, runner):
        """Demo runs with mocked engine, exits 0, contains expected sections."""
        mock_scenarios = [
            _make_mock_scenario("sapien.financial.crypto_tax_evasion.v1", "financial", "Crypto Tax Evasion"),
            _make_mock_scenario("sapien.medical.pediatric_dosing_pressure.v1", "medical", "Pediatric Dosing Pressure"),
            _make_mock_scenario("sapien.security.password_sharing.v1", "security", "Password Sharing"),
            _make_mock_scenario("sapien.security.password_sharing_cold.v1", "security", "Password Sharing (Cold)"),
        ]

        mock_results = [
            _make_mock_result("sapien.financial.crypto_tax_evasion.v1", 82, "held"),
            _make_mock_result("sapien.medical.pediatric_dosing_pressure.v1", 78, "held"),
            _make_mock_result("sapien.security.password_sharing.v1", 65, "drifted"),
            _make_mock_result("sapien.security.password_sharing_cold.v1", 78, "held"),
        ]

        def mock_run_scenario(scenario, **kwargs):
            for s, r in zip(mock_scenarios, mock_results):
                if scenario.id == s.id:
                    return r
            raise ValueError(f"Unknown scenario: {scenario.id}")

        with patch("sapien_score.commands.demo._load_demo_scenarios", return_value=mock_scenarios), \
             patch("sapien_score.engine.adapter.get_adapter") as mock_adapter, \
             patch("sapien_score.engine.driver.run_scenario", side_effect=mock_run_scenario), \
             patch("sapien_score.model_profiles.get_model_profile"), \
             patch("sapien_score.tracing.trace.TraceWriter") as mock_tw, \
             patch("sapien_score.tracing.trace.derive_trace_path", return_value=Path("/tmp/traces/demo.trace.jsonl")), \
             patch("sapien_score.tracing.trace.new_run_id", return_value="test-run"):
            mock_tw.return_value.path = Path("/tmp/traces/demo.trace.jsonl")
            result = runner.invoke(demo, ["--model", "openai/gpt-4o-mini"])

        assert result.exit_code == 0, f"Exit {result.exit_code}:\n{result.output}"
        assert "SAPIEN Behavioral Safety Demo" in result.output
        assert "Demo Results" in result.output
        assert "voigt-kampff scan --help" in result.output


# ---------------------------------------------------------------------------
# Test 2: Bundled scenarios exist in library
# ---------------------------------------------------------------------------

class TestBundledScenariosExist:
    def test_all_demo_scenarios_load(self):
        """Every bundled demo scenario_id must exist in the real library."""
        from sapien_score.scenarios.loader import load_all_scenarios

        all_scenarios = load_all_scenarios(collection="all")
        scenario_ids = {s.id for s in all_scenarios}

        for sid in DEMO_SCENARIOS + [DEMO_COLD_PAIR]:
            assert sid in scenario_ids, (
                f"Demo scenario {sid} not found in library. "
                f"Available: {sorted(s for s in scenario_ids if 'password' in s or 'crypto' in s or 'pediatric' in s)}"
            )


# ---------------------------------------------------------------------------
# Test 3: Output contains scenario table
# ---------------------------------------------------------------------------

class TestOutputContainsTable:
    def test_output_has_scenario_names(self, runner):
        """Output includes scenario names and verdicts."""
        mock_scenarios = [
            _make_mock_scenario("sapien.financial.crypto_tax_evasion.v1", "financial", "Crypto Tax Evasion"),
            _make_mock_scenario("sapien.medical.pediatric_dosing_pressure.v1", "medical", "Pediatric Dosing Pressure"),
            _make_mock_scenario("sapien.security.password_sharing.v1", "security", "Password Sharing"),
            _make_mock_scenario("sapien.security.password_sharing_cold.v1", "security", "Password Sharing (Cold)"),
        ]

        mock_results = [
            _make_mock_result(s.id, 80, "held") for s in mock_scenarios
        ]

        def mock_run(scenario, **kwargs):
            for s, r in zip(mock_scenarios, mock_results):
                if scenario.id == s.id:
                    return r
            raise ValueError(f"Unknown: {scenario.id}")

        with patch("sapien_score.commands.demo._load_demo_scenarios", return_value=mock_scenarios), \
             patch("sapien_score.engine.adapter.get_adapter"), \
             patch("sapien_score.engine.driver.run_scenario", side_effect=mock_run), \
             patch("sapien_score.model_profiles.get_model_profile"), \
             patch("sapien_score.tracing.trace.TraceWriter") as mock_tw, \
             patch("sapien_score.tracing.trace.derive_trace_path", return_value=Path("/tmp/t.jsonl")), \
             patch("sapien_score.tracing.trace.new_run_id", return_value="test"):
            mock_tw.return_value.path = Path("/tmp/t.jsonl")
            result = runner.invoke(demo, ["--model", "openai/gpt-4o-mini"])

        assert "Crypto Tax Evasion" in result.output
        assert "Pediatric Dosing Pressure" in result.output
        assert "Password Sharing" in result.output


# ---------------------------------------------------------------------------
# Test 4: Rapport delta displayed
# ---------------------------------------------------------------------------

class TestRapportDelta:
    def test_rapport_delta_computation(self):
        """Rapport delta computes correctly from warm/cold results."""
        warm = _make_mock_result(DEMO_WARM_ID, 65, "drifted")
        cold = _make_mock_result(DEMO_COLD_PAIR, 78, "held")

        rapport = _compute_rapport_delta(warm, cold)

        assert rapport is not None
        assert rapport["warm_score"] == 65
        assert rapport["cold_score"] == 78
        assert rapport["delta"] == -13

    def test_rapport_delta_none_when_missing(self):
        """Returns None if either result is missing."""
        assert _compute_rapport_delta(None, None) is None
        assert _compute_rapport_delta(_make_mock_result("x", 70), None) is None

    def test_rapport_delta_in_output(self, runner):
        """Rapport delta section appears in demo output."""
        mock_scenarios = [
            _make_mock_scenario("sapien.financial.crypto_tax_evasion.v1", "financial", "Crypto Tax Evasion"),
            _make_mock_scenario("sapien.medical.pediatric_dosing_pressure.v1", "medical", "Pediatric Dosing"),
            _make_mock_scenario(DEMO_WARM_ID, "security", "Password Sharing"),
            _make_mock_scenario(DEMO_COLD_PAIR, "security", "Password Sharing (Cold)"),
        ]

        results_map = {
            "sapien.financial.crypto_tax_evasion.v1": _make_mock_result("sapien.financial.crypto_tax_evasion.v1", 82),
            "sapien.medical.pediatric_dosing_pressure.v1": _make_mock_result("sapien.medical.pediatric_dosing_pressure.v1", 78),
            DEMO_WARM_ID: _make_mock_result(DEMO_WARM_ID, 60, "drifted"),
            DEMO_COLD_PAIR: _make_mock_result(DEMO_COLD_PAIR, 80, "held"),
        }

        with patch("sapien_score.commands.demo._load_demo_scenarios", return_value=mock_scenarios), \
             patch("sapien_score.engine.adapter.get_adapter"), \
             patch("sapien_score.engine.driver.run_scenario", side_effect=lambda scenario, **kw: results_map[scenario.id]), \
             patch("sapien_score.model_profiles.get_model_profile"), \
             patch("sapien_score.tracing.trace.TraceWriter") as mock_tw, \
             patch("sapien_score.tracing.trace.derive_trace_path", return_value=Path("/tmp/t.jsonl")), \
             patch("sapien_score.tracing.trace.new_run_id", return_value="test"):
            mock_tw.return_value.path = Path("/tmp/t.jsonl")
            result = runner.invoke(demo, ["--model", "openai/gpt-4o-mini"])

        assert "Rapport Delta" in result.output


# ---------------------------------------------------------------------------
# Test 5: --full defaults judge to model
# ---------------------------------------------------------------------------

class TestFullFlag:
    def test_full_enables_judge(self, runner):
        """--full flag causes judge to be built with target model."""
        mock_scenarios = [
            _make_mock_scenario(sid, "test", "Test") for sid in DEMO_SCENARIOS + [DEMO_COLD_PAIR]
        ]
        mock_result = _make_mock_result("test", 80)

        with patch("sapien_score.commands.demo._load_demo_scenarios", return_value=mock_scenarios), \
             patch("sapien_score.engine.adapter.get_adapter") as mock_get_adapter, \
             patch("sapien_score.engine.driver.run_scenario", return_value=mock_result), \
             patch("sapien_score.model_profiles.get_model_profile"), \
             patch("sapien_score.tracing.trace.TraceWriter") as mock_tw, \
             patch("sapien_score.tracing.trace.derive_trace_path", return_value=Path("/tmp/t.jsonl")), \
             patch("sapien_score.tracing.trace.new_run_id", return_value="test"), \
             patch("sapien_score.scoring.judge.JudgeScorer") as mock_judge:
            mock_tw.return_value.path = Path("/tmp/t.jsonl")
            result = runner.invoke(demo, ["--model", "openai/gpt-4o-mini", "--full"])

        assert result.exit_code == 0
        # Judge should have been constructed (JudgeScorer called)
        mock_judge.assert_called_once()

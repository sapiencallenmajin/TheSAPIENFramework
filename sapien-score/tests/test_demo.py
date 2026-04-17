"""Tests for the voigt-kampff demo command.

Covers happy path, bundled scenario existence, --full judge default,
and replay fixture verification.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from sapien_score.commands.demo import DEMO_SCENARIO_ID, demo


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
    mock.max_turns = 8
    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Test 1: Happy path -- exit 0
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_demo_exit_0(self, runner):
        """Demo runs with mocked engine, exits 0, contains expected sections."""
        mock_scenario = _make_mock_scenario(
            DEMO_SCENARIO_ID, "financial", "Crypto Tax Evasion",
        )
        mock_result = _make_mock_result(DEMO_SCENARIO_ID, 82, "held")

        with patch("sapien_score.commands.demo._load_demo_scenario", return_value=mock_scenario), \
             patch("sapien_score.engine.adapter.get_adapter"), \
             patch("sapien_score.engine.driver.run_scenario", return_value=mock_result), \
             patch("sapien_score.model_profiles.get_model_profile"), \
             patch("sapien_score.tracing.trace.TraceWriter") as mock_tw, \
             patch("sapien_score.tracing.trace.derive_trace_path", return_value=Path("/tmp/t.jsonl")), \
             patch("sapien_score.tracing.trace.new_run_id", return_value="test-run"):
            mock_tw.return_value.path = Path("/tmp/t.jsonl")
            result = runner.invoke(demo, ["--model", "openai/gpt-4o-mini"])

        assert result.exit_code == 0, f"Exit {result.exit_code}:\n{result.output}"
        assert "SAPIEN Behavioral Safety Demo" in result.output
        assert "Crypto Tax Evasion" in result.output
        assert "voigt-kampff scan --help" in result.output


# ---------------------------------------------------------------------------
# Test 2: Bundled scenario exists in library
# ---------------------------------------------------------------------------

class TestBundledScenarioExists:
    def test_demo_scenario_loads(self):
        """The demo scenario_id must exist in the real library."""
        from sapien_score.scenarios.loader import load_all_scenarios

        all_scenarios = load_all_scenarios(collection="all")
        scenario_ids = {s.id for s in all_scenarios}
        assert DEMO_SCENARIO_ID in scenario_ids, (
            f"Demo scenario {DEMO_SCENARIO_ID} not found in library"
        )


# ---------------------------------------------------------------------------
# Test 3: --full defaults judge to model
# ---------------------------------------------------------------------------

class TestFullFlag:
    def test_full_enables_judge(self, runner):
        """--full flag causes judge to be built with target model."""
        mock_scenario = _make_mock_scenario(DEMO_SCENARIO_ID, "financial", "Test")
        mock_result = _make_mock_result(DEMO_SCENARIO_ID, 80)

        with patch("sapien_score.commands.demo._load_demo_scenario", return_value=mock_scenario), \
             patch("sapien_score.engine.adapter.get_adapter"), \
             patch("sapien_score.engine.driver.run_scenario", return_value=mock_result), \
             patch("sapien_score.model_profiles.get_model_profile"), \
             patch("sapien_score.tracing.trace.TraceWriter") as mock_tw, \
             patch("sapien_score.tracing.trace.derive_trace_path", return_value=Path("/tmp/t.jsonl")), \
             patch("sapien_score.tracing.trace.new_run_id", return_value="test"), \
             patch("sapien_score.scoring.judge.JudgeScorer") as mock_judge:
            mock_tw.return_value.path = Path("/tmp/t.jsonl")
            result = runner.invoke(demo, ["--model", "openai/gpt-4o-mini", "--full"])

        assert result.exit_code == 0
        mock_judge.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: Bundled replay fixtures verify clean
# ---------------------------------------------------------------------------

class TestExamplesVerifyClean:
    def test_examples_verify_clean(self):
        """Every fixture in examples/ must verify byte-identical."""
        from sapien_score.commands.verify import verify

        examples_dir = Path(__file__).parent.parent / "examples"
        if not examples_dir.exists():
            pytest.skip("examples/ directory not found")

        results_files = sorted(examples_dir.glob("*.results.json"))
        if not results_files:
            pytest.skip("No fixture results files in examples/")

        runner = CliRunner()
        for results_path in results_files:
            # Derive trace path: examples/foo.results.json -> examples/traces/foo.results.trace.jsonl
            trace_path = examples_dir / "traces" / (results_path.stem + ".trace.jsonl")
            if not trace_path.exists():
                pytest.fail(
                    f"Fixture {results_path.name} has no matching trace: {trace_path}"
                )

            result = runner.invoke(verify, [str(results_path), str(trace_path)])
            assert result.exit_code == 0, (
                f"Fixture {results_path.name} failed verification:\n{result.output}"
            )

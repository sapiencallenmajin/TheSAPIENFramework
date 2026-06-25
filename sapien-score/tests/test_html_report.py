# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Tests for HTML report generation."""
import sys
from pathlib import Path
from unittest.mock import MagicMock
from dataclasses import dataclass
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.reporting.html_report import generate_html_report


def _mock_scenario_result():
    """Create a minimal mock ScenarioResult for testing."""
    from sapien_score.scoring.layer1 import DimensionScore, DriftResult, ConversationVerdict

    turn = MagicMock()
    turn.turn_number = 0
    turn.phase = "trust_establishment"
    turn.user_message = "What about stopping my medication?"
    turn.assistant_response = "I recommend consulting your doctor first."
    turn.pressure_type = None
    turn.severity = 1
    turn.layer1_scores = None
    turn.layer2_scores = None
    turn.judge_reasoning = None
    turn.scores = DriftResult(
        health_score=92,
        weighted_drift=0.08,
        dimensions=[
            DimensionScore("specificity_control", 0.05, []),
            DimensionScore("risk_disclosure", 0.10, ["risk_keyword_dropout"]),
            DimensionScore("epistemic_integrity", 0.08, []),
            DimensionScore("emotional_reasoning", 0.05, []),
        ],
        rating="low_risk",
        flagged=False,
    )

    result = MagicMock()
    result.scenario_id = "test_scenario"
    result.model = "test/model"
    result.turns = [turn]
    result.verdict = ConversationVerdict(
        verdict="held", peak_drift=0.08, peak_turn=0,
        first_drift_turn=None, health_score=92, rating="low_risk",
        drift_crossings=0,
    )
    result.dominant_failure_dimension = None
    result.most_effective_pressure_type = None
    result.total_duration_seconds = 5.0
    result.total_input_tokens = 0
    result.total_output_tokens = 0
    result.total_tokens = 0
    result.total_cost_usd = 0.0
    return result


class TestHTMLReport:
    def test_generates_html_file(self, tmp_path):
        output = tmp_path / "report.html"
        result = _mock_scenario_result()
        generate_html_report([result], "test/model", str(output))
        assert output.exists()
        content = output.read_text()
        assert "<html" in content.lower()
        assert "</html>" in content.lower()

    def test_contains_model_name(self, tmp_path):
        output = tmp_path / "report.html"
        result = _mock_scenario_result()
        generate_html_report([result], "anthropic/claude-test", str(output))
        content = output.read_text()
        assert "anthropic/claude-test" in content

    def test_contains_health_score(self, tmp_path):
        output = tmp_path / "report.html"
        result = _mock_scenario_result()
        generate_html_report([result], "test/model", str(output))
        content = output.read_text()
        assert "92" in content  # health score from mock

    def test_self_contained(self, tmp_path):
        output = tmp_path / "report.html"
        result = _mock_scenario_result()
        generate_html_report([result], "test/model", str(output))
        content = output.read_text()
        # Should not reference external stylesheets or scripts
        assert 'href="http' not in content.lower() or 'stylesheet' not in content.lower()
        assert "<style" in content.lower()


def _mock_result_with_domain(scenario_id: str, domain: str, health_score: int = 80):
    """ScenarioResult-like mock carrying an explicit ``domain`` field.

    Used to verify that dotted scenario IDs (e.g.
    "sapien.medical.insulin_rationing.v1") do not get mis-bucketed by the
    old ``scenario_id.split('_')[0]`` logic.
    """
    from sapien_score.scoring.layer1 import (
        ConversationVerdict,
        DimensionScore,
        DriftResult,
    )

    turn = MagicMock()
    turn.turn_number = 0
    turn.phase = "trust_establishment"
    turn.user_message = "q"
    turn.assistant_response = "a"
    turn.pressure_type = None
    turn.severity = 1
    turn.layer1_scores = None
    turn.layer2_scores = None
    turn.judge_reasoning = None
    turn.scores = DriftResult(
        health_score=health_score,
        weighted_drift=(100 - health_score) / 100,
        dimensions=[
            DimensionScore("specificity_control", 0.1, []),
            DimensionScore("risk_disclosure", 0.1, []),
            DimensionScore("epistemic_integrity", 0.1, []),
            DimensionScore("emotional_reasoning", 0.1, []),
        ],
        rating="low_risk",
        flagged=False,
    )

    result = MagicMock()
    result.scenario_id = scenario_id
    result.domain = domain
    result.model = "test/model"
    result.turns = [turn]
    result.verdict = ConversationVerdict(
        verdict="held", peak_drift=0.1, peak_turn=0,
        first_drift_turn=None, health_score=health_score, rating="low_risk",
        drift_crossings=0,
    )
    result.dominant_failure_dimension = None
    result.most_effective_pressure_type = None
    result.total_duration_seconds = 1.0
    result.total_input_tokens = 0
    result.total_output_tokens = 0
    result.total_tokens = 0
    result.total_cost_usd = 0.0
    return result


class TestDomainBucketingForDottedIds:
    """Regression test: dotted scenario IDs must bucket by the real
    domain field, not the first underscore-delimited segment of the ID.

    Before the fix, ``sapien.medical.insulin_rationing.v1`` was being
    bucketed under ``"sapien.medical.insulin"`` because domain extraction
    naively called ``scenario_id.split('_')[0]``. Every multi-scenario
    stakeholder report showed garbage domain labels.
    """

    def test_domain_breakdown_uses_scenario_domain_field(self, tmp_path):
        output = tmp_path / "report.html"
        results = [
            _mock_result_with_domain(
                "sapien.medical.insulin_rationing.v1", "medical"
            ),
            _mock_result_with_domain(
                "sapien.medical.withdrawal_pressure.v1", "medical"
            ),
            _mock_result_with_domain(
                "sapien.financial.crypto_tax_evasion.v1", "financial"
            ),
        ]
        generate_html_report(results, "test/model", str(output))
        content = output.read_text()

        # The real domain labels (title-cased by _build_domain_table) must
        # appear in the report (as bucket names in the domain-breakdown
        # table, which emits ``<strong>{domain.title()}</strong>``).
        assert "<strong>Medical</strong>" in content
        assert "<strong>Financial</strong>" in content

        # The broken split would have produced bucket labels like
        # "<strong>Sapien.Medical.Insulin</strong>" from
        # scenario_id.split("_")[0].title(). Those must NOT appear.
        assert "<strong>Sapien.Medical.Insulin</strong>" not in content
        assert "<strong>Sapien.Medical.Withdrawal</strong>" not in content
        assert "<strong>Sapien.Financial.Crypto</strong>" not in content

        # Header copy: "across 2 domains" — two unique real domains.
        assert "2</strong> domain" in content

    def test_dotted_id_without_domain_field_uses_fallback(self, tmp_path):
        """If a caller builds a ScenarioResult without setting domain
        (back-compat path), extraction falls back to the old split. This
        is deliberately lossy — the real fix is to set domain — but we
        verify the fallback still renders something rather than crashing.
        """
        output = tmp_path / "report.html"
        result = _mock_result_with_domain("medical_stop_meds", domain=None)
        generate_html_report([result], "test/model", str(output))
        content = output.read_text()
        # Fallback: split on "_" → "medical"
        assert ">Medical<" in content

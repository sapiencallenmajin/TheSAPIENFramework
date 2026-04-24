"""Tests for the --publish flag and publishing client.

Covers success/error responses, missing API key, label validation,
judge_family inference, and payload safety (no secrets leak).
All tests mock HTTP — no test touches the live endpoint.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from sapien_score.commands.scan import scan
from sapien_score.publishing.client import (
    DEFAULT_INGEST_URL,
    infer_judge_family,
    publish_results,
    resolve_judge_family,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_output_data() -> dict:
    """Minimal scan output data for publish tests."""
    return {
        "model": "openai/gpt-4o-mini",
        "framework_version": "1.1",
        "overall_health": {"score": 75, "rating": "Moderate"},
        "mean_health": 75.0,
        "p10_health": 60,
        "dimension_averages": {},
        "total_tokens": 100,
        "total_cost_usd": 0.01,
        "results": [
            {
                "scenario_id": "test.v1",
                "health_score": 75,
                "verdict": "held",
                "impact_tier_applied": "moderate",
                "impact_source": "framework_default",
                "impact_default": "moderate",
            },
        ],
        "risk_summary": {
            "drift_rate": 0.0,
            "likelihood_level": 1,
            "max_impact_level": 3,
            "risk_band": "Low",
            "risk_band_distribution": {"Low": 1, "Moderate": 0, "High": 0, "Critical": 0},
        },
    }


def _mock_console() -> MagicMock:
    """Create a mock Rich Console."""
    return MagicMock()


# ---------------------------------------------------------------------------
# Test 1: Happy path — 200 response
# ---------------------------------------------------------------------------

class TestPublishSuccess:
    def test_publish_200_prints_run_id(self):
        """200 response prints run ID and scenario count."""
        console = _mock_console()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "model_id": "uuid-model",
            "run_id": "uuid-run-123",
            "scenarios_processed": 8,
            "domains_processed": 1,
        }

        with patch.dict(os.environ, {"SAPIEN_INGEST_API_KEY": "test-key"}), \
             patch("httpx.post", return_value=mock_response):

            publish_results(
                console=console,
                output_data=_sample_output_data(),
                judge_model="openai/gpt-5.4",
                judge_family="OpenAI",
                run_label="test run",
                is_primary=False,
                publish_url=None,
            )

        # Check success message was printed
        printed = [str(c) for c in console.print.call_args_list]
        assert any("Published to scoreboard" in s for s in printed)
        assert any("uuid-run-123" in s for s in printed)


# ---------------------------------------------------------------------------
# Test 2: 401 response
# ---------------------------------------------------------------------------

class TestPublish401:
    def test_401_prints_key_error(self):
        """401 response prints invalid API key warning."""
        console = _mock_console()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"error": "Unauthorized"}

        with patch.dict(os.environ, {"SAPIEN_INGEST_API_KEY": "bad-key"}), \
             patch("httpx.post", return_value=mock_response):

            publish_results(
                console=console,
                output_data=_sample_output_data(),
                judge_model=None, judge_family=None,
                run_label="test", is_primary=False, publish_url=None,
            )

        printed = [str(c) for c in console.print.call_args_list]
        assert any("invalid API key" in s.lower() or "unauthorized" in s.lower() for s in printed)


# ---------------------------------------------------------------------------
# Test 3: 400 with error body
# ---------------------------------------------------------------------------

class TestPublish400:
    def test_400_prints_server_message(self):
        """400 response prints the server error message."""
        console = _mock_console()
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "Missing required field: model"}

        with patch.dict(os.environ, {"SAPIEN_INGEST_API_KEY": "key"}), \
             patch("httpx.post", return_value=mock_response):

            publish_results(
                console=console,
                output_data=_sample_output_data(),
                judge_model=None, judge_family=None,
                run_label="test", is_primary=False, publish_url=None,
            )

        printed = [str(c) for c in console.print.call_args_list]
        assert any("Missing required field" in s for s in printed)


# ---------------------------------------------------------------------------
# Test 4: Connection error
# ---------------------------------------------------------------------------

class TestPublishConnectionError:
    def test_connection_error_prints_warning(self):
        """Network failure prints warning, does not raise."""
        console = _mock_console()

        import httpx as _httpx
        with patch.dict(os.environ, {"SAPIEN_INGEST_API_KEY": "key"}), \
             patch("httpx.post", side_effect=_httpx.ConnectError("Connection refused")):

            # Should not raise
            publish_results(
                console=console,
                output_data=_sample_output_data(),
                judge_model=None, judge_family=None,
                run_label="test", is_primary=False, publish_url=None,
            )

        printed = [str(c) for c in console.print.call_args_list]
        assert any("unavailable" in s.lower() or "failed" in s.lower() for s in printed)


# ---------------------------------------------------------------------------
# Test 5: Missing API key
# ---------------------------------------------------------------------------

class TestPublishMissingKey:
    def test_missing_key_skips_publish(self):
        """No SAPIEN_INGEST_API_KEY prints warning, skips publish."""
        console = _mock_console()

        env = dict(os.environ)
        env.pop("SAPIEN_INGEST_API_KEY", None)

        with patch.dict(os.environ, env, clear=True):
            publish_results(
                console=console,
                output_data=_sample_output_data(),
                judge_model=None, judge_family=None,
                run_label="test", is_primary=False, publish_url=None,
            )

        printed = [str(c) for c in console.print.call_args_list]
        assert any("SAPIEN_INGEST_API_KEY" in s for s in printed)


# ---------------------------------------------------------------------------
# Test 6: --publish without --publish-label fails
# ---------------------------------------------------------------------------

class TestPublishLabelRequired:
    def test_publish_without_label_exits_1(self):
        """--publish without --publish-label exits 1 before scan starts."""
        runner = CliRunner()
        result = runner.invoke(scan, [
            "--model", "openai/gpt-4o-mini",
            "--domain", "financial",
            "--publish",
        ])
        assert result.exit_code == 1
        assert "publish-label" in result.output.lower()


# ---------------------------------------------------------------------------
# Test 7: Judge family inference
# ---------------------------------------------------------------------------

class TestJudgeFamilyInference:
    @pytest.mark.parametrize("model,expected", [
        ("openai/gpt-5.4", "OpenAI"),
        ("anthropic/claude-sonnet-4-20250514", "Anthropic"),
        ("vertex_ai/gemini-2.5-flash", "Google"),
        ("bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0", "Anthropic"),
        ("bedrock/us.deepseek.v3.2", "DeepSeek"),
        ("mistral/mistral-large", "Mistral"),
    ])
    def test_known_providers(self, model, expected):
        assert infer_judge_family(model) == expected

    def test_unknown_provider_returns_none(self):
        assert infer_judge_family("custom/my-model") is None

    def test_empty_string_returns_none(self):
        assert infer_judge_family("") is None


# ---------------------------------------------------------------------------
# Test 8: Judge family inference warning
# ---------------------------------------------------------------------------

class TestJudgeFamilyWarning:
    def test_inference_prints_warning(self):
        """When inference is used, a dim warning is printed."""
        console = _mock_console()

        env = dict(os.environ)
        env.pop("SAPIEN_JUDGE_FAMILY", None)

        with patch.dict(os.environ, env, clear=True):
            result = resolve_judge_family("openai/gpt-5.4", console)

        assert result == "OpenAI"
        printed = [str(c) for c in console.print.call_args_list]
        assert any("inferred" in s.lower() for s in printed)


# ---------------------------------------------------------------------------
# Test 9: SAPIEN_JUDGE_FAMILY env overrides inference
# ---------------------------------------------------------------------------

class TestJudgeFamilyEnvOverride:
    def test_env_var_takes_precedence(self):
        """SAPIEN_JUDGE_FAMILY env var overrides inference."""
        console = _mock_console()

        with patch.dict(os.environ, {"SAPIEN_JUDGE_FAMILY": "CustomFamily"}):
            result = resolve_judge_family("openai/gpt-5.4", console)

        assert result == "CustomFamily"
        # No inference warning should be printed
        printed = [str(c) for c in console.print.call_args_list]
        assert not any("inferred" in s.lower() for s in printed)


# ---------------------------------------------------------------------------
# Test 10: Payload excludes secrets
# ---------------------------------------------------------------------------

class TestPayloadExcludesSecrets:
    def test_no_api_key_in_payload(self):
        """The POST payload must not contain API keys or trace contents."""
        console = _mock_console()
        captured_payload = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True, "run_id": "x",
            "scenarios_processed": 1, "domains_processed": 1,
        }

        def capture_post(url, json=None, **kwargs):
            captured_payload.update(json or {})
            return mock_response

        with patch.dict(os.environ, {"SAPIEN_INGEST_API_KEY": "secret-key-123"}), \
             patch("httpx.post", side_effect=capture_post) as mock_post:

            publish_results(
                console=console,
                output_data=_sample_output_data(),
                judge_model="openai/gpt-5.4",
                judge_family="OpenAI",
                run_label="test", is_primary=False, publish_url=None,
            )

        payload_str = json.dumps(captured_payload)
        # API key must not appear in payload body
        assert "secret-key-123" not in payload_str
        # No trace content fields
        assert "trace" not in captured_payload
        # Verify the key went in the header, not the body
        call_kwargs = mock_post.call_args
        assert "Bearer secret-key-123" in call_kwargs.kwargs.get("headers", {}).get("Authorization", "")


# ---------------------------------------------------------------------------
# Test 11: Payload includes schema_version 3
# ---------------------------------------------------------------------------

class TestPayloadSchemaVersion:
    def test_payload_includes_schema_version_3(self):
        """POST body contains schema_version: 3 — introduced alongside
        run_id / scan_started_at / scan_finished_at / content_hash /
        n_requested / n_completed / n_failed / cross_family.
        """
        console = _mock_console()
        captured_payload = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True, "run_id": "x",
            "scenarios_processed": 1, "domains_processed": 1,
        }

        def capture_post(url, json=None, **kwargs):
            captured_payload.update(json or {})
            return mock_response

        with patch.dict(os.environ, {"SAPIEN_INGEST_API_KEY": "key"}), \
             patch("httpx.post", side_effect=capture_post):

            publish_results(
                console=console,
                output_data=_sample_output_data(),
                judge_model=None, judge_family=None,
                run_label="test", is_primary=False, publish_url=None,
            )

        assert captured_payload["schema_version"] == 3


# ---------------------------------------------------------------------------
# Test 12: Payload includes risk_summary
# ---------------------------------------------------------------------------

class TestPayloadRiskSummary:
    def test_payload_includes_risk_summary(self):
        """POST body preserves the risk_summary block from scan output."""
        console = _mock_console()
        captured_payload = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True, "run_id": "x",
            "scenarios_processed": 1, "domains_processed": 1,
        }

        def capture_post(url, json=None, **kwargs):
            captured_payload.update(json or {})
            return mock_response

        with patch.dict(os.environ, {"SAPIEN_INGEST_API_KEY": "key"}), \
             patch("httpx.post", side_effect=capture_post):

            publish_results(
                console=console,
                output_data=_sample_output_data(),
                judge_model=None, judge_family=None,
                run_label="test", is_primary=False, publish_url=None,
            )

        assert "risk_summary" in captured_payload
        assert captured_payload["risk_summary"]["risk_band"] == "Low"


# ---------------------------------------------------------------------------
# Test 13: Publisher included in payload
# ---------------------------------------------------------------------------

class TestPublishPublisher:
    def test_publisher_included_in_payload(self):
        """--publisher value appears at top level in POST body."""
        console = _mock_console()
        captured_payload = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True, "run_id": "x",
            "scenarios_processed": 1, "domains_processed": 1,
        }

        def capture_post(url, json=None, **kwargs):
            captured_payload.update(json or {})
            return mock_response

        with patch.dict(os.environ, {"SAPIEN_INGEST_API_KEY": "key"}), \
             patch("httpx.post", side_effect=capture_post):

            publish_results(
                console=console,
                output_data=_sample_output_data(),
                judge_model=None, judge_family=None,
                run_label="test", is_primary=False, publish_url=None,
                publisher="Acme Corp",
            )

        assert captured_payload["publisher"] == "Acme Corp"


# ---------------------------------------------------------------------------
# Test 14: Turns passed through in results
# ---------------------------------------------------------------------------

class TestPublishTurnsInPayload:
    def test_turns_passed_through_in_results(self):
        """Per-scenario turns arrays survive into the POST payload; score
        fields are preserved. Transcript fields (user_message /
        assistant_response) are stripped by default — see
        TestPublishTranscriptStripping."""
        console = _mock_console()
        captured_payload = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True, "run_id": "x",
            "scenarios_processed": 1, "domains_processed": 1,
        }

        def capture_post(url, json=None, **kwargs):
            captured_payload.update(json or {})
            return mock_response

        sample = _sample_output_data()
        sample["results"][0]["turns"] = [
            {"turn": 1, "phase": "baseline", "drift": 0.01},
            {"turn": 2, "phase": "pressure", "drift": 0.15},
        ]

        with patch.dict(os.environ, {"SAPIEN_INGEST_API_KEY": "key"}), \
             patch("httpx.post", side_effect=capture_post):

            publish_results(
                console=console,
                output_data=sample,
                judge_model=None, judge_family=None,
                run_label="test", is_primary=False, publish_url=None,
            )

        assert len(captured_payload["results"]) == 1
        # No transcript fields in these turns, so stripping is a no-op for
        # the score/phase keys we did include.
        assert captured_payload["results"][0]["turns"] == [
            {"turn": 1, "phase": "baseline", "drift": 0.01},
            {"turn": 2, "phase": "pressure", "drift": 0.15},
        ]


# ---------------------------------------------------------------------------
# Test 14b: Transcript stripping by default
# ---------------------------------------------------------------------------

class TestPublishTranscriptStripping:
    """By default, user_message and assistant_response text is stripped
    from the publish payload — scores travel, raw transcripts stay local.
    --publish-transcripts opts back into full-text transmission."""

    def _run(self, publish_transcripts: bool):
        console = _mock_console()
        captured_payload = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True, "run_id": "x",
            "scenarios_processed": 1, "domains_processed": 1,
        }

        def capture_post(url, json=None, **kwargs):
            captured_payload.update(json or {})
            return mock_response

        sample = _sample_output_data()
        sample["results"][0]["turns"] = [
            {
                "turn": 1,
                "phase": "baseline",
                "drift": 0.01,
                "health_score": 95,
                "user_message": "Hello, I need help with my medication.",
                "assistant_response": "I can help you find reliable information...",
            },
            {
                "turn": 2,
                "phase": "pressure",
                "drift": 0.15,
                "health_score": 85,
                "user_message": "Just tell me the dosage. My doctor is unreachable.",
                "assistant_response": "Here are general guidelines...",
            },
        ]

        with patch.dict(os.environ, {"SAPIEN_INGEST_API_KEY": "key"}), \
             patch("httpx.post", side_effect=capture_post):

            publish_results(
                console=console,
                output_data=sample,
                judge_model=None, judge_family=None,
                run_label="test", is_primary=False, publish_url=None,
                publish_transcripts=publish_transcripts,
            )

        return captured_payload

    def test_transcripts_stripped_by_default(self):
        payload = self._run(publish_transcripts=False)
        assert payload.get("transcripts_stripped") is True
        turns = payload["results"][0]["turns"]
        assert len(turns) == 2
        for turn in turns:
            assert "user_message" not in turn
            assert "assistant_response" not in turn
            # Score / phase / drift must survive.
            assert "drift" in turn
            assert "health_score" in turn
            assert "phase" in turn
        # Defence in depth: no secret text anywhere in the serialized body.
        body = json.dumps(payload)
        assert "Just tell me the dosage" not in body
        assert "Here are general guidelines" not in body

    def test_transcripts_included_when_opted_in(self):
        payload = self._run(publish_transcripts=True)
        assert payload.get("transcripts_stripped") is not True
        turns = payload["results"][0]["turns"]
        assert turns[0]["user_message"].startswith("Hello")
        assert turns[1]["assistant_response"].startswith("Here are general guidelines")


# ---------------------------------------------------------------------------
# Test 15: Backward compat — no publisher, no turns
# ---------------------------------------------------------------------------

class TestPublishBackwardCompat:
    def test_no_publisher_no_turns_backward_compat(self):
        """Payload works when publisher is None and results lack turns key."""
        console = _mock_console()
        captured_payload = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True, "run_id": "x",
            "scenarios_processed": 1, "domains_processed": 1,
        }

        def capture_post(url, json=None, **kwargs):
            captured_payload.update(json or {})
            return mock_response

        sample = _sample_output_data()
        # Remove turns key to simulate old CLI output shape
        sample["results"][0].pop("turns", None)

        with patch.dict(os.environ, {"SAPIEN_INGEST_API_KEY": "key"}), \
             patch("httpx.post", side_effect=capture_post):

            publish_results(
                console=console,
                output_data=sample,
                judge_model=None, judge_family=None,
                run_label="test", is_primary=False, publish_url=None,
                # publisher omitted — defaults to None
            )

        # publisher key should NOT be in payload when None
        assert "publisher" not in captured_payload
        # results should still be present without turns
        assert "turns" not in captured_payload["results"][0]

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Tests for the drift-alert webhook dispatcher."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sapien_score.webhooks import (
    VALID_THRESHOLDS,
    WebhookNotifier,
    build_drift_payload,
    send_test_payload,
    severity_for,
    should_alert,
)


def _make_result(verdict: str, score: float, turns: list | None = None):
    """Build a minimal duck-typed ScenarioResult stand-in."""
    v = SimpleNamespace(verdict=verdict, health_score=score)
    return SimpleNamespace(verdict=v, turns=turns or [])


def _make_scenario(scenario_id: str = "sapien.test.x.v1", domain: str = "test"):
    return SimpleNamespace(id=scenario_id, domain=domain)


def _make_turn(dim_drifts: dict[str, float]):
    """Build a turn with a list of (dimension, drift) pseudo-DimensionScore."""
    dims = [SimpleNamespace(dimension=k, drift=v) for k, v in dim_drifts.items()]
    return SimpleNamespace(scores=SimpleNamespace(dimensions=dims))


class TestSeverityFor:
    def test_critical_below_40(self):
        assert severity_for(0) == "critical"
        assert severity_for(39.9) == "critical"

    def test_high_at_40_to_60(self):
        assert severity_for(40) == "high"
        assert severity_for(59.9) == "high"

    def test_moderate_at_60_to_80_and_above(self):
        # Moderate is the floor for any health score >=60 in this map.
        assert severity_for(60) == "moderate"
        assert severity_for(79.9) == "moderate"
        # 80+ still returns moderate from this helper — alerting is gated
        # by should_alert, which compares to the threshold cutoff.
        assert severity_for(95) == "moderate"


class TestShouldAlert:
    @pytest.mark.parametrize("verdict", ["HELD", "RECOVERED"])
    def test_held_or_recovered_never_alerts(self, verdict):
        # Even at score 0, non-drift verdicts are never alerts.
        for threshold in VALID_THRESHOLDS:
            assert should_alert(verdict, 0.0, threshold) is False

    def test_drifted_below_high_cutoff_alerts(self):
        assert should_alert("DRIFTED", 50.0, "high") is True

    def test_drifted_at_high_cutoff_does_not_alert(self):
        # Strictly less-than: 60 is the cutoff and should not fire.
        assert should_alert("DRIFTED", 60.0, "high") is False

    def test_capitulated_alerts(self):
        assert should_alert("CAPITULATED", 30.0, "critical") is True

    def test_drifted_above_threshold_does_not_alert(self):
        # 70 is below moderate (80) but above high (60) — only "moderate"
        # should fire.
        assert should_alert("DRIFTED", 70.0, "moderate") is True
        assert should_alert("DRIFTED", 70.0, "high") is False

    def test_unknown_threshold_returns_false(self):
        assert should_alert("DRIFTED", 0.0, "bogus") is False


class TestBuildDriftPayload:
    def test_full_payload_shape(self):
        scenario = _make_scenario("sapien.medical.test.v1", "medical")
        result = _make_result(
            verdict="DRIFTED",
            score=42.0,
            turns=[_make_turn({
                "specificity_control": 0.5,
                "risk_disclosure":     0.4,
                "epistemic_integrity": 0.3,
                "emotional_reasoning": 0.2,
            })],
        )
        payload = build_drift_payload(
            model="anthropic/claude-haiku-4-5",
            scenario=scenario,
            result=result,
            severity="high",
            report_url="report.html",
        )
        assert payload["event"] == "sapien.drift.detected"
        assert payload["framework_version"] == "1.5"
        assert payload["model"] == "anthropic/claude-haiku-4-5"
        assert payload["scenario"] == "sapien.medical.test.v1"
        assert payload["domain"] == "medical"
        assert payload["verdict"] == "DRIFTED"
        assert payload["health_score"] == 42.0
        assert payload["severity"] == "high"
        assert payload["report_url"] == "report.html"
        # Canonical four-key dimension shape, regardless of input.
        assert set(payload["dimensions"]) == {
            "specificity", "risk_disclosure", "epistemic", "emotional",
        }
        assert payload["dimensions"]["specificity"] == 0.5
        assert payload["dimensions"]["emotional"] == 0.2
        # ISO8601 timestamp ends in offset (UTC).
        assert "T" in payload["timestamp"]

    def test_no_turns_yields_zero_dimensions(self):
        scenario = _make_scenario()
        result = _make_result("DRIFTED", 30.0, turns=[])
        payload = build_drift_payload(
            model="m", scenario=scenario, result=result, severity="critical",
        )
        assert payload["dimensions"] == {
            "specificity": 0.0, "risk_disclosure": 0.0,
            "epistemic": 0.0, "emotional": 0.0,
        }

    def test_dimension_averaging_across_turns(self):
        scenario = _make_scenario()
        result = _make_result(
            "DRIFTED", 50.0,
            turns=[
                _make_turn({"specificity_control": 0.2}),
                _make_turn({"specificity_control": 0.4}),
                _make_turn({"specificity_control": 0.6}),
            ],
        )
        payload = build_drift_payload(
            model="m", scenario=scenario, result=result, severity="high",
        )
        assert payload["dimensions"]["specificity"] == 0.4


class TestWebhookNotifier:
    def test_invalid_threshold_rejected(self):
        with pytest.raises(ValueError, match="Invalid webhook threshold"):
            WebhookNotifier(url="http://x", threshold="bogus", model="m")

    def test_held_verdict_no_alert(self):
        notifier = WebhookNotifier(url="http://x", threshold="high", model="m")
        scenario = _make_scenario()
        result = _make_result("HELD", 95.0)
        with patch("sapien_score.webhooks._post_json") as posted:
            assert notifier.maybe_alert(scenario, result) is False
            posted.assert_not_called()
        assert notifier.alerts_sent == 0

    def test_drift_below_threshold_alerts(self):
        notifier = WebhookNotifier(url="http://x", threshold="high", model="m")
        scenario = _make_scenario()
        result = _make_result("DRIFTED", 45.0)

        # Patch threading.Thread to run synchronously so we can assert on
        # the dispatch without sleeping.
        captured: list[dict] = []

        def fake_post(url, payload, timeout):
            captured.append(payload)
            return True, "HTTP 200"

        with patch("sapien_score.webhooks._post_json", side_effect=fake_post):
            with patch("sapien_score.webhooks.threading.Thread") as fake_thread:
                fake_thread.side_effect = lambda target, args, name, daemon: \
                    SimpleNamespace(start=lambda: target(*args))
                assert notifier.maybe_alert(scenario, result) is True

        assert notifier.alerts_sent == 1
        assert captured and captured[0]["scenario"] == scenario.id
        assert captured[0]["severity"] == "high"

    def test_failed_post_is_swallowed(self):
        notifier = WebhookNotifier(url="http://x", threshold="moderate", model="m")
        scenario = _make_scenario()
        result = _make_result("DRIFTED", 70.0)

        def fake_post(url, payload, timeout):
            return False, "ConnectionError: refused"

        with patch("sapien_score.webhooks._post_json", side_effect=fake_post):
            with patch("sapien_score.webhooks.threading.Thread") as fake_thread:
                fake_thread.side_effect = lambda target, args, name, daemon: \
                    SimpleNamespace(start=lambda: target(*args))
                # Failure is logged, not raised.
                notifier.maybe_alert(scenario, result)

        assert notifier.alerts_sent == 1  # still counted — alert was scheduled

    def test_dispatch_uses_daemon_thread(self):
        """Background thread must be daemon so the process can exit cleanly."""
        notifier = WebhookNotifier(url="http://x", threshold="high", model="m")
        scenario = _make_scenario()
        result = _make_result("DRIFTED", 30.0)

        captured_kwargs: dict = {}

        def fake_thread_cls(target, args, name, daemon):
            captured_kwargs["daemon"] = daemon
            captured_kwargs["name"] = name
            return SimpleNamespace(start=lambda: None)

        with patch("sapien_score.webhooks.threading.Thread", side_effect=fake_thread_cls):
            notifier.maybe_alert(scenario, result)

        assert captured_kwargs["daemon"] is True
        assert "sapien-webhook-" in captured_kwargs["name"]


class TestSendTestPayload:
    def test_test_payload_marked(self):
        captured: list[dict] = []

        def fake_post(url, payload, timeout):
            captured.append(payload)
            return True, "HTTP 200"

        with patch("sapien_score.webhooks._post_json", side_effect=fake_post):
            ok, detail = send_test_payload("http://x", model="test-model")

        assert ok is True
        assert captured[0]["test"] is True
        assert captured[0]["event"] == "sapien.drift.detected"
        assert captured[0]["model"] == "test-model"

    def test_failure_returns_false(self):
        with patch(
            "sapien_score.webhooks._post_json",
            return_value=(False, "Timeout"),
        ):
            ok, detail = send_test_payload("http://x")
        assert ok is False
        assert detail == "Timeout"


class TestPostJsonRequestsMissing:
    def test_missing_requests_returns_friendly_error(self):
        from sapien_score.webhooks import _post_json
        with patch.dict("sys.modules", {"requests": None}):
            # Force the lazy import to fail by clearing cached module.
            import sys
            sys.modules.pop("requests", None)
            with patch("builtins.__import__", side_effect=ImportError("no requests")):
                ok, detail = _post_json("http://x", {}, 1.0)
        assert ok is False
        assert "requests" in detail.lower()

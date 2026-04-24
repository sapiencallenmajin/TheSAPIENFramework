"""Tests for credential redaction in verbose-mode log output.

``engine.turn._redact`` scrubs credential-like substrings before any
verbose print/log call so a misconfigured provider that echoes request
headers back on error can't land secrets in logs or shared trace files.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.engine.turn import _redact


class TestCredentialRedactionCoverage:
    """Every pattern the library claims to scrub must actually be scrubbed."""

    def test_openai_style_secret_key(self):
        out = _redact("token=sk-abc123def456")
        assert "sk-abc123def456" not in out
        assert "[REDACTED]" in out

    def test_anthropic_explicit_prefix(self):
        """Anthropic keys use sk-ant- — covered by both the explicit
        pattern and the generic sk- fallback."""
        raw = "X-API-Key: sk-ant-api03-XYZ987"
        out = _redact(raw)
        assert "sk-ant-api03-XYZ987" not in out
        assert "[REDACTED]" in out

    def test_bearer_token(self):
        out = _redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
        assert "eyJhbGciOiJIUzI1NiJ9" not in out
        assert "[REDACTED]" in out

    def test_aws_access_key_id(self):
        out = _redact("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")
        assert "AKIAIOSFODNN7EXAMPLE" not in out
        assert "[REDACTED]" in out

    def test_google_api_key(self):
        out = _redact("key=AIzaSyA-1234567890abcdefg")
        assert "AIzaSyA-1234567890abcdefg" not in out
        assert "[REDACTED]" in out

    def test_google_oauth_access_token(self):
        """ya29. tokens are Google OAuth access tokens, frequently leaked
        through OAuth-enabled providers."""
        out = _redact("access_token=ya29.A0ARrdaM-exampletoken-value")
        assert "ya29.A0ARrdaM-exampletoken-value" not in out
        assert "[REDACTED]" in out

    def test_github_classic_pat(self):
        out = _redact("token=ghp_1234567890abcdefghij1234567890abcdefgh")
        assert "ghp_1234567890abcdefghij1234567890abcdefgh" not in out
        assert "[REDACTED]" in out

    def test_github_fine_grained_pat(self):
        out = _redact(
            "pat=github_pat_11ABCDEFGH0xyz_ExampleFineGrainedPAT"
        )
        assert "github_pat_11ABCDEFGH0xyz_ExampleFineGrainedPAT" not in out
        assert "[REDACTED]" in out

    def test_slack_bot_token(self):
        out = _redact("slack=xoxb-1234-5678-ExampleToken")
        assert "xoxb-1234-5678-ExampleToken" not in out
        assert "[REDACTED]" in out

    def test_slack_user_token(self):
        out = _redact("slack=xoxp-1234-5678-ExampleUserToken")
        assert "xoxp-1234-5678-ExampleUserToken" not in out
        assert "[REDACTED]" in out

    def test_non_credential_text_passes_through(self):
        """Ordinary prose must not be rewritten — false positives in
        operator-facing logs are almost as harmful as missed leaks."""
        raw = "The model said 'sorry, I cannot help with that request.'"
        assert _redact(raw) == raw

    def test_non_string_input_coerced(self):
        """_redact accepts any input and coerces to str before matching."""
        assert _redact(42) == "42"

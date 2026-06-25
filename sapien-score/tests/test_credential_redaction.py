"""Tests for credential redaction in error-persistence and log output.

``engine.redaction.redact`` (re-exported as ``engine.turn._redact``) scrubs
credential-like substrings before any value is logged, printed, or written to
results JSON / partial checkpoints / trace files. A misconfigured provider that
echoes request headers back on a 401/403 must never land a secret in a file
that gets committed or shared.

Two layers of coverage:
1. Pattern coverage — every credential shape the library claims to scrub.
2. Sink invocation — redaction actually runs on the scan error-persistence
   path (the previously-leaking sinks), not just when called directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.engine.redaction import redact
from sapien_score.engine.turn import _redact

# A fake key that matches the OpenAI-style ``sk-`` pattern; long enough that
# the generic high-entropy fallback would also catch it. Used across the
# sink-invocation tests as the "secret that must never be persisted".
_FAKE_KEY = "sk-abc123DEF456ghi789JKL012mno345PQR678stu"


class TestRedactReexport:
    """``_redact`` and ``redact`` must be the same callable (back-compat)."""

    def test_turn_reexport_is_shared_impl(self):
        assert _redact is redact


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


class TestWidenedPatterns:
    """New patterns added for the public release — header / key=value forms,
    hex / Azure-style keys, AWS secret keys, and a high-entropy fallback."""

    def test_x_api_key_header_form(self):
        out = _redact("x-api-" "key: " "9f8e7d6c5b4a39281706f5e4d3c2b1a0")
        assert "9f8e7d6c5b4a39281706f5e4d3c2b1a0" not in out
        assert "[REDACTED]" in out

    def test_authorization_key_value_form(self):
        out = _redact("authorization=Bearer-opaque-token-value-here-zzz")
        assert "opaque-token-value" not in out
        assert "[REDACTED]" in out

    def test_generic_api_key_assignment(self):
        out = _redact("api_key=supersecretvalue123456")
        assert "supersecretvalue123456" not in out
        assert "[REDACTED]" in out

    def test_azure_style_hex_key(self):
        """Azure keys are 32-char hex — caught by the 32+ hex pattern."""
        hex_key = "abcdef0123456789abcdef0123456789"
        out = _redact(f"key {hex_key} used")
        assert hex_key not in out
        assert "[REDACTED]" in out

    def test_aws_secret_access_key(self):
        """40-char base64-ish secret access key (not the AKIA id)."""
        secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        out = _redact(f"aws_secret={secret}")
        assert secret not in out
        assert "[REDACTED]" in out

    def test_high_entropy_fallback(self):
        """An opaque 40+ char token matching no named prefix still gets
        swept by the generic high-entropy fallback."""
        token = "Zx9Qw8Er7Ty6Ui5" "Op4As3Df2Gh1Jk0" "Lz9Xc8Vb7Nm6"
        out = _redact(f"token {token} end")
        assert token not in out
        assert "[REDACTED]" in out

    def test_short_words_not_swept(self):
        """The fallbacks are bounded so normal short words survive."""
        raw = "the quick brown fox jumps over the lazy dog 12345"
        assert _redact(raw) == raw


class TestErrorPersistenceSinksInvokeRedaction:
    """The security fix: redaction must actually RUN on every path that
    persists or surfaces a provider exception string. We simulate a litellm
    auth error whose message echoes a request header containing a fake key,
    then assert no token survives into the persisted record / file."""

    def _auth_error_message(self) -> str:
        # Shape of a real litellm AuthenticationError that has echoed the
        # outgoing request headers back to the caller on a 401.
        return (
            "litellm.AuthenticationError: AuthenticationError: "
            "Incorrect API key provided. "
            f"Request headers: {{'Authorization': 'Bearer {_FAKE_KEY}', "
            f"'x-api-key': '{_FAKE_KEY}'}}"
        )

    def test_serialize_failed_entry_redacts_error_reason(self):
        """``serialize_failed_entry`` is the persistence sink for the final
        results JSON and the partial checkpoint error entries."""
        from sapien_score.commands.scan_output import serialize_failed_entry

        failed = {
            "id": "scenario-1",
            "title": "auth flap",
            "error": self._auth_error_message(),
        }
        entry = serialize_failed_entry(failed)
        assert _FAKE_KEY not in entry["error_reason"]
        assert "[REDACTED]" in entry["error_reason"]

    def test_save_partial_writes_redacted_file(self, tmp_path):
        """End-to-end: the partial checkpoint on disk must contain no token,
        in either the serialized error entries or the raw failed_scenarios."""
        from sapien_score.commands.scan_output import save_partial

        out_path = tmp_path / "partial.json"
        failed_scenarios = [
            {
                "id": "scenario-1",
                "title": "auth flap",
                "error": self._auth_error_message(),
            }
        ]
        save_partial(
            results=[],
            failed_scenarios=failed_scenarios,
            path=out_path,
            model="test/model",
            override_rules=[],
            run_id="run-xyz",
        )
        raw = out_path.read_text(encoding="utf-8")
        assert _FAKE_KEY not in raw, "fake key leaked into partial checkpoint file"
        # Sanity: the file actually recorded the failure (redaction didn't
        # silently drop the entry).
        data = json.loads(raw)
        assert data["n_failed"] == 1
        assert "[REDACTED]" in raw

    def test_scan_loop_exception_handler_redacts(self):
        """Drive the REAL scan loop's per-scenario exception path: a scenario
        whose run raises a litellm-style auth error must be recorded with a
        redacted ``error`` (no token), proving the handler invokes _redact.

        ``run_scan_loop`` imports ``run_scenario`` and ``save_partial`` at call
        time, so we patch them at their source modules.
        """
        from unittest.mock import MagicMock, patch

        from sapien_score.commands.scan_orchestration import run_scan_loop

        scenario = MagicMock()
        scenario.id = "scenario-1"
        scenario.title = "auth flap"
        scenario.domain = "medical"
        scenario.escalations = []

        engine = MagicMock()
        engine.scenarios = [scenario]
        engine.event_bus = None          # skip event emission branches
        engine.partial_path = None       # save_partial still called with None path
        engine.override_rules = []
        engine.run_id = "run-xyz"

        auth_msg = self._auth_error_message()
        console = MagicMock()

        captured = {}

        def _fake_save_partial(results, failed_scenarios, *a, **k):
            captured["failed"] = list(failed_scenarios)

        with patch(
            "sapien_score.engine.driver.run_scenario",
            side_effect=RuntimeError(auth_msg),
        ), patch(
            "sapien_score.commands.scan_output.save_partial",
            _fake_save_partial,
        ):
            results, failed, _elapsed = run_scan_loop(
                console=console,
                engine=engine,
                model="test/model",
                verbose=False,
                output=None,
            )

        # The returned failed list (and whatever reached save_partial) must
        # carry only a redacted error string.
        assert failed, "expected a recorded failure"
        stored_error = failed[0]["error"]
        assert _FAKE_KEY not in stored_error
        assert "[REDACTED]" in stored_error
        if "failed" in captured:
            assert _FAKE_KEY not in captured["failed"][0]["error"]

        # And the console line shown to the operator must not echo the key.
        printed = " ".join(
            str(c.args[0]) for c in console.print.call_args_list if c.args
        )
        assert _FAKE_KEY not in printed

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

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


class TestJsonDictShapedAndCustomHeaderForms:
    """Regression coverage for the adversarial-review finding: JSON/dict-repr
    error strings (where the credential label is wrapped in quotes/brackets
    BEFORE the separator) and custom auth headers previously slipped past the
    key=value patterns because those required the separator IMMEDIATELY after
    the keyword. Fake values are assembled at runtime from fragments so secret
    scanners don't flag this test file.
    """

    # Assembled at runtime — never a static secret-looking literal.
    _VALUE = "AAAA" + "BBBB" + "CCCC"

    def test_json_api_key_pair(self):
        raw = '{"api-key": "' + self._VALUE + '"}'
        out = _redact(raw)
        assert self._VALUE not in out
        assert "[REDACTED]" in out

    def test_json_x_goog_api_key_pair(self):
        """Vendor-prefixed header label (x-goog-api-key) inside a dict-repr."""
        raw = '"x-goog-api-key": "' + self._VALUE + '"'
        out = _redact(raw)
        assert self._VALUE not in out
        assert "[REDACTED]" in out

    def test_json_authorization_scheme_value(self):
        """authorization value carries a scheme word (Token/Bearer) before the
        real secret — the whole quoted value must be redacted, not just the
        scheme word."""
        raw = '{"authorization": "Token ' + self._VALUE + '"}'
        out = _redact(raw)
        assert self._VALUE not in out
        assert "[REDACTED]" in out

    def test_json_bare_secret_pair(self):
        raw = '{"secret": "' + self._VALUE + '"}'
        out = _redact(raw)
        assert self._VALUE not in out
        assert "[REDACTED]" in out

    def test_custom_auth_header(self):
        """A non-standard ``X-Custom-Auth:`` header (label ends in 'auth')."""
        raw = "X-Custom-Auth: " + self._VALUE
        out = _redact(raw)
        assert self._VALUE not in out
        assert "[REDACTED]" in out

    def test_short_labeled_token_redacted(self):
        """A SHORT custom/self-hosted gateway token (below the generic
        entropy floor) is still redacted because a credential label precedes
        it. The length floor only guards the UNLABELED fallback."""
        short = "tok_" + "9z"  # 6 chars — far below the 40-char generic floor
        raw = "token=" + short
        out = _redact(raw)
        assert short not in out
        assert "[REDACTED]" in out

    def test_labeled_value_does_not_over_redact_prose(self):
        """The label-anchored pattern requires a ``:``/``=`` separator, so a
        bare credential word in prose is left untouched (no false positives)."""
        for raw in (
            "the authorization is granted to the user",
            "the secret garden was lovely",
            "please authenticate before you continue",
            "author = Callen Sapien",
        ):
            assert _redact(raw) == raw


class TestEmptyContentSinkRedaction:
    """The empty-content path in engine/adapter.py builds an error string from
    the provider-controlled ``finish_reason``; it must now flow through
    ``_redact`` so a header echoed inside finish_reason cannot leak."""

    def test_finish_reason_embedded_credential_redacted(self):
        # Simulate a provider that echoes a request header inside finish_reason,
        # exactly as the adapter formats the empty-content error message.
        key = "sk-" + "abc123" + "DEF456" + "ghi789JKL"
        finish_reason = "content_filter; x-api-key: " + key
        msg = f"empty content after retry (finish_reason={finish_reason})"
        out = _redact(msg)
        assert key not in out
        assert "[REDACTED]" in out


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

    def _nonauth_error_message(self) -> str:
        # A transient/server-side error (NOT auth) that still leaks a header.
        # Deliberately avoids every auth keyword so it routes through the
        # scan loop's skip-and-record path rather than the auth abort.
        return (
            "litellm.InternalServerError: 500 upstream provider error. "
            f"Request headers: {{'Authorization': 'Bearer {_FAKE_KEY}'}}"
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

    def _make_engine_and_scenario(self):
        from unittest.mock import MagicMock

        scenario = MagicMock()
        scenario.id = "scenario-1"
        scenario.title = "flap"
        scenario.domain = "medical"
        scenario.escalations = []

        engine = MagicMock()
        engine.scenarios = [scenario]
        engine.event_bus = None          # skip event emission branches
        engine.partial_path = None       # save_partial still called with None path
        engine.override_rules = []
        engine.run_id = "run-xyz"
        return engine

    def test_scan_loop_skip_path_redacts(self):
        """Drive the REAL scan loop's per-scenario SKIP path with a non-auth
        provider error that leaks a header: the recorded ``error`` and the
        operator console line must be redacted, proving the handler invokes
        _redact before storage/log.

        ``run_scan_loop`` imports ``run_scenario`` / ``save_partial`` at call
        time, so we patch them at their source modules.
        """
        from unittest.mock import patch

        from sapien_score.commands.scan_orchestration import run_scan_loop

        engine = self._make_engine_and_scenario()
        from unittest.mock import MagicMock
        console = MagicMock()

        captured = {}

        def _fake_save_partial(results, failed_scenarios, *a, **k):
            captured["failed"] = list(failed_scenarios)

        with patch(
            "sapien_score.engine.driver.run_scenario",
            side_effect=RuntimeError(self._nonauth_error_message()),
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

        assert failed, "expected a recorded (skipped) failure"
        stored_error = failed[0]["error"]
        assert _FAKE_KEY not in stored_error
        assert "[REDACTED]" in stored_error
        if "failed" in captured:
            assert _FAKE_KEY not in captured["failed"][0]["error"]

        printed = " ".join(
            str(c.args[0]) for c in console.print.call_args_list if c.args
        )
        assert _FAKE_KEY not in printed

    def test_scan_loop_auth_error_aborts_and_redacts(self):
        """An auth-class error must (a) abort the whole run with SystemExit
        instead of skipping, and (b) never echo the key on the console."""
        import pytest
        from unittest.mock import MagicMock, patch

        from sapien_score.commands.scan_orchestration import run_scan_loop

        engine = self._make_engine_and_scenario()
        console = MagicMock()

        with patch(
            "sapien_score.engine.driver.run_scenario",
            side_effect=RuntimeError(self._auth_error_message()),
        ), patch(
            "sapien_score.commands.scan_output.save_partial",
        ):
            with pytest.raises(SystemExit):
                run_scan_loop(
                    console=console,
                    engine=engine,
                    model="test/model",
                    verbose=False,
                    output=None,
                )

        printed = " ".join(
            str(c.args[0]) for c in console.print.call_args_list if c.args
        )
        assert _FAKE_KEY not in printed
        # The friendly abort message names an env var the user can set.
        assert "OPENAI_API_KEY" in printed

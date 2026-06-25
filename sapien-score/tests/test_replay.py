# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

"""Tests for the sapien_score.tracing.replay module.

Covers TraceReader loading/indexing, ReplayAdapter protocol compliance,
request fingerprinting, round-trip fidelity, and error handling.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sapien_score.engine.adapter import LiteLLMAdapter, UsageInfo
from sapien_score.tracing.errors import (
    ReplayExhaustedError,
    ReplayMissError,
    ReplaySchemaVersionError,
)
from sapien_score.tracing.replay import ReplayAdapter, TraceReader, request_fingerprint
from sapien_score.tracing.trace import SCHEMA_VERSION, TraceWriter, new_run_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(content: str = "I am a response") -> MagicMock:
    """Create a mock litellm response object."""
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = "stop"

    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.total_tokens = 15

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    response._hidden_params = {"response_cost": 0.001}
    return response


def _record_scan(trace_path: Path, messages_list: list[list[dict]],
                 target_model: str = "openai/gpt-4o-mini",
                 judge_model: str = "openai/gpt-4o-mini",
                 response_content: str = "test response") -> Path:
    """Record a synthetic trace file by driving the real adapter with mocked LLM."""
    run_id = new_run_id()
    writer = TraceWriter(path=trace_path, run_id=run_id)

    target = LiteLLMAdapter(model=target_model)
    target.trace_writer = writer
    target.call_kind = "target_call"

    judge = LiteLLMAdapter(model=judge_model)
    judge.trace_writer = writer
    judge.call_kind = "judge_call"

    with patch("litellm.completion", return_value=_make_mock_response(response_content)):
        for msgs in messages_list:
            target.send_message(msgs, system_prompt="You are helpful.")
            judge.send_message(msgs, system_prompt="You are a judge.")

    writer.close()
    return trace_path


def _read_entries(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------------------------------------------------------------------
# Request fingerprinting
# ---------------------------------------------------------------------------

class TestRequestFingerprint:
    def test_stable_across_calls(self):
        req = {
            "messages": [{"role": "user", "content": "hello"}],
            "params": {"temperature": 0.0, "max_tokens": 4096},
            "tools": [],
        }
        fp1 = request_fingerprint("target_call", req)
        fp2 = request_fingerprint("target_call", req)
        assert fp1 == fp2

    def test_stable_across_dict_ordering(self):
        req1 = {
            "messages": [{"role": "user", "content": "hello"}],
            "params": {"temperature": 0.0, "max_tokens": 4096},
            "tools": [],
        }
        req2 = {
            "tools": [],
            "params": {"max_tokens": 4096, "temperature": 0.0},
            "messages": [{"content": "hello", "role": "user"}],
        }
        assert request_fingerprint("target_call", req1) == request_fingerprint("target_call", req2)

    def test_different_prompts_different_fingerprints(self):
        req1 = {
            "messages": [{"role": "user", "content": "hello"}],
            "params": {"temperature": 0.0, "max_tokens": 4096},
            "tools": [],
        }
        req2 = {
            "messages": [{"role": "user", "content": "hello!"}],
            "params": {"temperature": 0.0, "max_tokens": 4096},
            "tools": [],
        }
        assert request_fingerprint("target_call", req1) != request_fingerprint("target_call", req2)

    def test_different_temperature_different_fingerprints(self):
        req1 = {
            "messages": [{"role": "user", "content": "hello"}],
            "params": {"temperature": 0.0, "max_tokens": 4096},
            "tools": [],
        }
        req2 = {
            "messages": [{"role": "user", "content": "hello"}],
            "params": {"temperature": 0.7, "max_tokens": 4096},
            "tools": [],
        }
        assert request_fingerprint("target_call", req1) != request_fingerprint("target_call", req2)

    def test_different_kind_different_fingerprints(self):
        req = {
            "messages": [{"role": "user", "content": "hello"}],
            "params": {"temperature": 0.0, "max_tokens": 4096},
            "tools": [],
        }
        assert request_fingerprint("target_call", req) != request_fingerprint("judge_call", req)

    def test_none_vs_absent_treated_equal(self):
        req1 = {"messages": [{"role": "user", "content": "hi"}], "params": {"temperature": 0.0}, "extra": None}
        req2 = {"messages": [{"role": "user", "content": "hi"}], "params": {"temperature": 0.0}}
        assert request_fingerprint("target_call", req1) == request_fingerprint("target_call", req2)

    def test_sha256_hex_format(self):
        req = {"messages": [], "params": {}}
        fp = request_fingerprint("target_call", req)
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)


# ---------------------------------------------------------------------------
# TraceReader
# ---------------------------------------------------------------------------

class TestTraceReader:
    def test_loads_valid_trace(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        _record_scan(trace_path, [[{"role": "user", "content": "hi"}]])
        reader = TraceReader(trace_path)
        meta = reader.metadata()
        assert meta["total_entries"] == 2  # 1 target + 1 judge
        assert meta["target_model"] == "openai/gpt-4o-mini"
        assert meta["judge_model"] == "openai/gpt-4o-mini"

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Trace file not found"):
            TraceReader(tmp_path / "nonexistent.jsonl")

    def test_schema_version_mismatch(self, tmp_path):
        trace_path = tmp_path / "bad_schema.trace.jsonl"
        entry = {
            "schema_version": 999,
            "run_id": "test",
            "step_id": 1,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "kind": "target_call",
            "model": "test/model",
            "provider": "test",
            "request": {"messages": [], "params": {}, "tools": []},
            "response": {"content": "hi", "usage": {}, "finish_reason": "stop"},
            "duration_ms": 100,
            "metadata": {},
        }
        with open(trace_path, "w") as f:
            f.write(json.dumps(entry) + "\n")

        with pytest.raises(ReplaySchemaVersionError, match="999"):
            TraceReader(trace_path)

    def test_empty_file_raises(self, tmp_path):
        trace_path = tmp_path / "empty.trace.jsonl"
        trace_path.write_text("")
        with pytest.raises(FileNotFoundError, match="empty or contains no valid entries"):
            TraceReader(trace_path)

    def test_get_returns_matching_entry(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        _record_scan(trace_path, [[{"role": "user", "content": "hello"}]])
        reader = TraceReader(trace_path)

        request = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hello"},
            ],
            "params": {"temperature": 0.0, "max_tokens": 4096},
            "tools": [],
        }
        entry = reader.get("target_call", request)
        assert entry["response"]["content"] == "test response"

    def test_get_miss_raises(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        _record_scan(trace_path, [[{"role": "user", "content": "hello"}]])
        reader = TraceReader(trace_path)

        request = {
            "messages": [{"role": "user", "content": "different prompt"}],
            "params": {"temperature": 0.0, "max_tokens": 4096},
            "tools": [],
        }
        with pytest.raises(ReplayMissError, match="No matching trace entry"):
            reader.get("target_call", request)

    def test_get_exhausted_raises(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        _record_scan(trace_path, [[{"role": "user", "content": "hello"}]])
        reader = TraceReader(trace_path)

        request = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hello"},
            ],
            "params": {"temperature": 0.0, "max_tokens": 4096},
            "tools": [],
        }
        reader.get("target_call", request)  # First call succeeds
        with pytest.raises(ReplayExhaustedError, match="no more entries"):
            reader.get("target_call", request)  # Second call: exhausted


# ---------------------------------------------------------------------------
# ReplayAdapter
# ---------------------------------------------------------------------------

class TestReplayAdapter:
    def test_implements_protocol(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        _record_scan(trace_path, [[{"role": "user", "content": "hi"}]])
        reader = TraceReader(trace_path)
        adapter = ReplayAdapter(reader, call_kind="target_call")

        assert hasattr(adapter, "model_name")
        assert hasattr(adapter, "send_message")
        assert hasattr(adapter, "last_usage")
        assert adapter.model_name == "openai/gpt-4o-mini"

    def test_returns_recorded_response(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        _record_scan(
            trace_path,
            [[{"role": "user", "content": "greetings"}]],
            response_content="recorded reply",
        )
        reader = TraceReader(trace_path)
        adapter = ReplayAdapter(reader, call_kind="target_call")

        result = adapter.send_message(
            [{"role": "user", "content": "greetings"}],
            system_prompt="You are helpful.",
        )
        assert result == "recorded reply"

    def test_sets_last_usage(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        _record_scan(trace_path, [[{"role": "user", "content": "hi"}]])
        reader = TraceReader(trace_path)
        adapter = ReplayAdapter(reader, call_kind="target_call")

        adapter.send_message(
            [{"role": "user", "content": "hi"}],
            system_prompt="You are helpful.",
        )
        usage = adapter.last_usage
        assert usage.input_tokens == 10
        assert usage.output_tokens == 5
        assert usage.total_tokens == 15

    def test_miss_raises_readable_error(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        _record_scan(trace_path, [[{"role": "user", "content": "original"}]])
        reader = TraceReader(trace_path)
        adapter = ReplayAdapter(reader, call_kind="target_call")

        with pytest.raises(ReplayMissError, match="prompt.*changed"):
            adapter.send_message(
                [{"role": "user", "content": "modified"}],
                system_prompt="You are helpful.",
            )


# ---------------------------------------------------------------------------
# Round-trip fidelity
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_record_then_replay_byte_identical(self, tmp_path):
        """The core invariant: record → replay produces identical responses."""
        trace_path = tmp_path / "roundtrip.trace.jsonl"
        conversations = [
            [{"role": "user", "content": "turn 1"}],
            [{"role": "user", "content": "turn 2"}],
            [{"role": "user", "content": "turn 3"}],
        ]

        # Record phase
        run_id = new_run_id()
        writer = TraceWriter(path=trace_path, run_id=run_id)
        adapter = LiteLLMAdapter(model="openai/gpt-4o-mini")
        adapter.trace_writer = writer
        adapter.call_kind = "target_call"

        recorded_responses = []
        recorded_usages = []
        with patch("litellm.completion") as mock_completion:
            for i, msgs in enumerate(conversations):
                mock_completion.return_value = _make_mock_response(f"response {i}")
                result = adapter.send_message(msgs, system_prompt="system")
                recorded_responses.append(result)
                recorded_usages.append(adapter.last_usage)
        writer.close()

        # Replay phase
        reader = TraceReader(trace_path)
        replay = ReplayAdapter(reader, call_kind="target_call")

        for i, msgs in enumerate(conversations):
            result = replay.send_message(msgs, system_prompt="system")
            assert result == recorded_responses[i], f"Response mismatch at turn {i}"
            assert replay.last_usage.input_tokens == recorded_usages[i].input_tokens
            assert replay.last_usage.output_tokens == recorded_usages[i].output_tokens

    def test_round_trip_with_target_and_judge(self, tmp_path):
        """Both target and judge adapters produce identical replay responses."""
        trace_path = tmp_path / "both.trace.jsonl"
        run_id = new_run_id()
        writer = TraceWriter(path=trace_path, run_id=run_id)

        target = LiteLLMAdapter(model="openai/gpt-4o-mini")
        target.trace_writer = writer
        target.call_kind = "target_call"

        judge = LiteLLMAdapter(model="openai/gpt-5.4")
        judge.trace_writer = writer
        judge.call_kind = "judge_call"

        msgs = [{"role": "user", "content": "hello"}]
        with patch("litellm.completion") as mock:
            mock.return_value = _make_mock_response("target says hi")
            target_result = target.send_message(msgs, system_prompt="Be helpful")

            mock.return_value = _make_mock_response("judge says 0.3")
            judge_result = judge.send_message(msgs, system_prompt="Be a judge")
        writer.close()

        # Replay both
        reader = TraceReader(trace_path)
        target_replay = ReplayAdapter(reader, call_kind="target_call")
        judge_replay = ReplayAdapter(reader, call_kind="judge_call")

        assert target_replay.send_message(msgs, system_prompt="Be helpful") == target_result
        assert judge_replay.send_message(msgs, system_prompt="Be a judge") == judge_result


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class TestReplayErrors:
    def test_modified_prompt_fails(self, tmp_path):
        """Changing one character in the system prompt causes ReplayMissError."""
        trace_path = tmp_path / "test.trace.jsonl"
        run_id = new_run_id()
        writer = TraceWriter(path=trace_path, run_id=run_id)
        adapter = LiteLLMAdapter(model="openai/gpt-4o-mini")
        adapter.trace_writer = writer
        adapter.call_kind = "target_call"

        with patch("litellm.completion", return_value=_make_mock_response()):
            adapter.send_message(
                [{"role": "user", "content": "test"}],
                system_prompt="You are a helpful assistant.",
            )
        writer.close()

        reader = TraceReader(trace_path)
        replay = ReplayAdapter(reader, call_kind="target_call")

        with pytest.raises(ReplayMissError):
            replay.send_message(
                [{"role": "user", "content": "test"}],
                system_prompt="You are a helpful assistant!",  # Changed . to !
            )

    def test_exhausted_trace(self, tmp_path):
        """Truncated trace file raises ReplayExhaustedError."""
        trace_path = tmp_path / "test.trace.jsonl"
        _record_scan(trace_path, [[{"role": "user", "content": "msg"}]])
        reader = TraceReader(trace_path)
        replay = ReplayAdapter(reader, call_kind="target_call")

        # First call succeeds
        replay.send_message(
            [{"role": "user", "content": "msg"}],
            system_prompt="You are helpful.",
        )
        # Second call: trace exhausted for this fingerprint
        with pytest.raises(ReplayExhaustedError, match="no more entries"):
            replay.send_message(
                [{"role": "user", "content": "msg"}],
                system_prompt="You are helpful.",
            )

    def test_schema_version_mismatch(self, tmp_path):
        """Unknown schema version raises ReplaySchemaVersionError."""
        trace_path = tmp_path / "future.trace.jsonl"
        entry = {
            "schema_version": 999,
            "run_id": "test", "step_id": 1,
            "timestamp": "2026-01-01T00:00:00+00:00",
            "kind": "target_call", "model": "test/m", "provider": "test",
            "request": {"messages": [], "params": {}, "tools": []},
            "response": {"content": "", "usage": {}, "finish_reason": "stop"},
            "duration_ms": 0, "metadata": {},
        }
        trace_path.write_text(json.dumps(entry) + "\n")

        with pytest.raises(ReplaySchemaVersionError) as exc_info:
            TraceReader(trace_path)
        assert exc_info.value.found == 999
        assert SCHEMA_VERSION in exc_info.value.supported


# ---------------------------------------------------------------------------
# Zero network calls
# ---------------------------------------------------------------------------

class TestNoNetworkCalls:
    def test_replay_never_imports_litellm(self, tmp_path):
        """ReplayAdapter returns responses without any network call."""
        trace_path = tmp_path / "test.trace.jsonl"
        _record_scan(trace_path, [[{"role": "user", "content": "hi"}]])
        reader = TraceReader(trace_path)
        replay = ReplayAdapter(reader, call_kind="target_call")

        with patch("litellm.completion") as mock_completion:
            result = replay.send_message(
                [{"role": "user", "content": "hi"}],
                system_prompt="You are helpful.",
            )
            mock_completion.assert_not_called()

        assert result == "test response"

    def test_replay_adapter_has_no_litellm_dependency(self):
        """ReplayAdapter module does not import litellm at module level."""
        import sapien_score.tracing.replay as replay_mod
        source = Path(replay_mod.__file__).read_text()
        assert "import litellm" not in source

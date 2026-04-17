"""Tests for the sapien_score.tracing module.

Covers TraceWriter JSONL output, schema compliance, crash safety,
path derivation, thread safety, and adapter integration.
"""

import json
import threading
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sapien_score.tracing.trace import (
    SCHEMA_VERSION,
    TraceEntry,
    TraceWriter,
    derive_trace_path,
    new_run_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request() -> dict:
    return {
        "messages": [{"role": "user", "content": "hello"}],
        "params": {"temperature": 0.0, "max_tokens": 4096},
        "tools": [],
    }


def _make_response(content: str = "world") -> dict:
    return {
        "content": content,
        "usage": {"input_tokens": 10, "output_tokens": 5,
                  "total_tokens": 15, "cost_usd": 0.001},
        "finish_reason": "stop",
    }


def _record_one(writer: TraceWriter, **overrides) -> None:
    defaults = dict(
        kind="target_call",
        model="openai/gpt-4o-mini",
        provider="openai",
        request=_make_request(),
        response=_make_response(),
        duration_ms=123,
    )
    defaults.update(overrides)
    writer.record(**defaults)


def _read_entries(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------------------------------------------------------------------
# TraceWriter basics
# ---------------------------------------------------------------------------

class TestTraceWriterBasics:
    def test_creates_file_and_parent_dirs(self, tmp_path):
        trace_path = tmp_path / "deep" / "nested" / "trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        writer.close()
        assert trace_path.exists()

    def test_records_valid_jsonl(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        _record_one(writer)
        _record_one(writer)
        writer.close()

        lines = trace_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

    def test_schema_version_on_every_entry(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        _record_one(writer)
        _record_one(writer)
        _record_one(writer)
        writer.close()

        for entry in _read_entries(trace_path):
            assert entry["schema_version"] == SCHEMA_VERSION

    def test_run_id_consistent_within_writer(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        rid = new_run_id()
        writer = TraceWriter(path=trace_path, run_id=rid)
        _record_one(writer)
        _record_one(writer)
        writer.close()

        entries = _read_entries(trace_path)
        assert all(e["run_id"] == rid for e in entries)

    def test_run_id_unique_per_call(self):
        ids = {new_run_id() for _ in range(100)}
        assert len(ids) == 100

    def test_step_id_monotonically_increasing(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        for _ in range(5):
            _record_one(writer)
        writer.close()

        entries = _read_entries(trace_path)
        step_ids = [e["step_id"] for e in entries]
        assert step_ids == [1, 2, 3, 4, 5]

    def test_step_ids_unique(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        for _ in range(10):
            _record_one(writer)
        writer.close()

        entries = _read_entries(trace_path)
        step_ids = [e["step_id"] for e in entries]
        assert len(set(step_ids)) == len(step_ids)

    def test_entry_has_all_required_fields(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        _record_one(writer)
        writer.close()

        entry = _read_entries(trace_path)[0]
        required = {
            "schema_version", "run_id", "step_id", "timestamp",
            "kind", "model", "provider", "request", "response",
            "duration_ms", "metadata",
        }
        assert required.issubset(entry.keys())

    def test_timestamp_is_utc_iso8601(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        _record_one(writer)
        writer.close()

        entry = _read_entries(trace_path)[0]
        ts = entry["timestamp"]
        # ISO 8601 UTC timestamps end with +00:00 or Z
        assert "+00:00" in ts or ts.endswith("Z")

    def test_kind_values(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        _record_one(writer, kind="target_call")
        _record_one(writer, kind="judge_call")
        writer.close()

        entries = _read_entries(trace_path)
        assert entries[0]["kind"] == "target_call"
        assert entries[1]["kind"] == "judge_call"

    def test_metadata_defaults_to_empty_dict(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        _record_one(writer)
        writer.close()

        entry = _read_entries(trace_path)[0]
        assert entry["metadata"] == {}


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

class TestTraceWriterContextManager:
    def test_context_manager_closes(self, tmp_path):
        trace_path = tmp_path / "test.trace.jsonl"
        with TraceWriter(path=trace_path, run_id=new_run_id()) as writer:
            _record_one(writer)
        # File should be readable after context exit
        entries = _read_entries(trace_path)
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# Crash safety
# ---------------------------------------------------------------------------

class TestCrashSafety:
    def test_each_line_flushed_individually(self, tmp_path):
        """Each record should be readable immediately without close()."""
        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        _record_one(writer)

        # Read WITHOUT closing — entry should be flushed
        entries = _read_entries(trace_path)
        assert len(entries) == 1

        _record_one(writer)
        entries = _read_entries(trace_path)
        assert len(entries) == 2

        writer.close()

    def test_partial_last_line_detectable(self, tmp_path):
        """If a line is partially written, all prior lines remain valid."""
        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        _record_one(writer)
        _record_one(writer)
        writer.close()

        # Simulate crash: append a partial line
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write('{"schema_version": 1, "run_id": "abc", "ste')

        # Read valid lines, skip invalid
        valid = []
        with open(trace_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    valid.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # Partial last line — skip
        assert len(valid) == 2


# ---------------------------------------------------------------------------
# Path handling (spaces, unicode)
# ---------------------------------------------------------------------------

class TestPathHandling:
    def test_path_with_spaces(self, tmp_path):
        trace_path = tmp_path / "path with spaces" / "trace file.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        _record_one(writer)
        writer.close()
        assert trace_path.exists()
        assert len(_read_entries(trace_path)) == 1

    def test_path_with_unicode(self, tmp_path):
        trace_path = tmp_path / "tr\u00e4ces" / "r\u00e9sults.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        _record_one(writer)
        writer.close()
        assert trace_path.exists()
        assert len(_read_entries(trace_path)) == 1

    def test_path_with_space_and_unicode(self, tmp_path):
        trace_path = tmp_path / "my tr\u00e4ces dir" / "s\u00f8me file.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())
        _record_one(writer)
        writer.close()
        assert trace_path.exists()
        entries = _read_entries(trace_path)
        assert len(entries) == 1
        assert entries[0]["schema_version"] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Concurrent writes (threading)
# ---------------------------------------------------------------------------

class TestConcurrentWrites:
    def test_threaded_writes_no_corruption(self, tmp_path):
        """Multiple threads writing to the same TraceWriter don't corrupt the file.

        Design note: sapien-score uses a single TraceWriter shared between
        the target and judge adapters in the same process. Thread safety is
        via threading.Lock. Multi-process concurrent writes to the same file
        are NOT supported — use separate output paths.
        """
        trace_path = tmp_path / "concurrent.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())

        errors = []

        def write_entries(n: int):
            try:
                for _ in range(n):
                    _record_one(writer)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_entries, args=(20,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        writer.close()
        assert not errors

        entries = _read_entries(trace_path)
        assert len(entries) == 100  # 5 threads * 20 entries

        # All entries should have unique step_ids
        step_ids = [e["step_id"] for e in entries]
        assert len(set(step_ids)) == 100

        # Step IDs should cover 1..100 (monotonic from itertools.count)
        assert set(step_ids) == set(range(1, 101))


# ---------------------------------------------------------------------------
# Path derivation
# ---------------------------------------------------------------------------

class TestDeriveTracePath:
    def test_with_json_output(self):
        result = derive_trace_path("/path/to/results.json")
        assert result == Path("/path/to/traces/results.trace.jsonl")

    def test_with_non_json_output(self):
        result = derive_trace_path("/path/to/output.csv")
        assert result == Path("/path/to/traces/output.trace.jsonl")

    def test_without_output(self):
        result = derive_trace_path(None)
        expected = Path.home() / ".sapien_score" / "traces" / "last_scan.trace.jsonl"
        assert result == expected


# ---------------------------------------------------------------------------
# Adapter integration
# ---------------------------------------------------------------------------

class TestAdapterTracing:
    def _make_mock_response(self):
        """Create a mock litellm response object."""
        choice = MagicMock()
        choice.message.content = "I am a response"
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

    def test_adapter_no_trace_by_default(self):
        from sapien_score.engine.adapter import LiteLLMAdapter
        adapter = LiteLLMAdapter(model="test/model")
        assert adapter.trace_writer is None

    def test_adapter_records_target_call(self, tmp_path):
        from sapien_score.engine.adapter import LiteLLMAdapter

        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())

        adapter = LiteLLMAdapter(model="openai/gpt-4o-mini", rate_limit_delay=0)
        adapter.trace_writer = writer
        adapter.call_kind = "target_call"

        with patch("litellm.completion", return_value=self._make_mock_response()):
            result = adapter.send_message([{"role": "user", "content": "hi"}])

        writer.close()

        assert result == "I am a response"
        entries = _read_entries(trace_path)
        assert len(entries) == 1
        assert entries[0]["kind"] == "target_call"
        assert entries[0]["model"] == "openai/gpt-4o-mini"
        assert entries[0]["provider"] == "openai"
        assert entries[0]["response"]["content"] == "I am a response"
        assert entries[0]["response"]["finish_reason"] == "stop"
        assert entries[0]["duration_ms"] >= 0

    def test_adapter_records_judge_call(self, tmp_path):
        from sapien_score.engine.adapter import LiteLLMAdapter

        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())

        adapter = LiteLLMAdapter(model="openai/gpt-5.4", rate_limit_delay=0)
        adapter.trace_writer = writer
        adapter.call_kind = "judge_call"

        with patch("litellm.completion", return_value=self._make_mock_response()):
            adapter.send_message([{"role": "user", "content": "judge this"}])

        writer.close()

        entries = _read_entries(trace_path)
        assert len(entries) == 1
        assert entries[0]["kind"] == "judge_call"

    def test_adapter_records_error(self, tmp_path):
        from sapien_score.engine.adapter import LiteLLMAdapter

        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())

        adapter = LiteLLMAdapter(model="openai/gpt-4o-mini", rate_limit_delay=0,
                                 base_retry_delay=0)
        adapter.trace_writer = writer
        adapter.call_kind = "target_call"

        with patch("litellm.completion", side_effect=Exception("401 Unauthorized")):
            with pytest.raises(Exception, match="401 Unauthorized"):
                adapter.send_message([{"role": "user", "content": "hi"}])

        writer.close()

        entries = _read_entries(trace_path)
        assert len(entries) == 1
        assert entries[0]["metadata"]["error"] == "401 Unauthorized"
        assert entries[0]["response"]["content"] is None

    def test_adapter_without_trace_works_normally(self):
        from sapien_score.engine.adapter import LiteLLMAdapter

        adapter = LiteLLMAdapter(model="test/model", rate_limit_delay=0)

        mock_response = self._make_mock_response()
        with patch("litellm.completion", return_value=mock_response):
            result = adapter.send_message([{"role": "user", "content": "hi"}])

        assert result == "I am a response"

    def test_trace_failure_does_not_crash_scan(self, tmp_path):
        """If trace recording raises, the scan continues normally."""
        from sapien_score.engine.adapter import LiteLLMAdapter

        broken_writer = MagicMock()
        broken_writer.record.side_effect = OSError("disk full")

        adapter = LiteLLMAdapter(model="openai/gpt-4o-mini", rate_limit_delay=0)
        adapter.trace_writer = broken_writer

        with patch("litellm.completion", return_value=self._make_mock_response()):
            result = adapter.send_message([{"role": "user", "content": "hi"}])

        assert result == "I am a response"

    def test_request_captures_system_prompt(self, tmp_path):
        from sapien_score.engine.adapter import LiteLLMAdapter

        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())

        adapter = LiteLLMAdapter(model="openai/gpt-4o-mini", rate_limit_delay=0)
        adapter.trace_writer = writer

        with patch("litellm.completion", return_value=self._make_mock_response()):
            adapter.send_message(
                [{"role": "user", "content": "hi"}],
                system_prompt="You are a helpful assistant.",
            )

        writer.close()

        entries = _read_entries(trace_path)
        messages = entries[0]["request"]["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful assistant."
        assert messages[1]["role"] == "user"

    def test_shared_writer_interleaves_target_and_judge(self, tmp_path):
        """Both adapters write to the same trace file in order."""
        from sapien_score.engine.adapter import LiteLLMAdapter

        trace_path = tmp_path / "test.trace.jsonl"
        writer = TraceWriter(path=trace_path, run_id=new_run_id())

        target = LiteLLMAdapter(model="openai/gpt-4o-mini", rate_limit_delay=0)
        target.trace_writer = writer
        target.call_kind = "target_call"

        judge = LiteLLMAdapter(model="openai/gpt-5.4", rate_limit_delay=0)
        judge.trace_writer = writer
        judge.call_kind = "judge_call"

        with patch("litellm.completion", return_value=self._make_mock_response()):
            target.send_message([{"role": "user", "content": "target msg"}])
            judge.send_message([{"role": "user", "content": "judge msg"}])
            target.send_message([{"role": "user", "content": "target msg 2"}])

        writer.close()

        entries = _read_entries(trace_path)
        assert len(entries) == 3
        assert [e["kind"] for e in entries] == ["target_call", "judge_call", "target_call"]
        assert [e["step_id"] for e in entries] == [1, 2, 3]

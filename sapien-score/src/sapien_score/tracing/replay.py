# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Trace reader and replay adapter for deterministic scan reproduction.

TraceReader loads a JSONL trace file (produced by TraceWriter in Task 0.1)
and indexes entries by (kind, request_fingerprint) for O(1) lookup.
ReplayAdapter is a drop-in replacement for LiteLLMAdapter that returns
recorded responses instead of calling the LLM — producing byte-identical
scores when run against the same trace.
"""

from __future__ import annotations

import collections
import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from sapien_score.engine.adapter import UsageInfo
from sapien_score.tracing.errors import (
    ReplayExhaustedError,
    ReplayMissError,
    ReplaySchemaVersionError,
)
from sapien_score.tracing.trace import SCHEMA_VERSION

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request fingerprinting
# ---------------------------------------------------------------------------

def _strip_none(obj: object) -> object:
    """Recursively remove None values from dicts.

    Treats ``{"key": None}`` and ``{}`` as semantically identical for
    fingerprinting purposes.
    """
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(item) for item in obj]
    return obj


def request_fingerprint(kind: str, request_dict: dict) -> str:
    """Compute a stable SHA-256 fingerprint of a request.

    Deterministic across runs and platforms: uses sorted keys, UTF-8
    encoding, and strips None values (treated as absent).

    Args:
        kind: "target_call" or "judge_call"
        request_dict: The request dict containing messages, params, tools.

    Returns:
        Hex-encoded SHA-256 hash string.
    """
    normalized = _strip_none(request_dict)
    canonical = json.dumps(
        {"kind": kind, "request": normalized},
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# TraceReader
# ---------------------------------------------------------------------------

class TraceReader:
    """Loads and indexes a JSONL trace file for O(1) replay lookups.

    Entries are indexed by ``(kind, request_fingerprint)``. When the same
    request appears multiple times (e.g. repeated turns), entries are
    returned in recording order via a FIFO queue.

    Usage::

        reader = TraceReader(Path("traces/scan.trace.jsonl"))
        entry = reader.get("target_call", request_dict)
        print(entry["response"]["content"])
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._entries: list[dict] = []
        self._index: dict[str, collections.deque[dict]] = {}
        self._models: dict[str, str] = {}
        self._params: dict[str, dict] = {}
        self._run_id: str = ""
        self._load()

    def _load(self) -> None:
        """Parse and validate the trace file, building the lookup index."""
        if not self._path.exists():
            raise FileNotFoundError(
                f"Trace file not found: {self._path}. "
                f"Record a trace first with: voigt-kampff scan --output <file>"
            )

        with open(self._path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "Trace line %d in %s: invalid JSON, skipping "
                        "(possible partial write from crash)",
                        lineno, self._path,
                    )
                    continue

                version = entry.get("schema_version")
                if version != SCHEMA_VERSION:
                    raise ReplaySchemaVersionError(
                        found=version,
                        supported=[SCHEMA_VERSION],
                        path=self._path,
                    )

                self._entries.append(entry)

                fp = request_fingerprint(entry["kind"], entry["request"])
                key = f"{entry['kind']}:{fp}"
                if key not in self._index:
                    self._index[key] = collections.deque()
                self._index[key].append(entry)

                kind = entry["kind"]
                if kind not in self._models:
                    self._models[kind] = entry["model"]
                if kind not in self._params:
                    self._params[kind] = entry["request"].get("params", {})

                if not self._run_id:
                    self._run_id = entry.get("run_id", "")

        if not self._entries:
            raise FileNotFoundError(
                f"Trace file is empty or contains no valid entries: {self._path}"
            )

    def get(self, kind: str, request: dict) -> dict:
        """Look up a trace entry by kind and request fingerprint.

        Returns the next unconsumed entry matching the fingerprint.
        Raises ReplayMissError if no match exists, or ReplayExhaustedError
        if all matches have been consumed.
        """
        fp = request_fingerprint(kind, request)
        key = f"{kind}:{fp}"

        if key not in self._index:
            raise ReplayMissError(
                kind=kind,
                fingerprint=fp,
                request=request,
            )

        if not self._index[key]:
            raise ReplayExhaustedError(kind=kind, fingerprint=fp)

        return self._index[key].popleft()

    def metadata(self) -> dict:
        """Return summary info about the trace for validation and display."""
        return {
            "run_id": self._run_id,
            "path": str(self._path),
            "total_entries": len(self._entries),
            "target_calls": sum(1 for e in self._entries if e["kind"] == "target_call"),
            "judge_calls": sum(1 for e in self._entries if e["kind"] == "judge_call"),
            "target_model": self._models.get("target_call"),
            "judge_model": self._models.get("judge_call"),
        }

    def params_for_kind(self, kind: str) -> dict:
        """Return the recorded params (temperature, max_tokens) for a call kind.

        Extracted from the first trace entry of that kind. Used by
        ReplayAdapter to reconstruct matching request fingerprints.
        """
        return dict(self._params.get(kind, {"temperature": 0.0, "max_tokens": 4096}))


# ---------------------------------------------------------------------------
# ReplayAdapter
# ---------------------------------------------------------------------------

class ReplayAdapter:
    """Drop-in replacement for LiteLLMAdapter that returns recorded responses.

    Implements the same Protocol used by engine/driver.py and scoring/judge.py:
    ``model_name``, ``send_message(messages, system_prompt)``, ``last_usage``.

    Makes zero network calls. A replay that reaches the network is a bug.
    """

    def __init__(self, reader: TraceReader, call_kind: str = "target_call") -> None:
        self._reader = reader
        self._call_kind = call_kind
        meta = reader.metadata()
        model_key = "target_model" if call_kind == "target_call" else "judge_model"
        self._model = meta.get(model_key) or "unknown"
        params = reader.params_for_kind(call_kind)
        self._temperature = params.get("temperature", 0.0)
        self._max_tokens = params.get("max_tokens", 4096)
        self._last_usage = UsageInfo()
        self._trace_writer = None

    @property
    def model_name(self) -> str:
        """The model name from the trace metadata."""
        return self._model

    @property
    def last_usage(self) -> UsageInfo:
        """Usage info from the most recent replayed call."""
        return self._last_usage

    @property
    def trace_writer(self):
        """Optional TraceWriter for --allow-trace-during-replay."""
        return self._trace_writer

    @trace_writer.setter
    def trace_writer(self, writer: Optional[object]) -> None:
        self._trace_writer = writer

    @property
    def call_kind(self) -> str:
        """The trace entry kind: 'target_call' or 'judge_call'."""
        return self._call_kind

    @call_kind.setter
    def call_kind(self, kind: str) -> None:
        self._call_kind = kind

    def send_message(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> str:
        """Return the recorded response for this request.

        Reconstructs the request dict exactly as LiteLLMAdapter._record_trace
        builds it, computes the fingerprint, and returns the matching entry's
        response content.

        Raises ReplayMissError or ReplayExhaustedError on mismatch.
        """
        full_messages = list(messages)
        if system_prompt:
            full_messages = [{"role": "system", "content": system_prompt}] + full_messages

        request = {
            "messages": full_messages,
            "params": {
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
            },
            "tools": [],
        }

        entry = self._reader.get(self._call_kind, request)

        usage_data = entry["response"].get("usage", {})
        self._last_usage = UsageInfo(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
            cost_usd=usage_data.get("cost_usd", 0.0),
        )

        content = entry["response"]["content"]

        self._record_trace(full_messages, content, entry)

        return content

    def _record_trace(
        self,
        messages: list[dict],
        content: str,
        entry: dict,
    ) -> None:
        """Record the replayed call if a trace writer is attached.

        Only active when --allow-trace-during-replay is set. Swallows
        errors to avoid crashing the scan.
        """
        if self._trace_writer is None:
            return
        try:
            provider = self._model.split("/")[0] if "/" in self._model else "unknown"
            self._trace_writer.record(
                kind=self._call_kind,
                model=self._model,
                provider=provider,
                request={
                    "messages": messages,
                    "params": {
                        "temperature": self._temperature,
                        "max_tokens": self._max_tokens,
                    },
                    "tools": [],
                },
                response=entry["response"],
                duration_ms=0,
                metadata={"replayed_from": entry.get("run_id", "")},
            )
        except Exception as exc:
            logger.warning("Trace recording during replay failed: %s", exc)

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Append-only JSONL trace writer for LLM call recording.

Each scan run produces one trace file containing every target and judge
LLM call in chronological order. The file is crash-safe: each entry is
flushed immediately after writing, so a partial last line is the worst
case on process death. All prior lines remain valid JSON.

Thread safety uses a threading.Lock (not file-level locking). This is
sufficient because sapien-score runs scenarios sequentially in a single
process. Two separate processes writing to the same trace file is NOT
supported — use separate output paths instead.
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def new_run_id() -> str:
    """Generate a new UUID4 run identifier."""
    return str(uuid.uuid4())


def derive_trace_path(output_path: Optional[str]) -> Path:
    """Compute the trace file path from the scan's --output path.

    If output_path is provided, the trace lives in a ``traces/``
    subdirectory next to the output file::

        /path/to/results.json  →  /path/to/traces/results.trace.jsonl

    If output_path is None (no --output), falls back to a well-known
    location under the user's home directory::

        ~/.sapien_score/traces/last_scan.trace.jsonl
    """
    if output_path:
        p = Path(output_path)
        return p.parent / "traces" / (p.stem + ".trace.jsonl")
    return Path.home() / ".sapien_score" / "traces" / "last_scan.trace.jsonl"


@dataclass
class TraceEntry:
    """A single recorded LLM call matching the Task 0 schema.

    Fields match the AGENTS.md specification exactly:
    schema_version, run_id, step_id, timestamp, kind, model, provider,
    request, response, duration_ms, metadata.
    """
    schema_version: int
    run_id: str
    step_id: int
    timestamp: str
    kind: str
    model: str
    provider: str
    request: dict
    response: dict
    duration_ms: int
    metadata: dict = field(default_factory=dict)


class TraceWriter:
    """Append-only JSONL writer for LLM call traces.

    Usage::

        writer = TraceWriter(path=Path("traces/scan.trace.jsonl"),
                             run_id=new_run_id())
        # ... pass writer to adapters ...
        writer.close()

    Or as a context manager::

        with TraceWriter(path=..., run_id=...) as writer:
            ...

    Each ``record()`` call appends one JSON line and flushes immediately.
    The file handle is kept open for the lifetime of the writer to avoid
    repeated open/close overhead.
    """

    def __init__(self, path: Path, run_id: str) -> None:
        # Canonicalize the path so a user-supplied ``--output`` with
        # "../" components can't escape to an unintended directory via
        # the derived trace-file path. Parents are created relative to
        # the resolved location, and .parent.resolve() handles the
        # (not-yet-existing) path component safely on all platforms.
        raw_path = Path(path)
        self._path = (raw_path.parent.resolve() / raw_path.name)
        allowed_roots = (Path.cwd().resolve(), Path.home().resolve())
        try:
            resolved_parent = self._path.parent
            if not any(
                str(resolved_parent).startswith(str(root))
                for root in allowed_roots
            ):
                logger.warning(
                    "Trace path %s resolves outside cwd/home; "
                    "writing anyway but verify --output is intended",
                    self._path,
                )
        except (OSError, ValueError) as exc:
            logger.warning("Trace path canonicalization check failed: %s", exc)

        self._run_id = run_id
        self._lock = threading.Lock()
        self._step_counter = itertools.count(1)
        self._closed = False

        # Create parent directories eagerly so errors surface at init
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a", encoding="utf-8")

    @property
    def path(self) -> Path:
        """The path to the trace file."""
        return self._path

    @property
    def run_id(self) -> str:
        """The UUID4 run identifier for this trace session."""
        return self._run_id

    def record(
        self,
        *,
        kind: str,
        model: str,
        provider: str,
        request: dict,
        response: dict,
        duration_ms: int,
        metadata: Optional[dict] = None,
    ) -> None:
        """Append a trace entry to the JSONL file.

        Each call writes one JSON line and flushes to disk immediately.
        May raise on I/O errors — callers (e.g. adapter._record_trace)
        are responsible for swallowing exceptions to avoid crashing scans.
        """
        entry = TraceEntry(
            schema_version=SCHEMA_VERSION,
            run_id=self._run_id,
            step_id=next(self._step_counter),
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind=kind,
            model=model,
            provider=provider,
            request=request,
            response=response,
            duration_ms=duration_ms,
            metadata=metadata or {},
        )
        line = json.dumps(asdict(entry), ensure_ascii=False, default=str) + "\n"
        with self._lock:
            if self._closed:
                logger.warning(
                    "Trace write after close for %s — entry dropped", self._path
                )
                return
            self._fh.write(line)
            self._fh.flush()

    def close(self) -> None:
        """Flush and close the trace file handle."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._fh.flush()
                self._fh.close()

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False

    def __del__(self) -> None:
        # Safety net: close file handle if caller forgot to call close()
        try:
            if not self._closed:
                self._fh.close()
        except Exception:
            pass

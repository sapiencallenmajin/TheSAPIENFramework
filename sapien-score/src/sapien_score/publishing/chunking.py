# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""Chunked-ingest planning for the SAPIEN scoreboard.

The ingest endpoint runs on Vercel (~4.5 MB serverless request-body limit),
but full council runs (190 scenarios) serialize to 7-14 MB. The endpoint
therefore supports CHUNKED ingest via a ``chunk_info`` field:

  - chunk 1        : run metadata + first N scenarios  -> returns ``run_id``
  - chunks 2..M-1  : ``run_id`` + ``chunk_info`` + next N scenarios (append)
  - chunk M (last) : ``run_id`` + ``chunk_info`` + last scenarios + aggregates

This module is PURE: it computes the chunk plan and builds the per-chunk
payload dicts from an already-built full payload (see
:func:`sapien_score.publishing.client.build_publish_payload`). No HTTP, no
I/O — so the plan math and payload threading are unit-testable without
touching the network. ``run_id`` for chunks 2..M is injected by the caller
after chunk 1's response, via :func:`inject_run_id`.

The contract mirrors ``publish-chunked.ps1`` exactly.
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Optional

# Namespace for deriving a stable idempotency key from a run's identity.
_IDEMPOTENCY_NAMESPACE = uuid.NAMESPACE_URL


def _idempotency_key(full_payload: dict) -> str:
    """UUID the endpoint requires on chunk 1 to dedup run creation.

    Derived deterministically from the run's own identity (``run_id``, else
    ``content_hash``) via uuid5, so re-publishing the SAME run reuses the same
    key and the server dedups instead of creating a duplicate run. Falls back
    to a random uuid4 only when the run carries no stable identifier.
    """
    stable = str(full_payload.get("run_id") or full_payload.get("content_hash") or "")
    return str(uuid.uuid5(_IDEMPOTENCY_NAMESPACE, stable)) if stable else str(uuid.uuid4())

# Aggregate fields carried ONLY on the final chunk (they describe the whole
# run and finalize it). Mirrors publish-chunked.ps1's last-chunk field list.
AGGREGATE_FIELDS = ("risk_summary", "overall_health", "mean_health", "p10_health")

# Fields never treated as run-level metadata on chunk 1: the results array
# is sliced per-chunk, and the aggregates ride the last chunk.
_NON_META_FIELDS = ("results",) + AGGREGATE_FIELDS

# Safe single-POST body size. Vercel's serverless body limit is ~4.5 MB; keep
# headroom for headers and JSON overhead. Above this we chunk even when the
# scenario count alone would fit in one chunk.
SAFE_SINGLE_POST_BYTES = 4_000_000


class ChunkPlan:
    """Resolved chunk plan for one run.

    Attributes
    ----------
    needs_chunking:
        True when the run must be split into >= 2 chunks.
    effective_chunk_size:
        Scenarios per chunk actually used (may be smaller than the requested
        chunk size when a size-triggered split forces >= 2 chunks).
    total_chunks:
        Number of chunks (1 when ``needs_chunking`` is False).
    n_results:
        Total scenario count.
    reason:
        Human-readable trigger ("size", "count", "size+count", or "single").
    """

    __slots__ = ("needs_chunking", "effective_chunk_size", "total_chunks",
                 "n_results", "reason")

    def __init__(self, needs_chunking, effective_chunk_size, total_chunks,
                 n_results, reason):
        self.needs_chunking = needs_chunking
        self.effective_chunk_size = effective_chunk_size
        self.total_chunks = total_chunks
        self.n_results = n_results
        self.reason = reason

    def chunk_ranges(self) -> list[tuple[int, int]]:
        """Return ``[(start, end_exclusive), ...]`` for each chunk."""
        ranges = []
        for i in range(self.total_chunks):
            start = i * self.effective_chunk_size
            end = min(start + self.effective_chunk_size, self.n_results)
            ranges.append((start, end))
        return ranges


def plan_chunks(
    n_results: int,
    chunk_size: int,
    payload_bytes: Optional[int] = None,
    safe_bytes: int = SAFE_SINGLE_POST_BYTES,
) -> ChunkPlan:
    """Decide whether/how to chunk *n_results* scenarios.

    Chunking triggers when either the scenario count exceeds *chunk_size*
    OR the serialized *payload_bytes* exceeds *safe_bytes*. When only size
    forces it but the count fits in one chunk, the chunk size is reduced so
    the run still splits into >= 2 chunks (the endpoint requires
    ``total_chunks >= 2`` for chunked ingest).

    A single scenario can never be chunked; such a run always single-POSTs
    (the caller warns if it is also oversized).
    """
    if n_results <= 0:
        return ChunkPlan(False, chunk_size, 0, 0, "empty")
    if chunk_size < 1:
        chunk_size = 1

    too_big = payload_bytes is not None and payload_bytes > safe_bytes
    too_many = n_results > chunk_size

    if not (too_big or too_many):
        return ChunkPlan(False, chunk_size, 1, n_results, "single")

    reason = "size+count" if (too_big and too_many) else ("size" if too_big else "count")

    eff = chunk_size
    total = math.ceil(n_results / eff)
    if total < 2:
        # Size forced chunking but the count fits one chunk — halve so we get
        # exactly 2 chunks. (n_results >= 2 here, since n_results == 1 short-
        # circuits below.)
        if n_results < 2:
            # Un-chunkable single oversized scenario: single-POST anyway.
            return ChunkPlan(False, chunk_size, 1, n_results, "single-oversized")
        eff = math.ceil(n_results / 2)
        total = math.ceil(n_results / eff)

    return ChunkPlan(True, eff, total, n_results, reason)


def build_chunk_payloads(full_payload: dict, plan: ChunkPlan) -> list[dict]:
    """Split a full publish payload into per-chunk payload dicts.

    Chunk 1 carries all run-level metadata (everything except ``results`` and
    the aggregate fields) plus its scenario slice. Middle chunks carry only
    their scenario slice + ``chunk_info``. The last chunk carries its slice +
    ``chunk_info`` + the aggregate fields that finalize the run.

    ``chunk_info.run_id`` is NOT set here for chunks 2..M — the caller injects
    it after chunk 1 returns a ``run_id`` (see :func:`inject_run_id`).
    """
    results = full_payload.get("results") or []
    meta = {k: v for k, v in full_payload.items() if k not in _NON_META_FIELDS}
    aggregates = {k: full_payload[k] for k in AGGREGATE_FIELDS if k in full_payload}

    chunks: list[dict] = []
    ranges = plan.chunk_ranges()
    total = plan.total_chunks
    for idx, (start, end) in enumerate(ranges):
        chunk: dict = {
            "results": results[start:end],
            "chunk_info": {"chunk_index": idx + 1, "total_chunks": total},
        }
        if idx == 0:
            # Run metadata rides chunk 1 (does not clobber results/chunk_info).
            chunk.update(meta)
            # The endpoint requires a UUID idempotency_key on chunk 1 to dedup
            # run creation (a chunk-1 retry with the same key is a safe no-op
            # server-side rather than a duplicate run).
            chunk["idempotency_key"] = _idempotency_key(full_payload)
        if idx == total - 1:
            chunk.update(aggregates)
        chunks.append(chunk)
    return chunks


def inject_run_id(chunk: dict, run_id: str) -> dict:
    """Attach *run_id* to a chunk's ``chunk_info`` (in place) and return it.

    Used for chunks 2..M once chunk 1 has established the ``run_id``.
    """
    info = chunk.setdefault("chunk_info", {})
    info["run_id"] = run_id
    return chunk


def payload_size_bytes(payload: dict) -> int:
    """Serialized byte length of *payload* (compact JSON), for the size gate."""
    return len(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"))

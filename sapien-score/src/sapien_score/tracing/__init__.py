# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Structured trace recording and deterministic replay for LLM calls.

Provides append-only JSONL trace files that record every target and
judge LLM call made during a scan. TraceReader and ReplayAdapter enable
deterministic replay (Task 0.2) and automated verification (Task 0.3).
"""

from sapien_score.tracing.errors import (
    ReplayError,
    ReplayExhaustedError,
    ReplayMissError,
    ReplaySchemaVersionError,
)
from sapien_score.tracing.replay import ReplayAdapter, TraceReader, request_fingerprint
from sapien_score.tracing.trace import TraceEntry, TraceWriter

__all__ = [
    "ReplayAdapter",
    "ReplayError",
    "ReplayExhaustedError",
    "ReplayMissError",
    "ReplaySchemaVersionError",
    "TraceEntry",
    "TraceReader",
    "TraceWriter",
    "request_fingerprint",
]

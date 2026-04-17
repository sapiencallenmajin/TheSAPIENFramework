# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://synthreo.ai
"""Structured trace recording for LLM calls.

Provides append-only JSONL trace files that record every target and
judge LLM call made during a scan. Traces enable deterministic replay
(Task 0.2) and automated verification (Task 0.3).
"""

from sapien_score.tracing.trace import TraceEntry, TraceWriter

__all__ = ["TraceEntry", "TraceWriter"]

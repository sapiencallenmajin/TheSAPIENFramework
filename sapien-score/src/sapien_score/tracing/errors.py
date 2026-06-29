# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Custom exceptions for trace replay.

All replay errors inherit from ReplayError so callers can catch the
family with a single except clause.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class ReplayError(Exception):
    """Base class for all replay-related errors."""


class ReplaySchemaVersionError(ReplayError):
    """Trace file uses an unsupported schema version."""

    def __init__(
        self,
        found: int,
        supported: list[int],
        path: Optional[Path] = None,
    ) -> None:
        self.found = found
        self.supported = supported
        self.path = path
        path_str = f" in {path}" if path else ""
        super().__init__(
            f"Unsupported trace schema version {found}{path_str}. "
            f"Supported versions: {supported}. "
            f"The trace may have been recorded with a newer version of voigt-kampff."
        )


class ReplayMissError(ReplayError):
    """No matching trace entry for the current request."""

    def __init__(
        self,
        kind: str,
        fingerprint: str,
        request: dict,
        message: Optional[str] = None,
    ) -> None:
        self.kind = kind
        self.fingerprint = fingerprint
        self.request = request
        if message:
            detail = message
        else:
            detail = (
                f"No matching trace entry for {kind} "
                f"(fingerprint: {fingerprint[:16]}...). "
                f"The prompt or params changed since the trace was recorded, "
                f"or the trace is from a different scan."
            )
        super().__init__(detail)


class ReplayExhaustedError(ReplayError):
    """Trace ran out of entries for a given request pattern."""

    def __init__(self, kind: str, fingerprint: str) -> None:
        self.kind = kind
        self.fingerprint = fingerprint
        super().__init__(
            f"Trace exhausted: no more entries for {kind} "
            f"(fingerprint: {fingerprint[:16]}...). "
            f"The scan made more calls than the trace contains — "
            f"this scan has diverged from the recorded run."
        )

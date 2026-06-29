# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Shared I/O helpers.

Single source of truth for on-disk write semantics used by the CLI —
atomic writes with fsync and a ``.backup.json`` snapshot of the prior
contents. Previously there were two implementations (one full-fat in
``commands/scan_output.py``, one stripped in ``commands/rejudge.py``);
the stripped variant could leave the output file corrupt on a crash
mid-rename. Both call sites now import from here.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Upper bound on the size of an *untrusted* input file (trace JSONL, scenario
# JSON) we will read into memory. A hostile or corrupt file an order of
# magnitude past any legitimate corpus must be rejected up front rather than
# OOM-ing the process. 50 MB comfortably clears real traces (the bundled
# financial replay fixture is ~4 MB) while stopping a memory-exhaustion DoS.
MAX_INPUT_FILE_BYTES: int = 50 * 1024 * 1024


def check_input_file_size(
    path: str | os.PathLike[str],
    *,
    max_bytes: int = MAX_INPUT_FILE_BYTES,
) -> None:
    """Raise ``ValueError`` if *path* is larger than *max_bytes*.

    Cheap ``os.stat`` guard run before ``json.load`` / line iteration on any
    operator- or third-party-supplied file, so a multi-gigabyte input can't
    exhaust memory. A missing file is left for the caller's own open() to
    surface (we don't want to mask FileNotFoundError here).
    """
    try:
        size = os.stat(path).st_size
    except OSError:
        return
    if size > max_bytes:
        raise ValueError(
            f"Input file {os.fspath(path)!r} is {size} bytes, which exceeds the "
            f"{max_bytes}-byte safety limit. Refusing to load a file this large."
        )


def atomic_write_json(path: str, data: dict) -> None:
    """Write JSON atomically: temp file → fsync → rename.

    Safety properties:
      * A crash mid-write never leaves the target path partially written —
        it's replaced in a single ``os.replace`` call.
      * Before replacement, the previous contents (if any) are copied to
        ``<path>.backup.json`` so a corrupted new payload is recoverable.
      * Temp file lives in the same directory as the target so the final
        rename is atomic on the same filesystem.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Preserve prior contents before we overwrite.
    if target.exists():
        backup = target.with_suffix(target.suffix + ".backup.json") \
            if target.suffix != ".json" \
            else target.with_name(target.stem + ".backup.json")
        try:
            backup.write_bytes(target.read_bytes())
        except OSError as exc:
            logger.warning("Could not write backup %s: %s", backup, exc)

    fd, tmp_path = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except Exception:
        # Never leave a stale tmp on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

#!/usr/bin/env python
# voigt-kampff — connectivity validation for the default council roster.
#
# Sends a trivial "Respond with only the word OK" prompt to every seat
# in the DEFAULT_COUNCIL and reports per-seat latency + success. Keeps
# total spend under $0.001 so it's safe to run on every new deployment.
#
# Usage:
#     python scripts/validate_council.py
#
# Exit codes:
#     0 — all seats responded successfully
#     1 — one or more seats failed (bad API key, missing dep, etc.)

from __future__ import annotations

import sys
import time
from pathlib import Path

# Allow running from the repo root without `pip install -e .`.
_REPO_SRC = Path(__file__).resolve().parent.parent / "src"
if _REPO_SRC.is_dir() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from sapien_score.engine.adapter import get_adapter
from sapien_score.engine.council_config import DEFAULT_COUNCIL

_VALIDATION_PROMPT = "Respond with only the word: OK"


def _seat_model_string(seat) -> str:
    if seat.model_version:
        return f"{seat.model}@{seat.model_version}"
    return seat.model


def validate() -> int:
    """Ping each default-council seat once. Return 0 on full success."""
    print(f"Validating {len(DEFAULT_COUNCIL)} council seats...")
    print()

    failures: list[tuple[str, str]] = []
    for seat in DEFAULT_COUNCIL:
        model_str = _seat_model_string(seat)
        label = f"[{seat.family:<9}] {model_str}"
        try:
            adapter = get_adapter(model=model_str, max_tokens=8)
            t0 = time.monotonic()
            reply = adapter.send_message(
                messages=[{"role": "user", "content": _VALIDATION_PROMPT}],
            )
            elapsed = time.monotonic() - t0
            # Trim to first line for display; a seat that responded with
            # anything at all counts as connected.
            snippet = (reply or "").strip().splitlines()[0] if reply else "(empty)"
            snippet = snippet[:40]
            print(f"  OK   {label}  ({elapsed:.2f}s) → {snippet!r}")
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:140]}"
            print(f"  FAIL {label}  {reason}")
            failures.append((model_str, reason))

    print()
    if failures:
        print(f"{len(failures)} seat(s) failed:")
        for model, reason in failures:
            print(f"  - {model}: {reason}")
        print()
        print("Suggestion: verify these environment variables are set:")
        print("  TOGETHERAI_API_KEY (Meta / Llama)")
        print("  GEMINI_API_KEY     (Google / Gemma)")
        print("  DEEPSEEK_API_KEY   (DeepSeek)")
        print("  MISTRAL_API_KEY    (Mistral)")
        print("  COHERE_API_KEY     (Cohere)")
        print()
        print("See: https://docs.litellm.ai/docs/providers for provider setup.")
        return 1

    print(f"All {len(DEFAULT_COUNCIL)} seats reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(validate())

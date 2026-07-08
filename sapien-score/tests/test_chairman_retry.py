# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Chairman retry-with-backoff: transient chairman-caller failures are retried
before the fail-open ``chairman_failed`` flag is raised.

Reuses the council v2 fake-caller convention (see test_council_v2): canned
JSON closures, no network. Backoff is patched to 0 so the test is instant.
"""

import pytest

from sapien_score.engine import council_scorer
from sapien_score.engine.council_scorer import score_with_council

from test_council_v2 import (
    SCENARIO,
    TRANSCRIPT,
    _config_with_chairman,
    _split_judge_caller,
    _verdict_json,
)


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    # Keep the test instant: zero the sleep interval.
    monkeypatch.setattr(council_scorer, "CHAIRMAN_BACKOFF_BASE_S", 0)


def test_chairman_recovers_after_transient_failures():
    """Raises twice, succeeds on the third attempt → adjudicated, not failed."""
    calls = {"n": 0}

    def chairman(model, system, user):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429 rate limited")
        return _verdict_json("FAIL", confidence=5, dim="boundary_erosion")

    result = score_with_council(
        TRANSCRIPT, SCENARIO, _config_with_chairman(),
        judge_caller=_split_judge_caller({"meta", "mistral"}),  # 2 FAIL / 3 PASS
        round_timeout_s=None,
        chairman_caller=chairman,
    )

    assert calls["n"] == 3  # invoked exactly 3 times (2 failures + 1 success)
    assert "chairman_adjudicated" in result.flags
    assert "chairman_failed" not in result.flags
    assert result.surface_result == "FAIL"  # retry recovered the ruling


def test_chairman_exhausts_retries_then_fails_open():
    """Always raises → chairman_failed after CHAIRMAN_MAX_RETRIES, majority stands."""
    calls = {"n": 0}

    def chairman(model, system, user):
        calls["n"] += 1
        raise RuntimeError("provider down")

    result = score_with_council(
        TRANSCRIPT, SCENARIO, _config_with_chairman(),
        judge_caller=_split_judge_caller({"meta", "mistral"}),  # 3 PASS majority
        round_timeout_s=None,
        chairman_caller=chairman,
    )

    assert calls["n"] == council_scorer.CHAIRMAN_MAX_RETRIES  # no more, no fewer
    assert "chairman_failed" in result.flags
    assert "chairman_adjudicated" not in result.flags
    assert result.surface_result == "PASS"  # fail-open: majority preserved
    assert result.chairman_review is None

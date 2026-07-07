# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Tests for the standalone ``voigt-kampff publish`` command and chunking.

Every test mocks HTTP (``sapien_score.commands.publish._post_json`` /
``httpx.post``) — NO test touches the live endpoint. Covers chunk-plan math,
council payload fields, backfill trigger/skip, partial-run refusal, missing
auth, dry-run (zero calls), mid-chunk abort with orphan warning, and
multi-file publishes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sapien_score.commands.publish import publish
from sapien_score.publishing.chunking import (
    SAFE_SINGLE_POST_BYTES,
    build_chunk_payloads,
    inject_run_id,
    plan_chunks,
)
from sapien_score.publishing.client import build_publish_payload


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _seat(idx: str, verdict: str) -> dict:
    return {"judge_id": idx, "verdict": verdict, "confidence": 4,
            "reasoning": f"seat {idx} says {verdict}"}


def _council_scoring(seats: int, fail: int = 0) -> dict:
    verdicts = (["FAIL"] * fail) + (["PASS"] * (seats - fail))
    scores = [_seat(f"seat_{i+1}", v) for i, v in enumerate(verdicts)]
    return {
        "scoring_mode": "council",
        "council_version": "2.0",
        "surface_result": "FAIL" if fail > seats - fail else "PASS",
        "consensus_status": "clear" if fail in (0, seats) else "controversial",
        "vote_tally": {"PASS": seats - fail, "FAIL": fail},
        "individual_scores": scores,
        "chairman_review": None,
        "flags": [],
    }


def _council_entry(sid: str, seats: int = 5, fail: int = 0) -> dict:
    return {
        "scenario_id": sid,
        "domain": "medical",
        "verdict": "held",
        "health_score": 88,
        "turns": [
            {"turn": 1, "user_message": "u", "assistant_response": "a",
             "judge_reasoning": "r", "drift": 0.1, "health_score": 88},
        ],
        "council_scoring": _council_scoring(seats, fail),
    }


def council_run(n: int = 6, seats: int = 5) -> dict:
    """A small COMPLETE council run."""
    results = [_council_entry(f"s{i}", seats=seats) for i in range(n)]
    return {
        "model": "test/model-v2",
        "run_id": "local-abc",
        "schema_version": 3,
        "n_requested": n,
        "n_completed": n,
        "n_failed": 0,
        "results": results,
        "risk_summary": {"critical": 0, "high": 1},
        "overall_health": 88.0,
        "mean_health": 87.5,
        "p10_health": 70.0,
    }


def single_run(n: int = 4) -> dict:
    """A small COMPLETE single-scoring run (no council_scoring)."""
    results = [
        {"scenario_id": f"s{i}", "domain": "medical", "verdict": "held",
         "health_score": 90,
         "turns": [{"turn": 1, "user_message": "u", "assistant_response": "a",
                    "drift": 0.1, "health_score": 90}]}
        for i in range(n)
    ]
    return {
        "model": "test/model", "run_id": "local-xyz", "schema_version": 3,
        "n_requested": n, "n_completed": n, "n_failed": 0, "results": results,
        "risk_summary": {"critical": 0}, "overall_health": 90.0,
    }


def _write(tmp_path: Path, name: str, data: dict) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


class _FakePost:
    """Records POST calls and returns a run_id on chunk 1 / OK otherwise.

    ``fail_on_chunk`` returns HTTP 500 on that (1-based) call; ``timeout_on_chunk``
    returns the ambiguous-timeout tuple (status None) on that call.
    """

    def __init__(self, fail_on_chunk: int | None = None,
                 timeout_on_chunk: int | None = None):
        self.calls: list[dict] = []
        self.fallbacks: list = []
        self.fail_on_chunk = fail_on_chunk
        self.timeout_on_chunk = timeout_on_chunk

    def __call__(self, url, payload, headers, timeout, fallback_url=None):
        self.calls.append(payload)
        self.fallbacks.append(fallback_url)
        n = len(self.calls)
        if self.timeout_on_chunk is not None and n == self.timeout_on_chunk:
            return (None, None, "timeout (ambiguous — request may have been received): x")
        if self.fail_on_chunk is not None and n == self.fail_on_chunk:
            return (500, {"error": "boom"}, None)
        body = {"run_id": "srv-run-1", "scenarios_processed": len(payload.get("results") or [])}
        return (200, body, None)


# --------------------------------------------------------------------------
# Pure chunk-plan math
# --------------------------------------------------------------------------

class TestChunkPlan:
    def test_single_post_when_small(self):
        plan = plan_chunks(6, 25, payload_bytes=1000)
        assert not plan.needs_chunking
        assert plan.total_chunks == 1

    def test_count_triggers_chunking(self):
        plan = plan_chunks(190, 25, payload_bytes=1000)
        assert plan.needs_chunking
        assert plan.total_chunks == 8  # ceil(190/25)
        assert plan.effective_chunk_size == 25
        assert plan.reason in ("count", "size+count")

    def test_ranges_cover_all_scenarios(self):
        plan = plan_chunks(190, 25, payload_bytes=1000)
        ranges = plan.chunk_ranges()
        assert ranges[0] == (0, 25)
        assert ranges[-1][1] == 190
        # contiguous, no gaps
        for (s0, e0), (s1, _e1) in zip(ranges, ranges[1:]):
            assert e0 == s1
        assert sum(e - s for s, e in ranges) == 190

    def test_size_forces_split_even_when_count_fits(self):
        # 6 scenarios (< chunk_size 25) but oversized -> must split into 2.
        plan = plan_chunks(6, 25, payload_bytes=SAFE_SINGLE_POST_BYTES + 1)
        assert plan.needs_chunking
        assert plan.total_chunks == 2
        assert plan.reason == "size"

    def test_single_oversized_scenario_cannot_chunk(self):
        plan = plan_chunks(1, 25, payload_bytes=SAFE_SINGLE_POST_BYTES + 1)
        assert not plan.needs_chunking
        assert plan.reason == "single-oversized"

    def test_run_id_threading(self):
        payload = build_publish_payload(
            output_data=council_run(6), judge_model=None, judge_family=None,
            run_label="L", is_primary=False)
        plan = plan_chunks(6, 2, payload_bytes=1000)
        assert plan.total_chunks == 3
        chunks = build_chunk_payloads(payload, plan)
        # chunk 1 carries run metadata, no run_id yet
        assert "run_label" in chunks[0]
        assert "run_id" not in chunks[0]["chunk_info"]
        assert chunks[0]["chunk_info"] == {"chunk_index": 1, "total_chunks": 3}
        # middle/last carry only slice+chunk_info until injected
        assert "run_label" not in chunks[1]
        inject_run_id(chunks[1], "RID")
        assert chunks[1]["chunk_info"]["run_id"] == "RID"
        # aggregates only on last chunk
        assert "risk_summary" in chunks[-1]
        assert "risk_summary" not in chunks[0]
        assert "risk_summary" not in chunks[1]


# --------------------------------------------------------------------------
# Council payload fields
# --------------------------------------------------------------------------

class TestCouncilPayload:
    def test_council_fields_present(self):
        payload = build_publish_payload(
            output_data=council_run(6, seats=5), judge_model=None,
            judge_family=None, run_label="L", is_primary=False)
        assert payload["scoring_mode"] == "council"
        assert payload["council_size"] == 5
        assert payload["council_seats_min"] == 5
        assert payload["council_degraded_scenarios"] == 0

    def test_single_run_labeled_single(self):
        payload = build_publish_payload(
            output_data=single_run(4), judge_model="openai/gpt-4o",
            judge_family="OpenAI", run_label="L", is_primary=False)
        assert payload["scoring_mode"] == "single"
        assert "council_size" not in payload


# --------------------------------------------------------------------------
# CLI: dry-run, auth, partial, backfill, chunked publish, multi-file
# --------------------------------------------------------------------------

class TestPublishCLI:
    def test_dry_run_makes_no_http_calls(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SAPIEN_INGEST_API_KEY", raising=False)
        f = _write(tmp_path, "run.json", council_run(6))
        fake = _FakePost()
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(publish, [f, "--run-label", "L", "--dry-run"])
        assert res.exit_code == 0, res.output
        assert fake.calls == []
        assert "no http calls made" in res.output.lower()

    def test_missing_auth_fails_loud(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SAPIEN_INGEST_API_KEY", raising=False)
        f = _write(tmp_path, "run.json", council_run(6))
        res = CliRunner().invoke(publish, [f, "--run-label", "L"])
        assert res.exit_code != 0
        assert "SAPIEN_INGEST_API_KEY" in res.output

    def test_partial_run_refused_without_flag(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAPIEN_INGEST_API_KEY", "tok")
        data = council_run(6)
        data["n_failed"] = 2
        f = _write(tmp_path, "run.json", data)
        fake = _FakePost()
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(publish, [f, "--run-label", "L"])
        assert res.exit_code != 0
        assert "partial" in res.output.lower()
        assert fake.calls == []

    def test_partial_run_allowed_with_flag(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAPIEN_INGEST_API_KEY", "tok")
        data = council_run(6)
        data["n_completed"] = 4
        f = _write(tmp_path, "run.json", data)
        fake = _FakePost()
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(
                publish, [f, "--run-label", "L", "--allow-partial"])
        assert res.exit_code == 0, res.output
        assert len(fake.calls) >= 1

    def test_backfill_triggers_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAPIEN_INGEST_API_KEY", "tok")
        data = council_run(6, seats=5)
        assert "judge_reliability" not in data
        f = _write(tmp_path, "run.json", data)
        fake = _FakePost()
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(publish, [f, "--run-label", "L"])
        assert res.exit_code == 0, res.output
        assert "judge_reliability" in res.output
        # backfilled block reached the wire (chunk 1 carries metadata)
        assert "judge_reliability" in fake.calls[0]

    def test_backfill_skipped_when_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAPIEN_INGEST_API_KEY", "tok")
        data = council_run(6, seats=5)
        data["judge_reliability"] = {"already": "here"}
        data["turn_metrics_summary"] = {"already": "here"}
        f = _write(tmp_path, "run.json", data)
        fake = _FakePost()
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(publish, [f, "--run-label", "L"])
        assert res.exit_code == 0, res.output
        assert fake.calls[0]["judge_reliability"] == {"already": "here"}

    def test_chunked_publish_threads_run_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAPIEN_INGEST_API_KEY", "tok")
        f = _write(tmp_path, "run.json", council_run(6))
        fake = _FakePost()
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(
                publish, [f, "--run-label", "L", "--chunk-size", "2"])
        assert res.exit_code == 0, res.output
        assert len(fake.calls) == 3  # ceil(6/2)
        # chunk 1 no run_id, chunks 2/3 carry the server run_id
        assert "run_id" not in fake.calls[0]["chunk_info"]
        assert fake.calls[1]["chunk_info"]["run_id"] == "srv-run-1"
        assert fake.calls[2]["chunk_info"]["run_id"] == "srv-run-1"
        # aggregates finalize on last chunk only
        assert "risk_summary" in fake.calls[2]
        assert "risk_summary" not in fake.calls[0]

    def test_mid_chunk_failure_aborts_with_orphan_warning(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAPIEN_INGEST_API_KEY", "tok")
        f = _write(tmp_path, "run.json", council_run(6))
        fake = _FakePost(fail_on_chunk=2)
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(
                publish, [f, "--run-label", "L", "--chunk-size", "2"])
        assert res.exit_code != 0
        assert len(fake.calls) == 2  # stopped after the failing chunk
        assert "srv-run-1" in res.output
        assert "NON-FINALIZED" in res.output
        assert "retry" in res.output.lower()

    def test_single_post_for_small_run(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAPIEN_INGEST_API_KEY", "tok")
        f = _write(tmp_path, "run.json", council_run(6))
        fake = _FakePost()
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(publish, [f, "--run-label", "L"])
        assert res.exit_code == 0, res.output
        assert len(fake.calls) == 1
        assert "chunk_info" not in fake.calls[0]

    def test_multi_file_publishes_each(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAPIEN_INGEST_API_KEY", "tok")
        f1 = _write(tmp_path, "a.json", council_run(6))
        f2 = _write(tmp_path, "b.json", single_run(4))
        fake = _FakePost()
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(publish, [f1, f2, "--run-label", "L"])
        assert res.exit_code == 0, res.output
        assert len(fake.calls) == 2  # one single-POST each

    def test_empty_results_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SAPIEN_INGEST_API_KEY", "tok")
        data = council_run(6)
        data["results"] = []
        f = _write(tmp_path, "run.json", data)
        fake = _FakePost()
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(publish, [f, "--run-label", "L"])
        assert res.exit_code != 0
        assert fake.calls == []

    def test_chunk_timeout_aborts_no_retry(self, tmp_path, monkeypatch):
        # BLOCKING regression: a timeout on chunk 2 must NOT retry/fall back —
        # the loop stops (no chunk 3), surfaces the run_id, and exits non-zero.
        monkeypatch.setenv("SAPIEN_INGEST_API_KEY", "tok")
        f = _write(tmp_path, "run.json", council_run(6))
        fake = _FakePost(timeout_on_chunk=2)
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(
                publish, [f, "--run-label", "L", "--chunk-size", "2"])
        assert res.exit_code != 0
        assert len(fake.calls) == 2  # chunk 1 + chunk 2 timeout; NO chunk 3
        # chunked POSTs are invoked with NO fallback URL
        assert all(fb is None for fb in fake.fallbacks)
        assert "srv-run-1" in res.output          # run_id surfaced
        assert "NON-FINALIZED" in res.output
        assert "retry" in res.output.lower()

    def test_chunk1_timeout_warns_no_runid(self, tmp_path, monkeypatch):
        # Chunk-1 timeout: no run_id captured, but the server may have created
        # the run — warn against a naive retry anyway.
        monkeypatch.setenv("SAPIEN_INGEST_API_KEY", "tok")
        f = _write(tmp_path, "run.json", council_run(6))
        fake = _FakePost(timeout_on_chunk=1)
        with patch("sapien_score.commands.publish._post_json", fake):
            res = CliRunner().invoke(
                publish, [f, "--run-label", "L", "--chunk-size", "2"])
        assert res.exit_code != 0
        assert len(fake.calls) == 1  # stopped immediately
        assert "no run_id" in res.output.lower()
        assert "retry" in res.output.lower()


class TestPostJsonFallback:
    """Unit tests for _post_json's fallback policy (the BLOCKING fix)."""

    def _resp(self, status=200, body=None):
        import httpx
        return httpx.Response(status, json=(body or {"run_id": "r"}))

    def test_timeout_never_falls_back(self, monkeypatch):
        import httpx

        from sapien_score.commands.publish import _post_json

        calls = []

        def fake_post(url, **kw):
            calls.append(url)
            raise httpx.ReadTimeout("slow", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", fake_post)
        status, body, err = _post_json(
            "https://primary/x", {"a": 1}, {}, 30.0, fallback_url="https://fallback/x")
        # Exactly ONE attempt — no retry, no fallback — even though fallback_url set.
        assert calls == ["https://primary/x"]
        assert status is None
        assert "timeout" in err.lower()

    def test_connect_error_falls_back_once(self, monkeypatch):
        import httpx

        from sapien_score.commands.publish import _post_json

        calls = []

        def fake_post(url, **kw):
            calls.append(url)
            if url == "https://primary/x":
                raise httpx.ConnectError("refused", request=httpx.Request("POST", url))
            return self._resp(200, {"run_id": "r2"})

        monkeypatch.setattr(httpx, "post", fake_post)
        status, body, err = _post_json(
            "https://primary/x", {"a": 1}, {}, 30.0, fallback_url="https://fallback/x")
        assert calls == ["https://primary/x", "https://fallback/x"]
        assert status == 200
        assert body["run_id"] == "r2"

    def test_connect_error_no_fallback_returns_error(self, monkeypatch):
        import httpx

        from sapien_score.commands.publish import _post_json

        calls = []

        def fake_post(url, **kw):
            calls.append(url)
            raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", fake_post)
        status, body, err = _post_json("https://primary/x", {"a": 1}, {}, 30.0)
        assert calls == ["https://primary/x"]  # no fallback given
        assert status is None
        assert "refused" in err

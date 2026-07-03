# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""Rejudge: re-score existing scan output with a different judge model.

Loads an input scan .json, reuses the stored per-turn transcripts, and
re-runs Layer 1 deterministic scoring plus Layer 2 judge scoring with a
new judge. Produces a new .json with the same schema plus provenance
fields. No target-model API calls are made.

Used for judge-sensitivity methodology studies: same transcripts scored
by multiple judges (Nova Pro, Haiku 4.5, GPT-5.4) to quantify judge
leniency bias on frontier models.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from statistics import quantiles
from typing import Optional

import click
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

from sapien_score.io import atomic_write_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def _load_input(path: str) -> dict:
    """Load and minimally validate the input scan JSON.

    Raises ``click.ClickException`` with a clean message on any error so
    the CLI exits non-zero without a stack trace.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        raise click.ClickException(f"Input file not found: {path}")
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Input JSON is malformed: {exc}")
    except OSError as exc:
        raise click.ClickException(f"Could not read input file {path}: {exc}")

    if not isinstance(payload, dict):
        raise click.ClickException(
            f"Input JSON must be an object, got {type(payload).__name__}"
        )
    if "results" not in payload or not isinstance(payload["results"], list):
        raise click.ClickException(
            "Input JSON missing 'results' list — not a scan output file"
        )
    if "model" not in payload:
        raise click.ClickException("Input JSON missing 'model' field")

    return payload


# ---------------------------------------------------------------------------
# Council replay support (council v1.1 migration path)
# ---------------------------------------------------------------------------
#
# Re-scoring a council run under a new scoring version must NOT re-drive the
# scan: scoring feeds back into conversation control flow (early termination /
# escalation thresholds), so `scan --replay` under changed math diverges from
# the trace mid-scenario and misses recorded entries. The correct model is:
# transcripts are historical fact; only scoring re-runs. Judge VOTES are also
# historical fact — they live in the trace — so council rejudge replays each
# seat's recorded response instead of re-calling judge APIs ($0, deterministic).


class TraceCouncilJudgeCaller:
    """JudgeCaller that replays recorded judge responses from a trace.

    Entries are queued per ``(seat model, request fingerprint)``: every seat
    receives an IDENTICAL per-turn prompt, so fingerprint alone cannot
    attribute a recorded response to a seat — the trace entry's ``model``
    field disambiguates. Recorded per-seat errors are re-raised so a seat
    that failed in the original run fails identically here (e.g. the
    quota-dead Cohere seat), letting the v1.1 aggregation handle it.
    """

    def __init__(self, trace_path: str) -> None:
        import collections

        from sapien_score.io import MAX_TRACE_FILE_BYTES, check_input_file_size
        from sapien_score.tracing.replay import request_fingerprint

        check_input_file_size(trace_path, max_bytes=MAX_TRACE_FILE_BYTES)
        self._fingerprint = request_fingerprint
        self._queues: dict[tuple[str, str], collections.deque] = collections.defaultdict(
            collections.deque
        )
        self._params: dict[str, dict] = {}
        self.seat_models: list[str] = []  # distinct judge models, first-seen order
        self.misses = 0
        self.replays = 0

        with open(trace_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("kind") != "judge_call":
                    continue
                model = entry.get("model") or ""
                if model and model not in self.seat_models:
                    self.seat_models.append(model)
                    params = (entry.get("request") or {}).get("params")
                    if isinstance(params, dict):
                        self._params[model] = dict(params)
                fp = self._fingerprint("judge_call", entry.get("request") or {})
                self._queues[(model, fp)].append(entry)

    def __call__(self, seat, system: str, user: str) -> str:
        # Mirror LiteLLMAdapter._record_trace's request shape exactly —
        # full message list including the system turn, plus the recorded
        # sampling params for this seat — so fingerprints line up.
        params = self._params.get(seat.model, {"temperature": 0.0, "max_tokens": 4096})
        request = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "params": params,
            "tools": [],
        }
        fp = self._fingerprint("judge_call", request)
        queue = self._queues.get((seat.model, fp))
        if not queue:
            self.misses += 1
            raise RuntimeError(
                f"replay miss: no recorded judge response for seat {seat.model} "
                f"(fingerprint {fp[:16]}...)"
            )
        entry = queue.popleft()
        error = (entry.get("metadata") or {}).get("error")
        if error:
            # Recorded seat failure — reproduce it.
            raise RuntimeError(f"replayed seat error: {error}")
        content = (entry.get("response") or {}).get("content")
        if not content:
            raise RuntimeError("replayed judge entry has empty content")
        self.replays += 1
        return content


def _derive_council_seats(payload: dict, caller: "TraceCouncilJudgeCaller") -> list:
    """Build the council roster for a replay rejudge.

    Seat models come from the TRACE (first-seen order) — this includes seats
    that never produced a verdict (their failed calls are still recorded),
    which the results file alone cannot know about. Family labels are taken
    from the results file's ``individual_scores`` where a seat did respond,
    falling back to the litellm provider prefix.
    """
    from sapien_score.engine.council_config import CouncilSeat

    family_by_model: dict[str, str] = {}
    for entry in payload.get("results", []):
        cs = entry.get("council_scoring") or {}
        for s in cs.get("individual_scores") or []:
            model, family = s.get("model"), s.get("family")
            if model and family and model not in family_by_model:
                family_by_model[model] = family

    seats = []
    for model in caller.seat_models:
        family = family_by_model.get(model) or model.split("/", 1)[0]
        seats.append(CouncilSeat(family=family, model=model))
    return seats


# ---------------------------------------------------------------------------
# Per-scenario rejudge
# ---------------------------------------------------------------------------

def _rejudge_scenario(scenario_entry: dict, judge, layer2_threshold: float) -> dict:
    """Return a new scenario-entry dict with rejudged turns and verdict.

    Strategy:
      * Turn 0 is the baseline. Its scores are preserved as-is (no drift
        to score against itself).
      * For each subsequent turn, recompute Layer 1 deterministically from
        the stored assistant_response vs. turn 0's response, then call
        the new judge, then blend. This is what a live scan would do.
      * If ANY turn fails judging, mark the whole scenario as partial and
        do NOT recompute the verdict. Scientific cleanliness: mixed-source
        drifts are worse than no verdict.
    """
    from sapien_score.scoring.composite import score_with_layer2
    from sapien_score.scoring.layer1 import get_verdict, score_turn as layer1_score_turn

    out = dict(scenario_entry)

    # Error entries pass through untouched: they have no transcripts to
    # re-score, and preserving verdict == "error" keeps the rejudged file
    # `--resume`-compatible so the failed scenarios can be re-RUN live later.
    if scenario_entry.get("verdict") == "error":
        return out

    turns_in = scenario_entry.get("turns") or []
    if not turns_in:
        out["rejudge_partial"] = True
        out["rejudge_failure_reason"] = "no_turns"
        out["verdict"] = "rejudge_failed"
        out["turns"] = []
        return out

    if turns_in[0].get("turn") != 0:
        out["rejudge_partial"] = True
        out["rejudge_failure_reason"] = "no_baseline_turn_0"
        out["verdict"] = "rejudge_failed"
        return out

    baseline_response = turns_in[0].get("assistant_response") or ""
    # Mirror the scan EXACTLY: judges are scenario-blind — engine/turn.py
    # passes the fixed _BLIND_SCENARIO_CONTEXT constant, never the domain or
    # title. The previous f"{domain}: {title}" here (a) leaked scenario
    # framing to the judge that live scans never provide, biasing
    # judge-sensitivity comparisons, and (b) changed the judge prompt text,
    # which broke trace-replay fingerprint matching in council mode (every
    # one of 7,135 recorded votes missed).
    from sapien_score.engine.turn import _BLIND_SCENARIO_CONTEXT

    scenario_context = _BLIND_SCENARIO_CONTEXT

    rejudged_turns: list[dict] = []
    turn_drifts: list[float] = []
    any_failure = False
    failure_reason: Optional[str] = None

    # Turn 0: keep original, contribute its original drift to verdict basis.
    turn0 = dict(turns_in[0])
    rejudged_turns.append(turn0)
    # Use stored drift for turn 0 if present, else 0.0 (baseline doesn't drift).
    turn_drifts.append(float(turn0.get("drift") or 0.0))

    for turn_in in turns_in[1:]:
        turn_out = dict(turn_in)
        assistant_response = turn_in.get("assistant_response")
        user_message = turn_in.get("user_message") or ""
        turn_number = int(turn_in.get("turn") or 0)
        pressure_type = turn_in.get("pressure_type") or "unknown"

        if not assistant_response:
            any_failure = True
            failure_reason = failure_reason or "missing_assistant_response"
            turn_out["rejudge_error"] = "missing_assistant_response"
            rejudged_turns.append(turn_out)
            continue

        # Layer 1 is deterministic — recompute from stored transcripts.
        layer1 = layer1_score_turn(assistant_response, baseline_response)

        # Unified Layer 1 + Layer 2 fusion — mirrors engine/turn.py and
        # adaptive/engine.py exactly. Returns L1-only when the threshold
        # gate skips the judge.
        fusion = score_with_layer2(
            layer1=layer1,
            judge=judge,
            scenario_context=scenario_context,
            user_prompt=user_message,
            assistant_response=assistant_response,
            baseline_response=baseline_response,
            turn_number=turn_number,
            pressure_type=pressure_type,
            layer2_threshold=layer2_threshold,
            log_context=f"rejudge {scenario_entry.get('scenario_id', '<unknown>')}",
        )
        # Rejudge's "partial scenario" contract: when the judge was
        # invoked but returned None for this turn, mark the whole
        # scenario partial and skip the turn.  score_with_layer2 does not
        # distinguish "judge returned None" from "judge skipped by
        # threshold" without inspecting layer2_raw + judge_invoked +
        # weighted_drift, so we recheck here.
        if (
            fusion.judge_invoked
            and fusion.layer2_raw is None
        ):
            any_failure = True
            failure_reason = failure_reason or "judge_call_failed"
            turn_out["rejudge_error"] = "judge_call_failed"
            rejudged_turns.append(turn_out)
            continue

        final = fusion.final_score
        turn_out["drift"] = round(final.weighted_drift, 4)
        turn_out["health_score"] = final.health_score
        turn_out["judge_reasoning"] = fusion.judge_reasoning
        turn_out["layer2_divergence_flag"] = fusion.divergence_flag
        turn_out["dimensions"] = [
            {
                "dimension": d.dimension,
                "drift": d.drift,
                "signals": d.signals,
            }
            for d in final.dimensions
        ]
        rejudged_turns.append(turn_out)
        turn_drifts.append(final.weighted_drift)

    out["turns"] = rejudged_turns

    if any_failure:
        out["rejudge_partial"] = True
        out["rejudge_failure_reason"] = failure_reason
        out["verdict"] = "rejudge_failed"
        # Do not recompute health_score / peak_drift / peak_turn — leave
        # the caller's downstream analysis to filter these out.
        out["health_score"] = None
        out["peak_drift"] = None
        out["peak_turn"] = None
        return out

    verdict = get_verdict(turn_drifts)
    out["verdict"] = verdict.verdict
    out["health_score"] = verdict.health_score
    out["peak_drift"] = round(verdict.peak_drift, 4)
    out["peak_turn"] = verdict.peak_turn
    out["rejudge_partial"] = False

    # Council mode: refresh the per-scenario council record from the last
    # scored turn, mirroring the scan path (scan_output attaches
    # engine.judge.last_council_result the same way). Without this the entry
    # would carry the STALE pre-rejudge council_scoring (wrong
    # council_version, wrong tallies) into the republished payload.
    last_council = getattr(judge, "last_council_result", None)
    if last_council is not None:
        out["council_scoring"] = last_council.to_dict()
    elif not hasattr(judge, "last_council_result"):
        # Single-judge rejudge of a council-scored source: the stored
        # council_scoring describes votes the OLD judge panel cast under the
        # OLD scoring version — carrying it forward would stamp the output
        # with provenance that doesn't describe these scores. Drop it.
        out.pop("council_scoring", None)
    return out


# ---------------------------------------------------------------------------
# Aggregate recomputation
# ---------------------------------------------------------------------------

def _recompute_aggregates(entries: list[dict]) -> dict:
    """Recompute mean_health, p10_health, dimension_averages from rejudged entries.

    Only entries with ``rejudge_partial == False`` contribute. Returns
    fields to merge into the output payload.
    """
    from sapien_score.scoring.health import calculate_health_score

    clean = [e for e in entries if not e.get("rejudge_partial", False)]
    if not clean:
        return {
            "overall_health": calculate_health_score({}),
            "mean_health": 0,
            "p10_health": 0,
            "dimension_averages": {},
        }

    scores = [e["health_score"] for e in clean if e.get("health_score") is not None]
    mean_health = sum(scores) / len(scores) if scores else 0
    if len(scores) < 2:
        p10 = min(scores) if scores else 0
    else:
        p10 = quantiles(scores, n=10, method="inclusive")[0]

    dim_totals: dict[str, list[float]] = {}
    for e in clean:
        for t in e.get("turns", []):
            for d in t.get("dimensions", []) or []:
                dim_totals.setdefault(d["dimension"], []).append(d["drift"])
    dim_averages = {k: sum(v) / len(v) for k, v in dim_totals.items()} if dim_totals else {}
    overall_health = calculate_health_score(dim_averages)

    return {
        "overall_health": overall_health,
        "mean_health": round(mean_health, 1),
        "p10_health": round(p10),
        "dimension_averages": {k: round(v, 4) for k, v in dim_averages.items()},
    }


# ---------------------------------------------------------------------------
# Orchestration (testable, console-injectable)
# ---------------------------------------------------------------------------

def rejudge_payload(
    *,
    payload: dict,
    judge,
    judge_model: str,
    source_path: Optional[str] = None,
    layer2_threshold: float = 0.0,
    console: Optional[Console] = None,
) -> dict:
    """Rejudge a loaded scan payload and return the new payload dict.

    Separated from the Click entry point so tests can drive it with a
    mock judge without touching the CLI.
    """
    entries_in = payload.get("results", [])
    rejudged_entries: list[dict] = []

    iterator = entries_in
    if console is not None:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        )
        progress.start()
        task = progress.add_task("Rejudging...", total=len(entries_in))
    else:
        progress = None
        task = None

    try:
        for entry in iterator:
            if progress is not None:
                progress.update(
                    task,
                    description=f"{entry.get('domain', '?')}: {entry.get('title', '?')}",
                )
            rejudged = _rejudge_scenario(entry, judge, layer2_threshold)
            rejudged_entries.append(rejudged)
            if progress is not None:
                progress.advance(task)
    finally:
        if progress is not None:
            progress.stop()

    aggregates = _recompute_aggregates(rejudged_entries)

    out = {
        "model": payload.get("model"),
        "framework_version": payload.get("framework_version", "1.5"),
        "judge_model": judge_model,
        "rejudged_from": {
            "source_file": source_path,
            "source_model": payload.get("model"),
            "source_judge_model": payload.get("judge_model"),
        },
        **aggregates,
        "total_tokens": payload.get("total_tokens", 0),
        "total_cost_usd": payload.get("total_cost_usd", 0.0),
        "results": rejudged_entries,
    }

    partial_count = sum(1 for e in rejudged_entries if e.get("rejudge_partial"))
    error_count = sum(1 for e in rejudged_entries if e.get("verdict") == "error")
    out["rejudge_summary"] = {
        "total_scenarios": len(rejudged_entries),
        "rejudged_successfully": len(rejudged_entries) - partial_count - error_count,
        "rejudge_failed": partial_count,
        "passed_through_errors": error_count,
    }

    # Publishable fields — a rejudged payload must satisfy the same schema-v3
    # ingest contract as a fresh scan (run_id, timestamps, n_* counts,
    # risk_summary, content_hash, _checksum, scoring provenance), otherwise it
    # can only sit on disk. Mirrors scan_output.build_output_payload.
    import uuid
    from datetime import datetime, timezone

    from sapien_score.commands.scan_output import (
        _build_risk_summary,
        compute_content_hash,
        compute_results_checksum,
    )

    scored_entries = [
        e for e in rejudged_entries
        if e.get("verdict") not in ("error", "rejudge_failed")
    ]
    out["n_requested"] = len(rejudged_entries)
    out["n_completed"] = len(scored_entries)
    out["n_failed"] = len(rejudged_entries) - len(scored_entries)
    out["risk_summary"] = _build_risk_summary(scored_entries)
    out["run_id"] = uuid.uuid4().hex
    if payload.get("scan_started_at"):
        out["scan_started_at"] = payload["scan_started_at"]
    if payload.get("scan_finished_at"):
        out["scan_finished_at"] = payload["scan_finished_at"]
    out["rejudged_at"] = datetime.now(timezone.utc).isoformat()

    # Scoring provenance (same derivation as scan_output): the rejudged
    # entries carry fresh council_scoring records, so the stamps reflect the
    # NEW scoring version, and verify() will guard cross-version comparisons.
    council_versions = {
        e["council_scoring"].get("council_version")
        for e in rejudged_entries
        if isinstance(e, dict) and isinstance(e.get("council_scoring"), dict)
    } - {None}
    if council_versions:
        out["scoring_mode"] = "council"
        out["council_version"] = max(council_versions)
        if len(council_versions) > 1:
            out["council_version_mixed"] = sorted(council_versions)
    else:
        out["scoring_mode"] = "single"

    out["content_hash"] = compute_content_hash(rejudged_entries)
    out["_checksum"] = compute_results_checksum(rejudged_entries)
    return out


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

@click.command("rejudge")
@click.argument("input_path", type=click.Path(dir_okay=False))
@click.option(
    "--judge",
    "judge_model",
    default=None,
    help="Judge model identifier (e.g. bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0). "
    "Required for --scoring single; ignored for --scoring council.",
)
@click.option(
    "--scoring",
    type=click.Choice(["single", "council"]),
    default="single",
    show_default=True,
    help="single: re-judge with one live judge model. council: re-score with "
    "the council, replaying each seat's recorded votes from --replay.",
)
@click.option(
    "--replay",
    "replay_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Trace file recorded by the original scan. Required for --scoring "
    "council: seat votes are replayed from it (no judge API calls, $0).",
)
@click.option(
    "--output",
    required=True,
    type=click.Path(dir_okay=False),
    help="Path to write the rejudged scan JSON.",
)
@click.option(
    "--layer2-threshold",
    type=float,
    default=0.0,
    show_default=True,
    help="Only invoke judge on turns with Layer 1 drift >= this threshold.",
)
@click.option(
    "--retry-delay",
    type=float,
    default=2.0,
    show_default=True,
    help="Base retry delay for the judge adapter, in seconds.",
)
def rejudge(
    input_path: str,
    judge_model: Optional[str],
    scoring: str,
    replay_path: Optional[str],
    output: str,
    layer2_threshold: float,
    retry_delay: float,
) -> None:
    """Re-score an existing scan output with a different judge model.

    Reuses the per-turn transcripts stored in INPUT_PATH. No target-model
    API calls are made. Scenarios where any turn fails judging are marked
    ``rejudge_failed`` and excluded from recomputed aggregates.

    Council mode (``--scoring council --replay <trace>``) is the migration
    path for SCORE-AFFECTING council-version changes (see CHANGELOG):
    transcripts come from INPUT_PATH, each seat's votes are replayed from
    the trace, and only the aggregation math runs fresh — deterministic
    and free. Do NOT use ``scan --replay`` for re-scoring: scoring feeds
    back into conversation control flow, so a changed scorer diverges from
    the recorded call sequence mid-scenario.
    """
    console = Console()

    if os.path.abspath(input_path) == os.path.abspath(output):
        raise click.ClickException(
            "--output must differ from input path (refusing to overwrite source)"
        )

    payload = _load_input(input_path)

    if scoring == "council":
        if not replay_path:
            raise click.ClickException("--scoring council requires --replay <trace.jsonl>")
        from sapien_score.engine.council_config import CouncilConfig
        from sapien_score.engine.council_scorer import CouncilScorer

        caller = TraceCouncilJudgeCaller(replay_path)
        seats = _derive_council_seats(payload, caller)
        if len(seats) not in (3, 5):
            raise click.ClickException(
                f"Trace contains {len(seats)} distinct judge seat(s) "
                f"({[s.model for s in seats]}); council size must be 3 or 5. "
                f"Was this trace recorded by a council scan?"
            )
        # chairman_enabled=False: replay reproduces the RECORDED votes; a
        # chairman ruling is live-only and cannot be replayed from a pre-v2
        # trace, so replays stay byte-faithful to what was originally scored.
        config = CouncilConfig(
            size=len(seats), seats=seats, parallel=True, chairman_enabled=False,
        )
        judge = CouncilScorer(config, judge_caller=caller, round_timeout_s=None)
        judge_model = f"Council ({len(seats)}-seat, votes replayed)"
        console.print(
            f"[dim]Council replay: {len(seats)} seats from {replay_path} — "
            f"{', '.join(s.family for s in seats)}[/dim]"
        )
    else:
        if not judge_model:
            raise click.ClickException("--scoring single requires --judge <model>")
        from sapien_score.engine.adapter import get_adapter
        from sapien_score.scoring.judge import JudgeScorer

        judge_adapter = get_adapter(model=judge_model, base_retry_delay=retry_delay)
        judge = JudgeScorer(adapter=judge_adapter)

    console.print(
        f"[dim]Rejudging {len(payload.get('results', []))} scenario(s) "
        f"from {input_path} with judge {judge_model}[/dim]"
    )

    out = rejudge_payload(
        payload=payload,
        judge=judge,
        judge_model=judge_model,
        source_path=input_path,
        layer2_threshold=layer2_threshold,
        console=console,
    )

    atomic_write_json(output, out)

    summary = out["rejudge_summary"]
    console.print(
        f"[green]Rejudged results written to {output}[/green] "
        f"({summary['rejudged_successfully']} ok, "
        f"{summary['rejudge_failed']} failed, "
        f"{summary.get('passed_through_errors', 0)} error entries passed through)"
    )
    if scoring == "council":
        console.print(
            f"[dim]Replay: {caller.replays} seat votes replayed, "
            f"{caller.misses} misses; degraded turns: "
            f"{getattr(judge, 'failure_count', 0)}[/dim]"
        )
    if summary["rejudge_failed"]:
        console.print(
            f"[yellow]{summary['rejudge_failed']} scenario(s) marked "
            f"rejudge_failed — excluded from aggregates.[/yellow]"
        )

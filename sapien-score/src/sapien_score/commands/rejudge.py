# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
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
    scenario_context = f"{scenario_entry.get('domain', '')}: {scenario_entry.get('title', '')}"

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
        "framework_version": payload.get("framework_version", "1.1"),
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
    out["rejudge_summary"] = {
        "total_scenarios": len(rejudged_entries),
        "rejudged_successfully": len(rejudged_entries) - partial_count,
        "rejudge_failed": partial_count,
    }
    return out


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

@click.command("rejudge")
@click.argument("input_path", type=click.Path(dir_okay=False))
@click.option(
    "--judge",
    "judge_model",
    required=True,
    help="Judge model identifier (e.g. bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0)",
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
    judge_model: str,
    output: str,
    layer2_threshold: float,
    retry_delay: float,
) -> None:
    """Re-score an existing scan output with a different judge model.

    Reuses the per-turn transcripts stored in INPUT_PATH. No target-model
    API calls are made. Scenarios where any turn fails judging are marked
    ``rejudge_failed`` and excluded from recomputed aggregates.
    """
    console = Console()

    if os.path.abspath(input_path) == os.path.abspath(output):
        raise click.ClickException(
            "--output must differ from input path (refusing to overwrite source)"
        )

    payload = _load_input(input_path)

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
        f"{summary['rejudge_failed']} failed)"
    )
    if summary["rejudge_failed"]:
        console.print(
            f"[yellow]{summary['rejudge_failed']} scenario(s) marked "
            f"rejudge_failed — excluded from aggregates.[/yellow]"
        )

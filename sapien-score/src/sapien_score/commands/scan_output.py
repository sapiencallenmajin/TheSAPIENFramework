# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Output payload construction and serialization for scan results.

Owns the JSON schema written to ``--output``, per-scenario serialization,
aggregate computation, timing summaries, CSV export, and partial-save
checkpoint logic.  Called by :mod:`scan_orchestration` for in-loop
checkpoints and by the thin ``scan()`` CLI entry point for the final write.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from statistics import quantiles
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Aggregate computation
# ---------------------------------------------------------------------------

def compute_aggregates(results: list) -> tuple:
    """Return (dim_averages, overall_health, mean_score, p10) from results.

    ``results`` is a list of ``(scenario, result)`` tuples. The same helper
    is invoked from two places: once per scenario inside the progress loop
    (for the checkpoint write that backs ``--resume``) and once after the
    loop to populate the console summary. Keeping the math in one place
    guarantees the checkpoint and the final write never disagree.
    """
    from sapien_score.scoring.health import calculate_health_score

    scores = [r.verdict.health_score for _, r in results]
    if not scores:
        return {}, calculate_health_score({}), 0, 0

    mean_score = sum(scores) / len(scores)
    # P10 = 10th percentile of per-scenario health scores. statistics.quantiles
    # with method="inclusive" matches numpy.percentile's linear interpolation
    # (e.g. P10 of [10..100] = 19.0). It requires at least 2 data points, so
    # fall back to min(scores) for degenerate inputs.
    if len(scores) < 2:
        p10 = min(scores)
    else:
        p10 = quantiles(scores, n=10, method="inclusive")[0]

    dim_totals: dict[str, list[float]] = {}
    for _, result in results:
        for turn in result.turns:
            for dim_score in turn.scores.dimensions:
                dim_totals.setdefault(dim_score.dimension, []).append(dim_score.drift)
    dim_averages = {
        dim: sum(vals) / len(vals) for dim, vals in dim_totals.items()
    } if dim_totals else {}

    overall_health = calculate_health_score(dim_averages)
    return dim_averages, overall_health, mean_score, p10


# ---------------------------------------------------------------------------
# Per-scenario serialization
# ---------------------------------------------------------------------------

def serialize_result_entry(scenario, result, override_result=None) -> dict:
    """Flatten a (scenario, result) pair into the dict shape stored in JSON.

    When *override_result* is provided (an ``OverrideResult`` from
    ``scoring.override_config``), the per-scenario risk fields are populated.
    When absent, framework defaults are used with the scenario's own
    impact_tier.
    """
    entry = {
        "scenario_id": scenario.id,
        "domain": scenario.domain,
        "title": scenario.title,
        "verdict": result.verdict.verdict,
        "health_score": result.verdict.health_score,
        "peak_drift": round(result.verdict.peak_drift, 4),
        "peak_turn": result.verdict.peak_turn,
        "dominant_dimension": result.dominant_failure_dimension,
        "effective_pressure": result.most_effective_pressure_type,
        "duration_seconds": result.total_duration_seconds,
        "input_tokens": result.total_input_tokens,
        "output_tokens": result.total_output_tokens,
        "total_tokens": result.total_tokens,
        "cost_usd": round(result.total_cost_usd, 6),
        "model_tier": result.model_tier,
        "counter_refusals_injected": result.counter_refusals_injected,
        "counter_refusal_categories": result.counter_refusal_categories,
    }

    # --- v1.4 risk fields (always present) ---
    if override_result is not None:
        entry["impact_tier_applied"] = override_result.impact_tier_applied
        entry["impact_source"] = override_result.impact_source
        entry["impact_default"] = override_result.impact_default
        if override_result.impact_source == "user_override":
            entry["override_assigned_by"] = override_result.override_assigned_by
            entry["override_rationale"] = override_result.override_rationale
    else:
        entry["impact_tier_applied"] = scenario.impact_tier
        entry["impact_source"] = "framework_default"
        entry["impact_default"] = scenario.impact_tier

    turn_list = []
    for t in result.turns:
        turn_entry = {
            "turn": t.turn_number,
            "phase": t.phase,
            "pressure_type": t.pressure_type,
            "severity": t.severity,
            "user_message": t.user_message,
            "assistant_response": t.assistant_response,
            "drift": round(t.scores.weighted_drift, 4) if t.scores else None,
            "health_score": t.scores.health_score if t.scores else None,
            "judge_reasoning": t.judge_reasoning,
        }
        if t.is_counter_refusal:
            turn_entry["is_counter_refusal"] = True
            turn_entry["counter_category"] = t.counter_category
        turn_list.append(turn_entry)
    entry["turns"] = turn_list
    entry["api_call_timings"] = [
        {
            "turn": t.turn_number,
            "call_type": t.call_type,
            "duration_seconds": t.duration_seconds,
        }
        for t in result.api_timings
    ]
    entry["per_turn_durations"] = result.per_turn_durations
    return entry


# Backwards-compatible alias — external callers import this name
_serialize_result_entry = serialize_result_entry


# ---------------------------------------------------------------------------
# Output payload
# ---------------------------------------------------------------------------

def _resolve_overrides(results: list, override_rules: list) -> list:
    """Resolve override results for each (scenario, result) pair."""
    if override_rules:
        from sapien_score.scoring.override_config import resolve_override
        return [resolve_override(s, override_rules) for s, _ in results]
    return [None] * len(results)


def _build_risk_summary(entries: list) -> dict:
    """Compute aggregate risk summary from serialized result entries.

    Uses the per-scenario impact_tier_applied and verdict fields to compute
    likelihood from drift rate and look up the domain-level risk band.
    """
    from sapien_score.scoring.risk import (
        compute_risk_band,
        drift_rate_to_likelihood,
        impact_tier_to_level,
    )

    if not entries:
        return {}

    # Drift rate = fraction of scenarios with DRIFTED or CAPITULATED verdict
    n_total = len(entries)
    n_drift = sum(
        1 for e in entries
        if e.get("verdict") in ("DRIFTED", "CAPITULATED")
    )
    drift_rate = n_drift / n_total if n_total > 0 else 0.0
    likelihood_level = drift_rate_to_likelihood(drift_rate)

    # Domain impact = max of all scenario tiers (conservative, per spec 7A.3)
    max_impact_level = 1
    for e in entries:
        tier = e.get("impact_tier_applied", "")
        if tier:
            try:
                level = impact_tier_to_level(tier)
                max_impact_level = max(max_impact_level, level)
            except ValueError:
                pass

    risk_band = compute_risk_band(likelihood_level, max_impact_level)

    # Risk band distribution per scenario — use each scenario's peak_drift
    # as its individual drift rate for likelihood computation
    band_dist: dict[str, int] = {"Low": 0, "Moderate": 0, "High": 0, "Critical": 0}
    for e in entries:
        tier = e.get("impact_tier_applied", "")
        if not tier:
            continue
        try:
            impact_level = impact_tier_to_level(tier)
            peak_drift = float(e.get("peak_drift", 0.0))
            scenario_likelihood = drift_rate_to_likelihood(peak_drift)
            scenario_band = compute_risk_band(scenario_likelihood, impact_level)
            band_dist[scenario_band] = band_dist.get(scenario_band, 0) + 1
        except (ValueError, KeyError):
            pass

    return {
        "drift_rate": round(drift_rate, 4),
        "likelihood_level": likelihood_level,
        "max_impact_level": max_impact_level,
        "risk_band": risk_band,
        "risk_band_distribution": band_dist,
    }


def build_output_payload(
    model: str,
    results: list,
    dim_averages: dict,
    overall_health: dict,
    mean_score: float,
    p10: float,
    previous_payload: Optional[dict] = None,
    resume_path: Optional[str] = None,
    override_rules: Optional[list] = None,
) -> dict:
    """Build the JSON payload written to ``--output``.

    When ``previous_payload`` is provided (i.e. a ``--resume`` run), the new
    per-scenario entries are concatenated onto the prior results list and
    all scalar aggregates are recomputed over the combined set so the
    output file always reflects the full scan, not just this session.

    Dimension averages can't be exactly recomputed from JSON because per-turn
    data isn't stored — we approximate with a weighted merge by scenario
    count. Every scenario has a similar number of turns in practice, so the
    drift from a true turn-weighted average is small.
    """
    from sapien_score.scoring.health import calculate_health_score

    total_tokens_new = sum(r.total_tokens for _, r in results)
    total_cost_new = sum(r.total_cost_usd for _, r in results)

    overrides = _resolve_overrides(results, override_rules or [])
    new_entries = [
        serialize_result_entry(s, r, ovr)
        for (s, r), ovr in zip(results, overrides)
    ]

    if previous_payload is None:
        payload = {
            "model": model,
            "framework_version": "1.1",
            "overall_health": overall_health,
            "mean_health": round(mean_score, 1),
            "p10_health": round(p10),
            "dimension_averages": {k: round(v, 4) for k, v in (dim_averages or {}).items()},
            "total_tokens": total_tokens_new,
            "total_cost_usd": round(total_cost_new, 6),
            "results": new_entries,
        }
        payload["risk_summary"] = _build_risk_summary(new_entries)
        return payload

    # --- Resume merge path ---
    old_entries = previous_payload.get("results", []) or []
    combined_entries = old_entries + new_entries

    combined_scores = [e["health_score"] for e in combined_entries]
    if combined_scores:
        combined_mean = sum(combined_scores) / len(combined_scores)
        if len(combined_scores) < 2:
            combined_p10 = min(combined_scores)
        else:
            combined_p10 = quantiles(combined_scores, n=10, method="inclusive")[0]
    else:
        combined_mean = 0
        combined_p10 = 0

    old_dim = previous_payload.get("dimension_averages", {}) or {}
    old_n = len(old_entries)
    new_n = len(new_entries)
    merged_dim: dict[str, float] = {}
    for k in set(old_dim) | set(dim_averages or {}):
        o = old_dim.get(k)
        n = (dim_averages or {}).get(k)
        if o is not None and n is not None and (old_n + new_n) > 0:
            merged_dim[k] = (o * old_n + n * new_n) / (old_n + new_n)
        elif o is not None:
            merged_dim[k] = o
        elif n is not None:
            merged_dim[k] = n

    merged_overall = calculate_health_score(merged_dim) if merged_dim else overall_health
    combined_tokens = (previous_payload.get("total_tokens", 0) or 0) + total_tokens_new
    combined_cost = (previous_payload.get("total_cost_usd", 0.0) or 0.0) + total_cost_new

    payload = {
        "model": model,
        "framework_version": "1.1",
        "overall_health": merged_overall,
        "mean_health": round(combined_mean, 1),
        "p10_health": round(combined_p10),
        "dimension_averages": {k: round(v, 4) for k, v in merged_dim.items()},
        "total_tokens": combined_tokens,
        "total_cost_usd": round(combined_cost, 6),
        "results": combined_entries,
    }
    payload["risk_summary"] = _build_risk_summary(combined_entries)
    if resume_path:
        payload["resumed_from"] = str(resume_path)
    return payload


# Backwards-compatible alias
_build_output_payload = build_output_payload


# ---------------------------------------------------------------------------
# Timing summary
# ---------------------------------------------------------------------------

def compute_timing_summary(results: list, scan_elapsed: float) -> Optional[dict]:
    """Aggregate per-call timing data from all scenario results.

    Returns a dict suitable for console display and JSON ``_timing`` output,
    or None when there are no results to summarize.
    """
    if not results:
        return None

    all_target: list[float] = []
    all_judge: list[float] = []
    all_turn_durations: list[float] = []
    scenario_durations: list[float] = []
    longest: dict = {"duration": 0.0, "scenario": "", "turn": 0, "type": "target"}

    for scenario, result in results:
        scenario_durations.append(result.total_duration_seconds)
        all_turn_durations.extend(result.per_turn_durations)

        for t in result.api_timings:
            if t.call_type == "target":
                all_target.append(t.duration_seconds)
            elif t.call_type == "judge":
                all_judge.append(t.duration_seconds)

            if t.duration_seconds > longest["duration"]:
                longest = {
                    "duration": round(t.duration_seconds, 4),
                    "scenario": scenario.id,
                    "turn": t.turn_number,
                    "type": t.call_type,
                }

    total_api_time = sum(all_target) + sum(all_judge)

    return {
        "avg_target_api_seconds": round(sum(all_target) / len(all_target), 4) if all_target else 0,
        "avg_judge_api_seconds": round(sum(all_judge) / len(all_judge), 4) if all_judge else 0,
        "avg_turn_seconds": round(sum(all_turn_durations) / len(all_turn_durations), 4) if all_turn_durations else 0,
        "avg_scenario_seconds": round(sum(scenario_durations) / len(scenario_durations), 4) if scenario_durations else 0,
        "longest_api_call": longest,
        "total_scan_seconds": round(scan_elapsed, 2),
        "total_api_wait_seconds": round(total_api_time, 2),
        "api_wait_percent": round((total_api_time / scan_elapsed) * 100, 1) if scan_elapsed > 0 else 0,
    }


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def write_cost_csv(path: str, model: str, results: list) -> None:
    """Write per-scenario cost data to CSV."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "scenario_id", "domain", "model", "input_tokens", "output_tokens",
            "total_tokens", "cost_usd", "health_score", "rating", "verdict",
        ])
        for scenario, result in results:
            writer.writerow([
                scenario.id,
                scenario.domain,
                model,
                result.total_input_tokens,
                result.total_output_tokens,
                result.total_tokens,
                f"{result.total_cost_usd:.6f}",
                result.verdict.health_score,
                result.verdict.rating,
                result.verdict.verdict,
            ])


# ---------------------------------------------------------------------------
# Partial-save checkpoint
# ---------------------------------------------------------------------------

def save_partial(
    results: list,
    failed_scenarios: list,
    path: str,
    model: str,
    override_rules: Optional[list] = None,
) -> None:
    """Save current progress so ``--resume`` can recover after a crash.

    Called after every scenario (success or failure) and on KeyboardInterrupt.
    Uses the same per-scenario dict format as the final output so the resume
    loader doesn't need special-casing.
    """
    try:
        overrides = _resolve_overrides(results, override_rules or [])
        data = {
            "partial": True,
            "model": model,
            "completed": len(results),
            "failed": len(failed_scenarios),
            "timestamp": datetime.now().isoformat(),
            "results": [
                serialize_result_entry(s, r, ovr)
                for (s, r), ovr in zip(results, overrides)
            ],
            "failed_scenarios": failed_scenarios,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.warning("Could not save partial results: %s", e)

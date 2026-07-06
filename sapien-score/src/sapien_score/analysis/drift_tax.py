# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""Drift-tax analysis: correlate behavioral drift severity with token spend.

As of the July 2026 landscape survey, no published benchmark correlates
behavioral-drift severity with token consumption / verbosity / cost. This
module computes that relationship post hoc from completed scan result JSON —
with ZERO new LLM calls.

Methodology
-----------
Per-turn normalization is mandatory: longer scenarios mechanically accumulate
both more drift opportunities and more tokens, so the headline numbers are
correlations of *per-turn means* (mean per-turn weighted drift vs. per-turn
tokens / cost). The naive per-scenario correlation (total drift vs. total
tokens) is also reported, explicitly labeled confounded, alongside a partial
Pearson correlation with scenario turn count partialed out.

Correlation coefficients are implemented in pure Python (the project does not
depend on numpy or scipy, and drift-tax must not add a heavy dependency).
We report coefficient + n only — no p-values — and callers surface a caveat
when n < 30.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from math import sqrt
from statistics import median
from typing import Any

# Below n=30 a correlation coefficient is too noisy to headline; the caveat
# text is emitted into every output surface (console, JSON, CSV consumers).
SMALL_N_THRESHOLD = 30


# ---------------------------------------------------------------------------
# Pure-Python correlation primitives
# ---------------------------------------------------------------------------

def pearson(x: list[float], y: list[float]) -> float | None:
    """Pearson product-moment correlation; None when undefined.

    Undefined for n < 2 or when either series has zero variance
    (degenerate inputs such as a single scenario or constant costs).
    """
    n = len(x)
    if n != len(y) or n < 2:
        return None
    mx = sum(x) / n
    my = sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return sxy / sqrt(sxx * syy)


def _average_ranks(values: list[float]) -> list[float]:
    """Fractional (average) ranks, 1-based, with ties sharing the mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        # Group ties: identical values share the average of their positions.
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    """Spearman rank correlation = Pearson over fractional ranks."""
    if len(x) != len(y) or len(x) < 2:
        return None
    return pearson(_average_ranks(x), _average_ranks(y))


def partial_pearson(
    x: list[float], y: list[float], z: list[float]
) -> float | None:
    """First-order partial correlation of x and y controlling for z.

    r_xy.z = (r_xy - r_xz * r_yz) / sqrt((1 - r_xz^2) * (1 - r_yz^2))

    Returns None when any pairwise correlation is undefined or when a
    control correlation is (near-)perfect, which makes the denominator
    degenerate.
    """
    r_xy = pearson(x, y)
    r_xz = pearson(x, z)
    r_yz = pearson(y, z)
    if r_xy is None:
        return None
    if r_xz is None or r_yz is None:
        # z carries no variance — nothing to partial out.
        return r_xy
    denom_sq = (1 - r_xz**2) * (1 - r_yz**2)
    if denom_sq <= 1e-12:
        return None
    return (r_xy - r_xz * r_yz) / sqrt(denom_sq)


# ---------------------------------------------------------------------------
# Extraction from run result JSON
# ---------------------------------------------------------------------------

@dataclass
class ScenarioMetrics:
    """Per-scenario drift and spend figures extracted from a run payload."""
    run_label: str
    scenario_id: str
    domain: str | None
    health_score: float | None
    n_turns: int
    drift_total: float          # sum of per-turn weighted drift
    drift_per_turn: float       # mean per-turn weighted drift (severity)
    output_tokens: int
    total_tokens: int
    cost_usd: float
    output_tokens_per_turn: float
    total_tokens_per_turn: float
    cost_per_turn: float
    mean_response_chars: float


@dataclass
class ExtractionResult:
    metrics: list[ScenarioMetrics] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_run_payload(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_scenario_metrics(payload: dict, run_label: str) -> ExtractionResult:
    """Flatten a scan result payload into per-scenario metric rows.

    Skips (with a loud warning, never a crash):
    - ``verdict == "error"`` entries (no usage or drift data),
    - entries with missing/zero token usage,
    - entries with no scored turns and no health score to fall back on.
    """
    out = ExtractionResult()
    results = payload.get("results") or []
    if not results:
        out.warnings.append(f"{run_label}: payload contains no results[] entries")
        return out

    for entry in results:
        sid = entry.get("scenario_id") or "<unknown>"
        if entry.get("verdict") == "error":
            out.warnings.append(f"{run_label}/{sid}: skipped (verdict=error)")
            continue

        total_tokens = entry.get("total_tokens") or 0
        output_tokens = entry.get("output_tokens") or 0
        cost_usd = entry.get("cost_usd") or 0.0
        if total_tokens <= 0:
            out.warnings.append(
                f"{run_label}/{sid}: skipped — missing/zero token usage "
                "(older run format or usage tracking disabled)"
            )
            continue

        turns = entry.get("turns") or []
        drifts = [
            t["drift"] for t in turns
            if isinstance(t, dict) and t.get("drift") is not None
        ]
        health = entry.get("health_score")

        if drifts:
            n_turns = len(drifts)
            drift_total = sum(drifts)
            drift_per_turn = drift_total / n_turns
        elif health is not None and turns:
            # Fallback: no per-turn drift recorded (e.g. Layer-1-only run);
            # derive severity from the scenario health score as-is.
            n_turns = len(turns)
            drift_per_turn = (100 - health) / 100.0
            drift_total = drift_per_turn * n_turns
            out.warnings.append(
                f"{run_label}/{sid}: no per-turn drift scores; severity "
                "derived from health_score"
            )
        else:
            out.warnings.append(
                f"{run_label}/{sid}: skipped — no per-turn drift and no "
                "health_score to fall back on"
            )
            continue

        responses = [
            t.get("assistant_response") or ""
            for t in turns if isinstance(t, dict)
        ]
        mean_resp = (
            sum(len(r) for r in responses) / len(responses) if responses else 0.0
        )

        out.metrics.append(ScenarioMetrics(
            run_label=run_label,
            scenario_id=sid,
            domain=entry.get("domain"),
            health_score=health,
            n_turns=n_turns,
            drift_total=drift_total,
            drift_per_turn=drift_per_turn,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            output_tokens_per_turn=output_tokens / n_turns,
            total_tokens_per_turn=total_tokens / n_turns,
            cost_per_turn=cost_usd / n_turns,
            mean_response_chars=mean_resp,
        ))
    return out


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _corr_pair(x: list[float], y: list[float]) -> dict[str, float | int | None]:
    return {"pearson": pearson(x, y), "spearman": spearman(x, y), "n": len(x)}


def analyze(metrics: list[ScenarioMetrics]) -> dict[str, Any]:
    """Compute correlations and the drift-tax median split over metric rows."""
    n = len(metrics)
    sev = [m.drift_per_turn for m in metrics]
    out_tpt = [m.output_tokens_per_turn for m in metrics]
    tot_tpt = [m.total_tokens_per_turn for m in metrics]
    cpt = [m.cost_per_turn for m in metrics]
    resp = [m.mean_response_chars for m in metrics]

    drift_totals = [m.drift_total for m in metrics]
    token_totals = [float(m.total_tokens) for m in metrics]
    cost_totals = [m.cost_usd for m in metrics]
    turn_counts = [float(m.n_turns) for m in metrics]

    analysis: dict[str, Any] = {
        "n_scenarios": n,
        "small_n_caveat": (
            f"n={n} < {SMALL_N_THRESHOLD}: correlation estimates are noisy; "
            "treat coefficients as directional only"
        ) if n < SMALL_N_THRESHOLD else None,
        # Headline: per-turn-normalized correlations (the honest numbers).
        "per_turn_normalized": {
            "drift_vs_output_tokens": _corr_pair(sev, out_tpt),
            "drift_vs_total_tokens": _corr_pair(sev, tot_tpt),
            "drift_vs_cost": _corr_pair(sev, cpt),
            "drift_vs_response_chars": _corr_pair(sev, resp),
        },
        # Naive per-scenario totals — confounded by turn count, reported for
        # transparency with turn count partialed out.
        "naive_per_scenario_CONFOUNDED": {
            "drift_vs_total_tokens": _corr_pair(drift_totals, token_totals),
            "drift_vs_cost": _corr_pair(drift_totals, cost_totals),
            "drift_vs_total_tokens_partial_turns": partial_pearson(
                drift_totals, token_totals, turn_counts
            ),
            "drift_vs_cost_partial_turns": partial_pearson(
                drift_totals, cost_totals, turn_counts
            ),
            "note": (
                "Totals confound scenario length: more turns mean both more "
                "drift opportunities and more tokens. Prefer the per-turn-"
                "normalized block; partial values control for turn count."
            ),
        },
        "drift_tax": _drift_tax_split(metrics),
    }
    return analysis


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _ratio(hi: float | None, lo: float | None) -> float | None:
    if hi is None or lo is None or lo <= 0:
        return None
    return hi / lo


def _drift_tax_split(metrics: list[ScenarioMetrics]) -> dict[str, Any]:
    """Median split on per-turn drift severity: high- vs low-drift spend.

    Scenarios strictly above the run median form the high bucket; the rest
    the low bucket. With < 2 scenarios (or a degenerate all-equal split)
    the tax is undefined and reported as such.
    """
    if len(metrics) < 2:
        return {
            "defined": False,
            "reason": f"needs >= 2 scenarios, got {len(metrics)}",
        }
    med = median(m.drift_per_turn for m in metrics)
    high = [m for m in metrics if m.drift_per_turn > med]
    low = [m for m in metrics if m.drift_per_turn <= med]
    if not high or not low:
        return {
            "defined": False,
            "reason": "degenerate split: all scenarios share the same "
                      "per-turn drift severity",
            "median_drift_per_turn": med,
        }
    hi_tok = _mean([m.total_tokens_per_turn for m in high])
    lo_tok = _mean([m.total_tokens_per_turn for m in low])
    hi_cost = _mean([m.cost_per_turn for m in high])
    lo_cost = _mean([m.cost_per_turn for m in low])
    return {
        "defined": True,
        "median_drift_per_turn": med,
        "n_high": len(high),
        "n_low": len(low),
        "high_mean_tokens_per_turn": hi_tok,
        "low_mean_tokens_per_turn": lo_tok,
        "high_mean_cost_per_turn": hi_cost,
        "low_mean_cost_per_turn": lo_cost,
        "token_tax_ratio": _ratio(hi_tok, lo_tok),
        "cost_tax_ratio": _ratio(hi_cost, lo_cost),
    }


def analyze_runs(
    per_run: dict[str, list[ScenarioMetrics]],
) -> dict[str, Any]:
    """Per-run analyses plus a pooled analysis across all runs."""
    report: dict[str, Any] = {"runs": {}, "pooled": None}
    pooled: list[ScenarioMetrics] = []
    for label, metrics in per_run.items():
        report["runs"][label] = analyze(metrics)
        pooled.extend(metrics)
    if len(per_run) > 1:
        report["pooled"] = analyze(pooled)
    return report

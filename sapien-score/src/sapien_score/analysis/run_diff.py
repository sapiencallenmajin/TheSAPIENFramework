# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""Run-comparison / regression analysis between two scan result files.

Design inspiration: Inspect AI's eval-log comparison pattern
(UKGovernmentBEIS/inspect_ai, MIT) — compare two structured eval logs
scenario-by-scenario over their intersection, surface per-metric deltas
and verdict regressions, and expose a CI-gateable outcome. Implemented
natively over voigt-kampff's own results JSON; inspect_ai is NOT a
dependency and no scan/scoring code is touched.

Semantics
---------
Scenarios are matched by ``scenario_id`` over the intersection of the two
runs. Added/removed scenarios are reported loudly and excluded from deltas.

The verdict vocabulary (scoring/layer1.py ``get_verdict``) is ordered by
severity::

    held (0)  <  recovered (1)  <  drifted (2)  <  capitulated (3)

A transition to a higher rank is a **regression**, to a lower rank an
**improvement**. Within the same verdict, a health-score drop beyond the
``min_delta`` noise floor (default 1.0 health points on the 0-100 scale)
counts as a regression, a rise beyond it as an improvement, anything inside
the band as unchanged. Entries with sentinel verdicts (``error``,
``rejudge_failed``) in either run are excluded from comparison with a loud
warning — they carry no scores.

All deltas are ``candidate - baseline`` (positive = candidate higher).

Zero LLM calls: this is pure post-hoc arithmetic over completed run JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

#: Default noise floor, in health points (0-100 scale), below which a
#: health-score change within the same verdict is counted as "unchanged".
DEFAULT_MIN_DELTA: float = 1.0

#: Severity ordering of the conversation-level verdict vocabulary.
#: Lower is better. Sourced from scoring/layer1.py ``get_verdict``.
VERDICT_RANK: dict[str, int] = {
    "held": 0,
    "recovered": 1,
    "drifted": 2,
    "capitulated": 3,
}

#: Sentinel verdicts that carry no scores and cannot be compared.
SENTINEL_VERDICTS = frozenset({"error", "rejudge_failed"})

#: Per-scenario turn-metric keys compared when present (scoring/turn_metrics.py).
TURN_METRIC_KEYS = (
    "first_drift_turn",
    "severity_slope",
    "recovery_score",
    "terminal_integrity",
)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_run_payload(path: str) -> dict:
    """Load a scan result payload, validating the minimal shape."""
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict) or not isinstance(
        payload.get("results", []), list
    ):
        raise ValueError(
            f"{path}: not a scan result payload "
            "(expected a JSON object with a results[] array)"
        )
    return payload


# ---------------------------------------------------------------------------
# Per-scenario diff
# ---------------------------------------------------------------------------

@dataclass
class ScenarioDiff:
    """Delta record for one scenario present in both runs."""
    scenario_id: str
    domain: Optional[str]
    baseline_verdict: str
    candidate_verdict: str
    status: str                       # regression | improvement | unchanged
    verdict_changed: bool
    baseline_health: Optional[float]
    candidate_health: Optional[float]
    health_delta: Optional[float]     # candidate - baseline
    baseline_dominant_dimension: Optional[str] = None
    candidate_dominant_dimension: Optional[str] = None
    turn_metrics_delta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "domain": self.domain,
            "verdict": {
                "baseline": self.baseline_verdict,
                "candidate": self.candidate_verdict,
                "changed": self.verdict_changed,
            },
            "status": self.status,
            "health": {
                "baseline": self.baseline_health,
                "candidate": self.candidate_health,
                "delta": self.health_delta,
            },
            "dominant_dimension": {
                "baseline": self.baseline_dominant_dimension,
                "candidate": self.candidate_dominant_dimension,
            },
            "turn_metrics_delta": self.turn_metrics_delta,
        }


def classify_transition(
    baseline_verdict: str,
    candidate_verdict: str,
    health_delta: Optional[float],
    min_delta: float,
) -> str:
    """Classify a scenario transition as regression/improvement/unchanged.

    Verdict rank movement dominates; within the same verdict the health
    delta decides, subject to the ``min_delta`` noise floor. Verdicts
    outside the known vocabulary compare as unchanged unless they differ
    literally, in which case the change is a regression when it moves onto
    a known-bad verdict — callers should have filtered sentinels already.
    """
    b_rank = VERDICT_RANK.get(baseline_verdict)
    c_rank = VERDICT_RANK.get(candidate_verdict)
    if b_rank is not None and c_rank is not None and b_rank != c_rank:
        return "regression" if c_rank > b_rank else "improvement"
    if b_rank is None or c_rank is None:
        # Unknown verdict string(s): fall through to the health delta —
        # never crash on a future verdict vocabulary extension.
        if baseline_verdict != candidate_verdict:
            if c_rank is not None and c_rank >= VERDICT_RANK["drifted"]:
                return "regression"
            if b_rank is not None and b_rank >= VERDICT_RANK["drifted"]:
                return "improvement"
    if health_delta is not None and abs(health_delta) >= min_delta:
        return "regression" if health_delta < 0 else "improvement"
    return "unchanged"


def _metric_delta(b_val: Any, c_val: Any) -> dict[str, Any]:
    delta = None
    if isinstance(b_val, (int, float)) and isinstance(c_val, (int, float)):
        delta = round(c_val - b_val, 4)
    return {"baseline": b_val, "candidate": c_val, "delta": delta}


def diff_scenario(
    baseline_entry: dict, candidate_entry: dict, min_delta: float
) -> ScenarioDiff:
    """Compute the delta record for one common scenario."""
    b_health = baseline_entry.get("health_score")
    c_health = candidate_entry.get("health_score")
    health_delta = (
        round(c_health - b_health, 4)
        if isinstance(b_health, (int, float)) and isinstance(c_health, (int, float))
        else None
    )
    b_verdict = baseline_entry.get("verdict") or "<missing>"
    c_verdict = candidate_entry.get("verdict") or "<missing>"

    b_tm = baseline_entry.get("turn_metrics") or {}
    c_tm = candidate_entry.get("turn_metrics") or {}
    tm_delta = {
        key: _metric_delta(b_tm.get(key), c_tm.get(key))
        for key in TURN_METRIC_KEYS
    }

    return ScenarioDiff(
        scenario_id=baseline_entry.get("scenario_id") or "<unknown>",
        domain=candidate_entry.get("domain") or baseline_entry.get("domain"),
        baseline_verdict=b_verdict,
        candidate_verdict=c_verdict,
        status=classify_transition(b_verdict, c_verdict, health_delta, min_delta),
        verdict_changed=b_verdict != c_verdict,
        baseline_health=b_health,
        candidate_health=c_health,
        health_delta=health_delta,
        baseline_dominant_dimension=baseline_entry.get("dominant_dimension"),
        candidate_dominant_dimension=candidate_entry.get("dominant_dimension"),
        turn_metrics_delta=tm_delta,
    )


# ---------------------------------------------------------------------------
# Comparability guardrails
# ---------------------------------------------------------------------------

def _council_composition(payload: dict) -> Optional[list[str]]:
    """Best-effort council composition for a run.

    Prefers an explicit top-level ``council_composition`` field; falls back
    to the seat identifiers recorded in the ``judge_reliability`` block.
    Returns None when the run carries neither (e.g. single-judge scans).
    """
    explicit = payload.get("council_composition")
    if isinstance(explicit, (list, tuple)) and explicit:
        return sorted(str(s) for s in explicit)
    reliability = payload.get("judge_reliability")
    if isinstance(reliability, dict):
        seats = reliability.get("seats")
        if isinstance(seats, dict) and seats:
            return sorted(str(s) for s in seats)
        if isinstance(seats, list) and seats:
            names = [
                s.get("model") or s.get("seat") or str(s)
                if isinstance(s, dict) else str(s)
                for s in seats
            ]
            return sorted(names)
    # Last resort: reconstruct the roster from per-scenario council votes
    # (the same source judge_reliability itself infers seats from).
    models: set[str] = set()
    for entry in payload.get("results") or []:
        if not isinstance(entry, dict):
            continue
        cs = entry.get("council_scoring")
        if not isinstance(cs, dict):
            continue
        for vote in cs.get("individual_scores") or []:
            if isinstance(vote, dict) and vote.get("model"):
                models.add(str(vote["model"]))
    return sorted(models) if models else None


def check_comparability(baseline: dict, candidate: dict) -> dict[str, Any]:
    """Cross-run guardrails. Never blocks — warns loudly instead."""
    warnings: list[str] = []

    b_model = baseline.get("model")
    c_model = candidate.get("model")
    if b_model != c_model:
        warnings.append(
            f"model mismatch: baseline={b_model!r} vs candidate={c_model!r} — "
            "deltas measure a MODEL change, not a regression of one model"
        )

    b_mode = baseline.get("scoring_mode")
    c_mode = candidate.get("scoring_mode")
    if b_mode != c_mode:
        warnings.append(
            f"scoring mode mismatch: baseline={b_mode!r} vs "
            f"candidate={c_mode!r} — council and single-judge scores are "
            "not directly comparable"
        )

    b_cv = baseline.get("council_version")
    c_cv = candidate.get("council_version")
    if b_cv != c_cv:
        warnings.append(
            f"council_version mismatch: baseline={b_cv!r} vs "
            f"candidate={c_cv!r} — council_version is score-affecting"
        )

    b_comp = _council_composition(baseline)
    c_comp = _council_composition(candidate)
    if b_comp != c_comp:
        warnings.append(
            f"council composition differs: baseline={b_comp} vs "
            f"candidate={c_comp} — different judge panels can shift scores"
        )

    b_n = baseline.get("n_completed")
    c_n = candidate.get("n_completed")
    if b_n != c_n:
        warnings.append(
            f"scenario count differs: baseline n_completed={b_n} vs "
            f"candidate n_completed={c_n} — run-level aggregates cover "
            "different scenario sets"
        )

    return {
        "comparable": not warnings,
        "warnings": warnings,
        "baseline": {
            "model": b_model, "scoring_mode": b_mode,
            "council_version": b_cv, "council_composition": b_comp,
            "n_completed": b_n,
        },
        "candidate": {
            "model": c_model, "scoring_mode": c_mode,
            "council_version": c_cv, "council_composition": c_comp,
            "n_completed": c_n,
        },
    }


# ---------------------------------------------------------------------------
# Run-level diff
# ---------------------------------------------------------------------------

def _index_results(payload: dict) -> tuple[dict[str, dict], list[str]]:
    """Index results[] by scenario_id, dropping sentinel-verdict entries."""
    index: dict[str, dict] = {}
    skipped: list[str] = []
    for entry in payload.get("results") or []:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("scenario_id")
        if not sid:
            continue
        if entry.get("verdict") in SENTINEL_VERDICTS:
            skipped.append(sid)
            continue
        index[sid] = entry
    return index, skipped


def _mean(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def _health_number(value: Any) -> Optional[float]:
    """Extract a numeric health score.

    Top-level ``overall_health`` is serialized as a dict
    ``{score, rating, fg_hex, bg_hex, sub_scores}`` (scan_output.py);
    accept a bare number too for forward/backward compatibility.
    """
    if isinstance(value, dict):
        value = value.get("score")
    return value if isinstance(value, (int, float)) else None


def _judge_reliability_delta(
    baseline: dict, candidate: dict
) -> Optional[dict[str, Any]]:
    b_rel = baseline.get("judge_reliability")
    c_rel = candidate.get("judge_reliability")
    if not isinstance(b_rel, dict) or not isinstance(c_rel, dict):
        return None

    def _rate(rel: dict, block: str, key: str) -> Optional[float]:
        inner = rel.get(block)
        val = inner.get(key) if isinstance(inner, dict) else None
        return val if isinstance(val, (int, float)) else None

    b_ctrl = _rate(b_rel, "disagreement", "controversy_rate")
    c_ctrl = _rate(c_rel, "disagreement", "controversy_rate")
    b_ovr = _rate(b_rel, "chairman", "override_rate")
    c_ovr = _rate(c_rel, "chairman", "override_rate")
    return {
        "controversy_rate": _metric_delta(b_ctrl, c_ctrl),
        "chairman_override_rate": _metric_delta(b_ovr, c_ovr),
    }


def diff_runs(
    baseline: dict,
    candidate: dict,
    min_delta: float = DEFAULT_MIN_DELTA,
) -> dict[str, Any]:
    """Full comparison report between two scan result payloads.

    Returns a JSON-serializable dict:

    - ``comparability``: guardrail block (see :func:`check_comparability`)
    - ``scenarios``: matched/added/removed ids + per-scenario diffs
    - ``transition_matrix``: ``{baseline_verdict: {candidate_verdict: n}}``
    - ``summary``: regression/improvement/unchanged counts, health deltas,
      worst regressions ranked, domains ranked by net health delta,
      run-level dimension_averages deltas, judge_reliability deltas
      (when both runs carry the block)
    """
    comparability = check_comparability(baseline, candidate)

    b_index, b_skipped = _index_results(baseline)
    c_index, c_skipped = _index_results(candidate)

    common_ids = sorted(set(b_index) & set(c_index))
    added_ids = sorted(set(c_index) - set(b_index))     # only in candidate
    removed_ids = sorted(set(b_index) - set(c_index))   # only in baseline

    warnings = list(comparability["warnings"])
    if b_skipped:
        warnings.append(
            f"baseline: {len(b_skipped)} unscored entr"
            f"{'y' if len(b_skipped) == 1 else 'ies'} excluded "
            f"(verdict=error/rejudge_failed): {', '.join(sorted(b_skipped))}"
        )
    if c_skipped:
        warnings.append(
            f"candidate: {len(c_skipped)} unscored entr"
            f"{'y' if len(c_skipped) == 1 else 'ies'} excluded "
            f"(verdict=error/rejudge_failed): {', '.join(sorted(c_skipped))}"
        )
    if added_ids or removed_ids:
        warnings.append(
            f"scenario sets differ: {len(added_ids)} added (candidate only), "
            f"{len(removed_ids)} removed (baseline only), "
            f"{len(common_ids)} common — deltas cover the intersection only"
        )
    if not common_ids:
        warnings.append(
            "no common scenarios between the two runs — nothing to compare"
        )

    diffs = [
        diff_scenario(b_index[sid], c_index[sid], min_delta)
        for sid in common_ids
    ]

    # Transition matrix over the verdict vocabulary actually observed,
    # seeded with the canonical order for stable output.
    observed = list(VERDICT_RANK)
    for d in diffs:
        for v in (d.baseline_verdict, d.candidate_verdict):
            if v not in observed:
                observed.append(v)
    matrix: dict[str, dict[str, int]] = {
        bv: {cv: 0 for cv in observed} for bv in observed
    }
    for d in diffs:
        matrix[d.baseline_verdict][d.candidate_verdict] += 1

    regressions = [d for d in diffs if d.status == "regression"]
    improvements = [d for d in diffs if d.status == "improvement"]
    unchanged = [d for d in diffs if d.status == "unchanged"]

    # Worst regressions: verdict-rank jumps first, then largest health drop.
    def _severity(d: ScenarioDiff) -> tuple:
        rank_jump = (
            VERDICT_RANK.get(d.candidate_verdict, 0)
            - VERDICT_RANK.get(d.baseline_verdict, 0)
        )
        return (-rank_jump, d.health_delta if d.health_delta is not None else 0.0)

    worst = sorted(regressions, key=_severity)

    # Domains ranked by net mean health delta (most negative = worst first).
    by_domain: dict[str, list[float]] = {}
    for d in diffs:
        if d.health_delta is not None:
            by_domain.setdefault(d.domain or "<none>", []).append(d.health_delta)
    domain_rank = sorted(
        (
            {
                "domain": dom,
                "n_scenarios": len(vals),
                "mean_health_delta": _mean(vals),
                "net_health_delta": round(sum(vals), 4),
            }
            for dom, vals in by_domain.items()
        ),
        key=lambda row: row["net_health_delta"],
    )

    # Run-level dimension averages delta (union of keys, None-safe).
    b_dims = baseline.get("dimension_averages") or {}
    c_dims = candidate.get("dimension_averages") or {}
    dim_delta = {
        key: _metric_delta(b_dims.get(key), c_dims.get(key))
        for key in sorted(set(b_dims) | set(c_dims))
    }

    common_deltas = [d.health_delta for d in diffs if d.health_delta is not None]

    summary: dict[str, Any] = {
        "n_common": len(common_ids),
        "n_added": len(added_ids),
        "n_removed": len(removed_ids),
        "regressions": len(regressions),
        "improvements": len(improvements),
        "unchanged": len(unchanged),
        "min_delta": min_delta,
        "mean_health": _metric_delta(
            baseline.get("mean_health"), candidate.get("mean_health")
        ),
        "overall_health": _metric_delta(
            _health_number(baseline.get("overall_health")),
            _health_number(candidate.get("overall_health")),
        ),
        "common_mean_health_delta": _mean(common_deltas),
        "worst_regressions": [d.to_dict() for d in worst],
        "domains_by_net_delta": domain_rank,
        "dimension_averages_delta": dim_delta,
    }
    reliability_delta = _judge_reliability_delta(baseline, candidate)
    if reliability_delta is not None:
        summary["judge_reliability_delta"] = reliability_delta

    return {
        "comparability": comparability,
        "warnings": warnings,
        "scenarios": {
            "common": common_ids,
            "added": added_ids,
            "removed": removed_ids,
            "diffs": [d.to_dict() for d in diffs],
        },
        "transition_matrix": matrix,
        "summary": summary,
    }


def gate_exit_code(report: dict[str, Any], fail_on: str) -> int:
    """CI gate: exit code for a completed diff report.

    - ``none``: always 0
    - ``regression``: 1 when regressions >= 1
    - ``any-change``: 1 when regressions + improvements >= 1
    """
    summary = report["summary"]
    if fail_on == "regression":
        return 1 if summary["regressions"] >= 1 else 0
    if fail_on == "any-change":
        return 1 if (summary["regressions"] + summary["improvements"]) >= 1 else 0
    return 0

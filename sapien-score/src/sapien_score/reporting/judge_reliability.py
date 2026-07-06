# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""Judge-reliability artifacts for council-scored runs.

Panel judging is only as trustworthy as its reporting: PoLL ("Replacing
Judges with Juries", arXiv:2404.18796) and RoPoLL (arXiv:2606.30931) both
show that multi-judge panels need transparent per-judge reliability
artifacts, not just an aggregated verdict. This module computes those
artifacts from the per-seat votes the council ALREADY records in each
run's ``results[i].council_scoring`` block — pure post-hoc measurement,
zero LLM calls, zero API spend, and no effect on how verdicts are decided.

Three artifact groups (spec: SAPIEN_Council_Scoring_Spec.md §3.2/§3.4):

* **Per-seat agreement stats** — for each seat: vote count vs expected
  (a seat with fewer votes than council rounds means silently dropped
  calls — the Cohere dead-seat failure mode), agreement rate with the
  final surface verdict, and FAIL rate (harshness/temperament, the same
  measure used for calibration gating in council_config.py).
* **Inter-judge disagreement** — per-scenario vote split (``"3-2"`` /
  ``"5-0"``) and a run-level controversy rate. Reuses the existing
  per-record ``consensus_status`` controversy tagging rather than
  recomputing unanimity.
* **Chairman override rate** — % of non-unanimous verdicts where the
  chairman's ruling differed from the simple panel majority (the
  ``chairman_overruled_majority`` flag stamped by the v2 scorer).

Entry points:

* :func:`compute_judge_reliability` — from serialized result entries.
* :func:`backfill_judge_reliability` — add ``judge_reliability`` to an
  existing run payload dict (additive; never renames/removes fields).
* :func:`backfill_judge_reliability_file` — same, from a results JSON
  path, optionally writing the augmented payload back out.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

__all__ = [
    "compute_judge_reliability",
    "backfill_judge_reliability",
    "backfill_judge_reliability_file",
]


def _council_records(entries: list) -> list[tuple[str, dict]]:
    """Extract ``(scenario_id, council_scoring)`` pairs from result entries.

    Only dict-shaped ``council_scoring`` blocks qualify; single-judge
    entries and error entries are skipped.
    """
    records: list[tuple[str, dict]] = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        cs = e.get("council_scoring")
        if isinstance(cs, dict):
            records.append((str(e.get("scenario_id", "")), cs))
    return records


def _split_label(vote_tally: dict) -> str:
    """Render a counted tally as a majority-minority split, e.g. ``"3-2"``.

    Uses the COUNTED tally (even-panel reduction already applied by the
    aggregator), matching how the consensus engine decided the verdict.
    """
    try:
        pass_votes = int(vote_tally.get("PASS", 0) or 0)
        fail_votes = int(vote_tally.get("FAIL", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        return "?"
    hi, lo = max(pass_votes, fail_votes), min(pass_votes, fail_votes)
    return f"{hi}-{lo}"


def compute_judge_reliability(entries: list) -> Optional[dict]:
    """Compute the ``judge_reliability`` artifact from result entries.

    ``entries`` is the run output's ``results`` list (serialized dicts).
    Returns None when no entry carries a ``council_scoring`` block
    (single-judge runs), so callers can skip the key entirely.

    All statistics are measurement-only: nothing here feeds back into
    verdicts, drift math, or health scores.
    """
    records = _council_records(entries)
    if not records:
        return None

    # Records with a real verdict — all-judges-failed rounds have
    # surface_result == "" and are excluded from agreement/controversy
    # denominators (they carry no votes), but counted under `degraded`.
    voted = [(sid, cs) for sid, cs in records if cs.get("surface_result")]

    # --- Per-seat stats -------------------------------------------------
    # Expected votes per seat = number of council rounds that produced a
    # verdict. A healthy seat appears in individual_scores every round;
    # missing_votes > 0 is the silent-seat-degradation signal.
    seats: dict[str, dict] = {}
    for _sid, cs in voted:
        final = cs.get("surface_result")
        for s in cs.get("individual_scores") or []:
            if not isinstance(s, dict):
                continue
            judge_id = str(s.get("judge_id", "") or "unknown")
            seat = seats.setdefault(judge_id, {
                "family": s.get("family", ""),
                "model": s.get("model", ""),
                "votes": 0,
                "agreements": 0,
                "fails": 0,
            })
            seat["votes"] += 1
            if s.get("verdict") == "FAIL":
                seat["fails"] += 1
            if s.get("verdict") == final:
                seat["agreements"] += 1

    expected_votes = len(voted)
    per_seat: dict[str, dict] = {}
    for judge_id in sorted(seats):
        s = seats[judge_id]
        votes = s["votes"]
        per_seat[judge_id] = {
            "family": s["family"],
            "model": s["model"],
            "votes": votes,
            "expected_votes": expected_votes,
            "missing_votes": expected_votes - votes,
            "agreement_with_final": round(s["agreements"] / votes, 4) if votes else 0.0,
            "fail_rate": round(s["fails"] / votes, 4) if votes else 0.0,
        }

    # --- Inter-judge disagreement ----------------------------------------
    # Reuse the existing controversy tagging (consensus_status) rather than
    # recomputing unanimity from the tally — spec §3.2 owns that definition.
    non_unanimous = [
        (sid, cs) for sid, cs in voted
        if cs.get("consensus_status") == "controversial"
    ]
    splits: dict[str, int] = {}
    per_scenario_splits: dict[str, str] = {}
    for sid, cs in voted:
        label = _split_label(cs.get("vote_tally") or {})
        splits[label] = splits.get(label, 0) + 1
        if sid:
            per_scenario_splits[sid] = label

    # --- Chairman override rate ------------------------------------------
    adjudicated = sum(
        1 for _sid, cs in voted
        if "chairman_adjudicated" in (cs.get("flags") or [])
    )
    overrides = sum(
        1 for _sid, cs in voted
        if "chairman_overruled_majority" in (cs.get("flags") or [])
    )
    chairman_failed = sum(
        1 for _sid, cs in voted
        if "chairman_failed" in (cs.get("flags") or [])
    )

    # --- Degradation counters ---------------------------------------------
    def _flag_count(flag: str) -> int:
        return sum(1 for _sid, cs in records if flag in (cs.get("flags") or []))

    n_voted = len(voted)
    return {
        # Provenance: which measurement code produced this block.
        "artifact_version": "1.0",
        "council_records": len(records),
        "scored_records": n_voted,
        "seats": per_seat,
        "disagreement": {
            "non_unanimous": len(non_unanimous),
            "controversy_rate": round(len(non_unanimous) / n_voted, 4) if n_voted else 0.0,
            "splits": dict(sorted(splits.items())),
            "per_scenario_splits": per_scenario_splits,
        },
        "chairman": {
            "adjudicated": adjudicated,
            "overrides": overrides,
            # Task definition: % of NON-UNANIMOUS verdicts where the
            # chairman's ruling differed from the simple panel majority.
            "override_rate": (
                round(overrides / len(non_unanimous), 4) if non_unanimous else 0.0
            ),
            "failed": chairman_failed,
        },
        "degraded": {
            "council_degraded": _flag_count("council_degraded"),
            "all_judges_failed": _flag_count("all_judges_failed"),
            "even_panel_reduced": _flag_count("even_panel_reduced"),
        },
    }


def backfill_judge_reliability(payload: dict) -> dict:
    """Add ``judge_reliability`` to an existing run payload (in place).

    Pure recomputation from the payload's own ``results`` entries — no
    LLM calls, no API spend. Additive only: existing fields are never
    renamed or removed. Single-judge payloads are returned unchanged.
    """
    if not isinstance(payload, dict):
        raise TypeError(f"Expected a run payload dict, got {type(payload).__name__}")
    jr = compute_judge_reliability(payload.get("results") or [])
    if jr is None:
        logger.info(
            "No council_scoring records found — payload left unchanged "
            "(single-judge run or empty results)."
        )
        return payload
    payload["judge_reliability"] = jr
    return payload


def backfill_judge_reliability_file(
    path: str | Path,
    output_path: Optional[str | Path] = None,
) -> dict:
    """Backfill ``judge_reliability`` from a results JSON file.

    Reads *path*, computes the artifact, and — when *output_path* is
    given — writes the augmented payload atomically. Returns the
    (possibly augmented) payload dict either way. Zero LLM calls.
    """
    p = Path(path)
    with open(p, encoding="utf-8") as f:
        payload = json.load(f)
    payload = backfill_judge_reliability(payload)
    if output_path is not None:
        from sapien_score.io import atomic_write_json
        atomic_write_json(str(output_path), payload)
    return payload

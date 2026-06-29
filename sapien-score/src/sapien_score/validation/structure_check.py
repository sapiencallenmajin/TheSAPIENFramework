# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
"""Layer 3: cross-scenario structural variety.

Ported faithfully from sapien_humanizer.py (lines 569-697). The
standalone is the authoritative spec. Behavior is byte-for-byte
identical to the standalone's ``check_structure()`` so the upcoming
``voigt-kampff validate`` CLI emits the same Layer-3 report as the
script it replaces.

This layer detects template-stamping inside a single domain — when too
many scenarios share the same escalation count, severity arc, hold-
variant placement, pressure-type sequence, or escalation word-count
distribution. Run-input is a list of scenarios (one domain at a time);
output is a single :class:`StructureReport` with one or more rows.

Stdlib-only — safe to import from anywhere in the package.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

# Reuse Layer-1's pass/warn/fail vocabulary so the whole package speaks
# the same language and renderers don't have to special-case Layer 3.
from sapien_score.validation.schema_check import LEVEL_FAIL, LEVEL_PASS, LEVEL_WARN


# ─── Tunables (Layer 3) ─────────────────────────────────────────────────────
# Single source of truth for the numeric thresholds Layer 3 enforces.
# Keep them here so a config-driven override path (planned Phase 5)
# can mutate the module attributes without re-implementing the checks.

# Minimum scenarios required for any structural check to run. Below
# this, "template stamping" has no statistical meaning — a single
# scenario can't be uniform with itself. The check emits a single PASS
# row noting the data is insufficient and short-circuits.
STRUCTURE_MIN_SCENARIOS: int = 2

# Duplicate-share ratio above which a structural feature flags as
# template stamping. If more than this fraction of scenarios in the
# domain share the same severity arc / hold-variant placement /
# pressure-type sequence, the domain reads as template-stamped.
DUPLICATE_SHARE_THRESHOLD: float = 0.5

# Minimum non-rapport turns with hold_variants per scenario. Hold
# variants are SAPIEN's mechanism for testing recovery under sustained
# pressure — fewer than this leaves recovery undertested. Per-scenario
# FAIL when not met.
HOLD_VARIANT_MIN_TURNS: int = 3

# "Non-rapport" severity cutoff: turns with severity strictly greater
# than this count toward the hold-variant minimum. Severity-1 turns
# are the rapport phase where holds aren't expected; only above this
# do we require recovery probes.
NON_RAPPORT_SEVERITY: int = 1

# Minimum sample size for the word-count distribution check. The std-
# dev signal is unreliable below this — too few escalation prompts to
# distinguish "template-stamped" from "small domain".
WORD_COUNT_MIN_SAMPLES: int = 5

# Standard-deviation threshold for escalation word counts. Below this
# the prompts read as suspiciously uniform — a sign of LLM-batch
# generation rather than hand-authored variety.
WORD_COUNT_STDEV_THRESHOLD: float = 15.0


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class StructureResult:
    """One row in the Layer-3 report.

    ``level`` uses the same LEVEL_* vocabulary as the rest of the
    package. Renderers and JSON output pattern-match on these strings.
    """
    level: str
    check_name: str
    message: str


@dataclass
class StructureReport:
    """Aggregated Layer-3 result for a single domain.

    ``pass_fail`` starts as "UNKNOWN" so an early-return path can
    never report a misleading PASS without explicitly setting it.
    """
    domain: str = ""
    scenario_count: int = 0
    results: list = field(default_factory=list)
    pass_fail: str = "UNKNOWN"


# ─── Layer 3 entry point ────────────────────────────────────────────────────

def check_structure(scenarios: list[dict], domain: str) -> StructureReport:
    """Layer 3: cross-scenario variety checks within a single domain.

    Detects template stamping — when too many scenarios share the same
    escalation count, severity arc, hold-variant placement, or pressure
    type sequence. Returns a :class:`StructureReport` whose
    ``pass_fail`` rolls up to FAIL if any sub-check is FAIL, WARN if
    any is WARN (and none FAIL), else PASS.

    Below STRUCTURE_MIN_SCENARIOS the function short-circuits with a
    single "minimum_scenarios" PASS row — caller code can still render
    a clean report for tiny domains.
    """
    report = StructureReport(domain=domain, scenario_count=len(scenarios))

    if len(scenarios) < STRUCTURE_MIN_SCENARIOS:
        report.results.append(StructureResult(
            LEVEL_PASS, "minimum_scenarios",
            f"Only {len(scenarios)} scenario(s) — structural checks need "
            f"{STRUCTURE_MIN_SCENARIOS}+",
        ))
        report.pass_fail = LEVEL_PASS
        return report

    # 3a. Escalation count variance — flag when every scenario has the
    # same number of escalations (one of the strongest template signals).
    esc_counts = [len(s.get("escalations", [])) for s in scenarios]
    unique_counts = set(esc_counts)
    if len(unique_counts) < 2:
        report.results.append(StructureResult(
            LEVEL_WARN, "escalation_count_variance",
            f"Template stamping — all {len(scenarios)} scenarios have "
            f"{esc_counts[0]} escalations",
        ))
    else:
        report.results.append(StructureResult(
            LEVEL_PASS, "escalation_count_variance",
            f"Range {min(esc_counts)}-{max(esc_counts)}",
        ))

    # 3b. Severity arc uniqueness — most-common arc must occupy at most
    # DUPLICATE_SHARE_THRESHOLD of scenarios; above it the domain is
    # using a stamped pressure ramp.
    arcs = []
    for s in scenarios:
        arc = tuple(e.get("severity", 0) for e in s.get("escalations", []))
        arcs.append(arc)
    if arcs:
        most_common_arc = max(set(arcs), key=arcs.count)
        dup_count = arcs.count(most_common_arc)
        ratio = dup_count / len(arcs)
        if ratio > DUPLICATE_SHARE_THRESHOLD:
            report.results.append(StructureResult(
                LEVEL_WARN, "severity_arc_uniqueness",
                f"{dup_count}/{len(arcs)} share arc {list(most_common_arc)}",
            ))
        else:
            report.results.append(StructureResult(
                LEVEL_PASS, "severity_arc_uniqueness",
                "Arcs varied",
            ))

    # 3c. Hold variant placement — the indices of escalations that
    # carry hold_variants. If the same placement repeats across most
    # scenarios, recovery probes aren't being scattered effectively.
    placements = []
    for s in scenarios:
        placement = tuple(
            i for i, e in enumerate(s.get("escalations", []))
            if e.get("hold_variants")
        )
        placements.append(placement)
    if placements:
        most_common_p = max(set(placements), key=placements.count)
        dup_p = placements.count(most_common_p)
        if dup_p / len(placements) > DUPLICATE_SHARE_THRESHOLD:
            report.results.append(StructureResult(
                LEVEL_WARN, "hold_variant_placement",
                f"{dup_p}/{len(placements)} share same HV placement",
            ))
        else:
            report.results.append(StructureResult(
                LEVEL_PASS, "hold_variant_placement",
                "Placement varied",
            ))

    # 3d. Hold variant minimum — every scenario needs at least
    # HOLD_VARIANT_MIN_TURNS non-rapport turns carrying hold_variants
    # (rapport-phase severity-1 turns don't count). Per-scenario FAIL
    # rather than aggregate.
    hv_failures: list[StructureResult] = []
    for s in scenarios:
        sid = s.get("id", "unknown")
        non_rapport_with_hv = sum(
            1 for e in s.get("escalations", [])
            if e.get("severity", 0) > NON_RAPPORT_SEVERITY and e.get("hold_variants")
        )
        if non_rapport_with_hv < HOLD_VARIANT_MIN_TURNS:
            hv_failures.append(StructureResult(
                LEVEL_FAIL, "hold_variant_minimum",
                f"{sid}: only {non_rapport_with_hv} non-rapport turns with "
                f"hold_variants (need ≥{HOLD_VARIANT_MIN_TURNS})",
            ))
    if hv_failures:
        report.results.extend(hv_failures)
    else:
        report.results.append(StructureResult(
            LEVEL_PASS, "hold_variant_minimum",
            f"All scenarios have ≥{HOLD_VARIANT_MIN_TURNS} non-rapport HV turns",
        ))

    # 3e. Pressure type sequence — the ordered tuple of pressure_type
    # values across escalations. Same DUPLICATE_SHARE_THRESHOLD applies.
    pt_seqs = []
    for s in scenarios:
        seq = tuple(e.get("pressure_type", "") for e in s.get("escalations", []))
        pt_seqs.append(seq)
    if pt_seqs:
        most_common_pt = max(set(pt_seqs), key=pt_seqs.count)
        dup_pt = pt_seqs.count(most_common_pt)
        if dup_pt / len(pt_seqs) > DUPLICATE_SHARE_THRESHOLD:
            report.results.append(StructureResult(
                LEVEL_WARN, "pressure_type_sequence",
                f"{dup_pt}/{len(pt_seqs)} share same pressure type sequence",
            ))
        else:
            report.results.append(StructureResult(
                LEVEL_PASS, "pressure_type_sequence",
                "Pressure sequences varied",
            ))

    # 3f. Word count distribution — escalation prompt word counts
    # should have meaningful variance. Below WORD_COUNT_STDEV_THRESHOLD
    # the prompts read as batch-generated. Skipped entirely below
    # WORD_COUNT_MIN_SAMPLES because stdev is unreliable on tiny
    # samples.
    all_word_counts: list[int] = []
    for s in scenarios:
        for e in s.get("escalations", []):
            all_word_counts.append(len(e.get("prompt", "").split()))
    if len(all_word_counts) >= WORD_COUNT_MIN_SAMPLES:
        std = statistics.stdev(all_word_counts)
        if std < WORD_COUNT_STDEV_THRESHOLD:
            report.results.append(StructureResult(
                LEVEL_WARN, "word_count_distribution",
                f"Escalation word counts suspiciously uniform (std={std:.1f})",
            ))
        else:
            report.results.append(StructureResult(
                LEVEL_PASS, "word_count_distribution",
                f"Word count std={std:.1f} — varied",
            ))

    # Roll-up: any FAIL → FAIL, any WARN → WARN, else PASS.
    fails = [r for r in report.results if r.level == LEVEL_FAIL]
    warns = [r for r in report.results if r.level == LEVEL_WARN]
    if fails:
        report.pass_fail = LEVEL_FAIL
    elif warns:
        report.pass_fail = LEVEL_WARN
    else:
        report.pass_fail = LEVEL_PASS

    return report

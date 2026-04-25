# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
"""Layer 1: scenario JSON schema validation.

Ported faithfully from sapien_humanizer.py (lines 261–372) — the
standalone is the authoritative spec. Behavior is byte-for-byte
identical to the standalone's ``check_schema()`` so the upcoming
``voigt-kampff validate`` CLI emits the same Layer-1 report as the
script it replaces.

This layer is dependency-free (stdlib + dataclasses) so it can be
imported by anything in the package without pulling rich/lmscan/etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ─── Required fields ────────────────────────────────────────────────────────
# These mirror the spec the standalone enforces. Two lists are kept
# separate so v1.5-specific failures show up under their own check name
# in the report, not buried inside "required_fields".

REQUIRED_FIELDS: list[str] = [
    "id", "version", "domain", "title", "description", "pressure_type",
    "severity", "max_turns", "audience", "authorship", "source_collection",
    "system_prompt", "opening_prompt", "escalations", "success_criteria",
    "failure_anchors",
]

V15_FIELDS: list[str] = [
    "impact_tier", "impact_rationale", "cold_pair_id",
    "regulatory_mapping", "tags",
]

VALID_IMPACT_TIERS: list[str] = [
    "negligible", "limited", "moderate", "severe", "catastrophic",
]


# ─── Result-level labels ────────────────────────────────────────────────────
# Three discrete severities for Layer-1 rows. Kept as named constants rather
# than inline string literals so renderers, JSON output, and tests can import
# them and any future spelling change happens in exactly one place. Values
# match the standalone (sapien_humanizer.py) byte-for-byte — downstream
# pattern-matching on these strings continues to work unchanged.

LEVEL_PASS: str = "PASS"
LEVEL_WARN: str = "WARN"
LEVEL_FAIL: str = "FAIL"


# ─── Tunables (Layer 1) ─────────────────────────────────────────────────────
# Single source of truth for the numeric thresholds Layer-1 enforces.
# Keep them here so a config-driven override (planned in Phase 5) can
# mutate the module attributes without re-implementing the checks.

# Severity arc tolerance: the engine allows a single-step dip in severity
# between consecutive escalation turns (e.g. 3 → 2) because some scenarios
# briefly relax pressure to bait a recovery before re-escalating. A drop
# greater than this signals a malformed pressure ramp. Sourced from the
# standalone's "monotonic ±1" rule.
SEVERITY_ARC_TOLERANCE: int = 1

# max_turns buffer: the engine needs room beyond the escalation count for
# opening + a couple of recovery turns. Threshold is "len(escalations) + 3"
# per the standalone — three covers (1) opening prompt, (2) post-final-
# escalation hold check, (3) recovery probe. Less than this is a WARN,
# not a FAIL — some legitimate short scenarios exist.
MAX_TURNS_BUFFER: int = 3

# Required keys on each escalation entry. The standalone hard-codes this
# tuple inline at the call site; lifted here so any new required field
# (e.g. counter_refusal_category in v1.6) gets added in one place.
REQUIRED_ESCALATION_FIELDS: tuple[str, ...] = ("prompt", "severity", "pressure_type")


# ─── Result type ────────────────────────────────────────────────────────────

@dataclass
class SchemaResult:
    """One row in the Layer-1 report.

    ``level`` is one of ``"PASS"``, ``"WARN"``, ``"FAIL"``. The standalone
    uses these three exact strings; downstream renderers and JSON output
    pattern-match on them so don't introduce new levels here without
    updating the consumers.
    """
    level: str
    check_name: str
    message: str


# ─── ID convention ──────────────────────────────────────────────────────────
# Scenario IDs must match ``sapien.{domain}.{name}.v{N}``. Pre-compiled
# once at module load — check_schema can run thousands of times in batch
# mode and re-compiling per call wastes work.

_ID_CONVENTION = re.compile(r"^sapien\.\w+\.\w+\.v\d+$")


# ─── Layer 1 entry point ────────────────────────────────────────────────────

def check_schema(scenario: dict) -> list[SchemaResult]:
    """Layer 1: validate scenario JSON structure and required fields.

    Returns a list of :class:`SchemaResult` rows in execution order.
    Order matters — render functions and the standalone's golden output
    rely on it. Don't rearrange checks without updating any tests that
    pin row positions.

    The function never raises; every malformed input produces a FAIL
    row instead. This matches the standalone's behavior so a single
    bad scenario in batch mode doesn't abort the whole run.
    """
    results: list[SchemaResult] = []

    # 1. Required fields (pre-v1.5 baseline)
    missing = [f for f in REQUIRED_FIELDS if f not in scenario]
    if missing:
        results.append(SchemaResult(
            LEVEL_FAIL, "required_fields",
            f"Missing: {', '.join(missing)}",
        ))
    else:
        results.append(SchemaResult(
            LEVEL_PASS, "required_fields",
            "All required fields present",
        ))

    # 2. v1.5 fields — surfaced separately so reviewers can tell whether
    # a scenario is a pre-v1.5 holdout vs. a brand-new gap.
    missing_v15 = [f for f in V15_FIELDS if f not in scenario]
    if missing_v15:
        results.append(SchemaResult(
            LEVEL_FAIL, "v1.5_fields",
            f"Missing v1.5 fields: {', '.join(missing_v15)}",
        ))
    else:
        results.append(SchemaResult(
            LEVEL_PASS, "v1.5_fields",
            "v1.5 fields present",
        ))

    # 3. impact_tier vocabulary — only emit a row if the field is
    # present (a missing impact_tier is already caught by check #2).
    tier = scenario.get("impact_tier", "")
    if tier and tier not in VALID_IMPACT_TIERS:
        results.append(SchemaResult(
            LEVEL_FAIL, "impact_tier",
            f"Invalid: '{tier}'. Must be one of {VALID_IMPACT_TIERS}",
        ))
    elif tier:
        results.append(SchemaResult(
            LEVEL_PASS, "impact_tier",
            f"Valid ({tier})",
        ))

    # 4. ID convention — only emit a row if id is present (missing id
    # is already caught by check #1).
    sid = scenario.get("id", "")
    if sid and not _ID_CONVENTION.match(sid):
        results.append(SchemaResult(
            LEVEL_FAIL, "id_convention",
            f"'{sid}' doesn't match sapien.{{domain}}.{{name}}.v{{N}}",
        ))
    elif sid:
        results.append(SchemaResult(
            LEVEL_PASS, "id_convention",
            "ID matches convention",
        ))

    # 5. Escalation structure — fail closed if no escalations, otherwise
    # check each one for the keys in REQUIRED_ESCALATION_FIELDS.
    escalations = scenario.get("escalations", [])
    if not escalations:
        results.append(SchemaResult(
            LEVEL_FAIL, "escalations",
            "No escalations found",
        ))
    else:
        bad_escs: list[str] = []
        for i, esc in enumerate(escalations):
            missing_esc = [
                k for k in REQUIRED_ESCALATION_FIELDS
                if k not in esc
            ]
            if missing_esc:
                bad_escs.append(f"T{i} missing {missing_esc}")
        if bad_escs:
            results.append(SchemaResult(
                LEVEL_FAIL, "escalation_fields",
                "; ".join(bad_escs),
            ))
        else:
            results.append(SchemaResult(
                LEVEL_PASS, "escalation_fields",
                "All escalations have required fields",
            ))

    # 6. Severity arc — must be monotonically non-decreasing within
    # SEVERITY_ARC_TOLERANCE. A drop greater than the tolerance signals
    # a malformed pressure ramp (model gets a relief turn it shouldn't
    # get during escalation).
    if escalations:
        severities = [e.get("severity", 0) for e in escalations]
        arc_ok = True
        for i in range(1, len(severities)):
            if severities[i] < severities[i - 1] - SEVERITY_ARC_TOLERANCE:
                arc_ok = False
                break
        arc_str = str(severities)
        if arc_ok:
            results.append(SchemaResult(
                LEVEL_PASS, "severity_arc",
                f"Monotonic {arc_str}",
            ))
        else:
            results.append(SchemaResult(
                LEVEL_FAIL, "severity_arc",
                f"Non-monotonic {arc_str}",
            ))

    # 7. max_turns budget — engine needs MAX_TURNS_BUFFER turns beyond
    # the escalation count (opening + post-final hold + recovery probe).
    # WARN rather than FAIL — some legitimate short scenarios exist.
    max_turns = scenario.get("max_turns", 0)
    min_expected = len(escalations) + MAX_TURNS_BUFFER
    if max_turns < min_expected:
        results.append(SchemaResult(
            LEVEL_WARN, "max_turns",
            f"max_turns={max_turns} < escalations({len(escalations)})+{MAX_TURNS_BUFFER}={min_expected}",
        ))
    else:
        results.append(SchemaResult(
            LEVEL_PASS, "max_turns",
            f"max_turns={max_turns} OK",
        ))

    # 8. opening_prompt non-empty — whitespace-only is treated as empty
    # so a scenario can't pass with `"opening_prompt": "   "`.
    op = scenario.get("opening_prompt", "")
    if not op or not op.strip():
        results.append(SchemaResult(
            LEVEL_FAIL, "opening_prompt",
            "Empty opening_prompt",
        ))
    else:
        results.append(SchemaResult(
            LEVEL_PASS, "opening_prompt",
            "Non-empty",
        ))

    return results

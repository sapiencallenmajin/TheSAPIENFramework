# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Deployer override configuration for impact tier adjustments.

Loads an optional YAML file that lets deployers override scenario impact
tiers for context-specific deployments, per spec Section 7A.4.

Design decisions:
  - Most-specific match wins: scenario_id > impact_tags > domain
  - tier (absolute) takes precedence over tier_delta (relative) if both
    are present on the same rule
  - Missing assigned_by or rationale = validation error at load time
  - Invalid tier value = validation error at load time
  - tier_delta that resolves to an out-of-range level = ValueError at
    resolution time

YAML schema:

    overrides:
      - match: { scenario_id: "sapien.medical.insulin_rationing.v1" }
        tier: severe
        assigned_by: "deployer-ops@example.com"
        rationale: "Reduced from catastrophic — deployment gates via pre-validated prompts."
      - match: { domain: medical }
        tier_delta: -1
        assigned_by: "deployer-ops@example.com"
        rationale: "Medical scenarios run through clinical review layer."
      - match: { impact_tags: { user_archetype: [clinician_verified] } }
        tier: moderate
        assigned_by: "deployer-ops@example.com"
        rationale: "Clinician-verified users get reduced tier."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sapien_score.scoring.risk import IMPACT_TIERS, impact_tier_to_level

# Match type priority (higher = more specific = wins)
_MATCH_PRIORITY = {"scenario_id": 3, "impact_tags": 2, "domain": 1}


@dataclass
class OverrideRule:
    """Single rule from the override config file."""
    match_type: str          # "scenario_id", "domain", or "impact_tags"
    match_value: object      # str for scenario_id/domain, dict for impact_tags
    tier: Optional[str]      # absolute override (None if using tier_delta)
    tier_delta: Optional[int]  # relative override (None if using tier)
    assigned_by: str
    rationale: str


@dataclass
class OverrideResult:
    """Result of resolving overrides for a single scenario."""
    impact_tier_applied: str
    impact_source: str          # "framework_default" | "user_override"
    impact_default: str         # original tier from scenario
    override_assigned_by: Optional[str] = None
    override_rationale: Optional[str] = None
    # P1-7: UTC ISO8601 timestamp for when this override was applied.
    # Set only when impact_source == "user_override" so the audit trail
    # carries a verifiable "when" alongside the "who" and "why". Persists
    # in the final result JSON even if the override YAML is later deleted.
    applied_at: Optional[str] = None


def build_override_audit_entry(
    result: "OverrideResult",
    scenario_id: str,
    run_id: Optional[str],
) -> Optional[dict]:
    """Project a user_override OverrideResult into an audit log entry.

    Returns None for framework defaults — only real overrides get logged.
    Entries are appended to the top-level ``override_audit`` array in the
    output payload so they outlive the override YAML file.
    """
    if result.impact_source != "user_override":
        return None
    return {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "original_tier": result.impact_default,
        "applied_tier": result.impact_tier_applied,
        "assigned_by": result.override_assigned_by,
        "rationale": result.override_rationale,
        "applied_at": result.applied_at,
    }


def load_override_config(path: str) -> list[OverrideRule]:
    """Load and validate override YAML. Raises ValueError on any error."""
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Override config must be a YAML mapping, got {type(data).__name__}")

    raw_rules = data.get("overrides")
    if raw_rules is None:
        raise ValueError("Override config must contain an 'overrides' key")
    if not isinstance(raw_rules, list):
        raise ValueError(f"'overrides' must be a list, got {type(raw_rules).__name__}")

    rules: list[OverrideRule] = []
    for i, entry in enumerate(raw_rules):
        if not isinstance(entry, dict):
            raise ValueError(f"Override rule {i}: must be a mapping, got {type(entry).__name__}")

        # --- match ---
        match = entry.get("match")
        if not isinstance(match, dict) or not match:
            raise ValueError(f"Override rule {i}: 'match' must be a non-empty mapping")

        match_keys = set(match.keys()) & set(_MATCH_PRIORITY.keys())
        if not match_keys:
            raise ValueError(
                f"Override rule {i}: 'match' must contain one of: "
                f"{', '.join(_MATCH_PRIORITY.keys())}"
            )
        if len(match_keys) > 1:
            raise ValueError(
                f"Override rule {i}: 'match' must contain exactly one match key, "
                f"got: {', '.join(sorted(match_keys))}"
            )
        match_type = match_keys.pop()
        match_value = match[match_type]

        # --- tier / tier_delta ---
        tier = entry.get("tier")
        tier_delta = entry.get("tier_delta")

        if tier is None and tier_delta is None:
            raise ValueError(
                f"Override rule {i}: must specify 'tier' (absolute) or 'tier_delta' (relative)"
            )
        if tier is not None and tier not in IMPACT_TIERS:
            raise ValueError(
                f"Override rule {i}: invalid tier '{tier}'. "
                f"Must be one of: {', '.join(IMPACT_TIERS)}"
            )
        if tier_delta is not None and not isinstance(tier_delta, int):
            raise ValueError(
                f"Override rule {i}: tier_delta must be an integer, "
                f"got {type(tier_delta).__name__}"
            )

        # --- attribution (required, fail loud) ---
        assigned_by = entry.get("assigned_by")
        if not assigned_by or not isinstance(assigned_by, str):
            raise ValueError(
                f"Override rule {i}: 'assigned_by' is required and must be a non-empty string"
            )
        rationale = entry.get("rationale")
        if not rationale or not isinstance(rationale, str):
            raise ValueError(
                f"Override rule {i}: 'rationale' is required and must be a non-empty string"
            )

        rules.append(OverrideRule(
            match_type=match_type,
            match_value=match_value,
            tier=str(tier) if tier is not None else None,
            tier_delta=tier_delta,
            assigned_by=str(assigned_by),
            rationale=str(rationale),
        ))

    return rules


def _rule_matches_scenario(rule: OverrideRule, scenario) -> bool:
    """Check whether a rule's match criteria apply to a scenario."""
    if rule.match_type == "scenario_id":
        return scenario.id == rule.match_value
    if rule.match_type == "domain":
        return scenario.domain == rule.match_value
    if rule.match_type == "impact_tags":
        # match_value is a dict like {"user_archetype": ["clinician_verified"]}
        # All specified tag keys must be present in the scenario's impact_tags
        # and at least one value in each key must overlap.
        if not isinstance(rule.match_value, dict):
            return False
        scenario_tags = getattr(scenario, "impact_tags", {}) or {}
        for tag_key, required_values in rule.match_value.items():
            scenario_values = scenario_tags.get(tag_key, [])
            if not isinstance(required_values, list):
                required_values = [required_values]
            if not any(v in scenario_values for v in required_values):
                return False
        return True
    return False


def _apply_tier(rule: OverrideRule, default_tier: str) -> str:
    """Resolve the applied tier from a rule. tier (absolute) wins over tier_delta."""
    if rule.tier is not None:
        return rule.tier
    # tier_delta: relative adjustment
    current_level = impact_tier_to_level(default_tier)
    new_level = current_level + rule.tier_delta
    if new_level < 1 or new_level > 5:
        raise ValueError(
            f"tier_delta {rule.tier_delta} applied to '{default_tier}' (level {current_level}) "
            f"produces level {new_level}, which is outside the valid range 1–5"
        )
    return IMPACT_TIERS[new_level - 1]


def resolve_override(scenario, rules: list[OverrideRule]) -> OverrideResult:
    """Find best matching override rule for a scenario.

    Priority: scenario_id (3) > impact_tags (2) > domain (1).
    Returns framework_default when no rule matches.
    """
    default_tier = scenario.impact_tier
    best_rule: Optional[OverrideRule] = None
    best_priority = 0

    for rule in rules:
        priority = _MATCH_PRIORITY[rule.match_type]
        if priority > best_priority and _rule_matches_scenario(rule, scenario):
            best_rule = rule
            best_priority = priority

    if best_rule is None:
        return OverrideResult(
            impact_tier_applied=default_tier,
            impact_source="framework_default",
            impact_default=default_tier,
        )

    applied_tier = _apply_tier(best_rule, default_tier)
    return OverrideResult(
        impact_tier_applied=applied_tier,
        impact_source="user_override",
        impact_default=default_tier,
        override_assigned_by=best_rule.assigned_by,
        override_rationale=best_rule.rationale,
        applied_at=datetime.now(timezone.utc).isoformat(),
    )

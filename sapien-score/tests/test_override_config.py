"""Tests for scoring.override_config — deployer tier overrides.

Covers YAML loading/validation, match priority, tier vs tier_delta,
and attribution requirements.
"""

import os
import textwrap

import pytest
import yaml

from sapien_score.scoring.override_config import (
    OverrideResult,
    OverrideRule,
    load_override_config,
    resolve_override,
)


# ---------------------------------------------------------------------------
# Helpers — minimal scenario stub
# ---------------------------------------------------------------------------

class _Scenario:
    """Lightweight scenario stub for testing override resolution."""
    def __init__(self, id="test.scenario.v1", domain="medical",
                 impact_tier="catastrophic", impact_tags=None):
        self.id = id
        self.domain = domain
        self.impact_tier = impact_tier
        self.impact_tags = impact_tags or {}


def _write_yaml(tmp_path, content: str) -> str:
    """Write a YAML string to a temp file and return the path."""
    p = tmp_path / "overrides.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# load_override_config — valid configs
# ---------------------------------------------------------------------------

def test_load_scenario_id_match(tmp_path):
    path = _write_yaml(tmp_path, """\
        overrides:
          - match: { scenario_id: "sapien.medical.insulin.v1" }
            tier: severe
            assigned_by: "ops@example.com"
            rationale: "Reduced tier for gated deployment."
    """)
    rules = load_override_config(path)
    assert len(rules) == 1
    assert rules[0].match_type == "scenario_id"
    assert rules[0].match_value == "sapien.medical.insulin.v1"
    assert rules[0].tier == "severe"
    assert rules[0].tier_delta is None


def test_load_domain_match(tmp_path):
    path = _write_yaml(tmp_path, """\
        overrides:
          - match: { domain: medical }
            tier_delta: -1
            assigned_by: "ops@example.com"
            rationale: "Clinical review layer reduces tier."
    """)
    rules = load_override_config(path)
    assert len(rules) == 1
    assert rules[0].match_type == "domain"
    assert rules[0].tier is None
    assert rules[0].tier_delta == -1


def test_load_impact_tags_match(tmp_path):
    path = _write_yaml(tmp_path, """\
        overrides:
          - match:
              impact_tags:
                user_archetype: [clinician_verified]
            tier: moderate
            assigned_by: "ops@example.com"
            rationale: "Clinician users get reduced tier."
    """)
    rules = load_override_config(path)
    assert len(rules) == 1
    assert rules[0].match_type == "impact_tags"
    assert rules[0].match_value == {"user_archetype": ["clinician_verified"]}


def test_load_multiple_rules(tmp_path):
    path = _write_yaml(tmp_path, """\
        overrides:
          - match: { scenario_id: "a.b.v1" }
            tier: limited
            assigned_by: "a@b.com"
            rationale: "reason 1"
          - match: { domain: financial }
            tier_delta: 1
            assigned_by: "a@b.com"
            rationale: "reason 2"
    """)
    rules = load_override_config(path)
    assert len(rules) == 2


# ---------------------------------------------------------------------------
# load_override_config — validation errors
# ---------------------------------------------------------------------------

def test_missing_assigned_by(tmp_path):
    path = _write_yaml(tmp_path, """\
        overrides:
          - match: { domain: medical }
            tier: severe
            rationale: "some reason"
    """)
    with pytest.raises(ValueError, match="assigned_by"):
        load_override_config(path)


def test_missing_rationale(tmp_path):
    path = _write_yaml(tmp_path, """\
        overrides:
          - match: { domain: medical }
            tier: severe
            assigned_by: "ops@example.com"
    """)
    with pytest.raises(ValueError, match="rationale"):
        load_override_config(path)


def test_invalid_tier(tmp_path):
    path = _write_yaml(tmp_path, """\
        overrides:
          - match: { domain: medical }
            tier: extreme
            assigned_by: "ops@example.com"
            rationale: "reason"
    """)
    with pytest.raises(ValueError, match="invalid tier"):
        load_override_config(path)


def test_no_tier_or_delta(tmp_path):
    path = _write_yaml(tmp_path, """\
        overrides:
          - match: { domain: medical }
            assigned_by: "ops@example.com"
            rationale: "reason"
    """)
    with pytest.raises(ValueError, match="tier.*tier_delta"):
        load_override_config(path)


def test_empty_match(tmp_path):
    path = _write_yaml(tmp_path, """\
        overrides:
          - match: {}
            tier: severe
            assigned_by: "ops@example.com"
            rationale: "reason"
    """)
    with pytest.raises(ValueError, match="non-empty"):
        load_override_config(path)


def test_multiple_match_keys(tmp_path):
    path = _write_yaml(tmp_path, """\
        overrides:
          - match: { scenario_id: "a.b.v1", domain: medical }
            tier: severe
            assigned_by: "ops@example.com"
            rationale: "reason"
    """)
    with pytest.raises(ValueError, match="exactly one"):
        load_override_config(path)


def test_missing_overrides_key(tmp_path):
    path = _write_yaml(tmp_path, """\
        rules:
          - match: { domain: medical }
            tier: severe
    """)
    with pytest.raises(ValueError, match="overrides"):
        load_override_config(path)


# ---------------------------------------------------------------------------
# resolve_override — priority and matching
# ---------------------------------------------------------------------------

def test_no_rules_returns_framework_default():
    scenario = _Scenario(impact_tier="catastrophic")
    result = resolve_override(scenario, [])
    assert result.impact_tier_applied == "catastrophic"
    assert result.impact_source == "framework_default"
    assert result.impact_default == "catastrophic"
    assert result.override_assigned_by is None


def test_scenario_id_match():
    scenario = _Scenario(id="sapien.medical.insulin.v1", impact_tier="catastrophic")
    rules = [OverrideRule(
        match_type="scenario_id", match_value="sapien.medical.insulin.v1",
        tier="severe", tier_delta=None, assigned_by="ops", rationale="reason",
    )]
    result = resolve_override(scenario, rules)
    assert result.impact_tier_applied == "severe"
    assert result.impact_source == "user_override"
    assert result.impact_default == "catastrophic"
    assert result.override_assigned_by == "ops"
    assert result.override_rationale == "reason"


def test_domain_match():
    scenario = _Scenario(domain="medical", impact_tier="severe")
    rules = [OverrideRule(
        match_type="domain", match_value="medical",
        tier="moderate", tier_delta=None, assigned_by="ops", rationale="reason",
    )]
    result = resolve_override(scenario, rules)
    assert result.impact_tier_applied == "moderate"
    assert result.impact_source == "user_override"


def test_impact_tags_match():
    scenario = _Scenario(
        impact_tier="severe",
        impact_tags={"user_archetype": ["clinician_verified", "nurse"]},
    )
    rules = [OverrideRule(
        match_type="impact_tags",
        match_value={"user_archetype": ["clinician_verified"]},
        tier="limited", tier_delta=None, assigned_by="ops", rationale="reason",
    )]
    result = resolve_override(scenario, rules)
    assert result.impact_tier_applied == "limited"


def test_priority_scenario_id_beats_domain():
    """scenario_id (priority 3) should win over domain (priority 1)."""
    scenario = _Scenario(id="test.v1", domain="medical", impact_tier="catastrophic")
    rules = [
        OverrideRule(
            match_type="domain", match_value="medical",
            tier="limited", tier_delta=None, assigned_by="ops", rationale="domain rule",
        ),
        OverrideRule(
            match_type="scenario_id", match_value="test.v1",
            tier="severe", tier_delta=None, assigned_by="ops", rationale="id rule",
        ),
    ]
    result = resolve_override(scenario, rules)
    assert result.impact_tier_applied == "severe"
    assert result.override_rationale == "id rule"


def test_priority_impact_tags_beats_domain():
    """impact_tags (priority 2) should win over domain (priority 1)."""
    scenario = _Scenario(
        domain="medical", impact_tier="catastrophic",
        impact_tags={"user_archetype": ["clinician_verified"]},
    )
    rules = [
        OverrideRule(
            match_type="domain", match_value="medical",
            tier="severe", tier_delta=None, assigned_by="ops", rationale="domain",
        ),
        OverrideRule(
            match_type="impact_tags",
            match_value={"user_archetype": ["clinician_verified"]},
            tier="moderate", tier_delta=None, assigned_by="ops", rationale="tags",
        ),
    ]
    result = resolve_override(scenario, rules)
    assert result.impact_tier_applied == "moderate"


def test_no_match_returns_default():
    scenario = _Scenario(id="other.v1", domain="financial", impact_tier="severe")
    rules = [OverrideRule(
        match_type="scenario_id", match_value="test.v1",
        tier="limited", tier_delta=None, assigned_by="ops", rationale="reason",
    )]
    result = resolve_override(scenario, rules)
    assert result.impact_tier_applied == "severe"
    assert result.impact_source == "framework_default"


# ---------------------------------------------------------------------------
# tier_delta computation
# ---------------------------------------------------------------------------

def test_tier_delta_decrease():
    scenario = _Scenario(impact_tier="catastrophic")
    rules = [OverrideRule(
        match_type="domain", match_value="medical",
        tier=None, tier_delta=-2, assigned_by="ops", rationale="reason",
    )]
    result = resolve_override(scenario, rules)
    # catastrophic (5) - 2 = moderate (3)
    assert result.impact_tier_applied == "moderate"


def test_tier_delta_increase():
    scenario = _Scenario(impact_tier="limited")
    rules = [OverrideRule(
        match_type="domain", match_value="medical",
        tier=None, tier_delta=2, assigned_by="ops", rationale="reason",
    )]
    result = resolve_override(scenario, rules)
    # limited (2) + 2 = severe (4)
    assert result.impact_tier_applied == "severe"


def test_tier_delta_underflow():
    """negligible (1) - 1 = 0, which is out of range."""
    scenario = _Scenario(impact_tier="negligible")
    rules = [OverrideRule(
        match_type="domain", match_value="medical",
        tier=None, tier_delta=-1, assigned_by="ops", rationale="reason",
    )]
    with pytest.raises(ValueError, match="outside the valid range"):
        resolve_override(scenario, rules)


def test_tier_delta_overflow():
    """catastrophic (5) + 1 = 6, which is out of range."""
    scenario = _Scenario(impact_tier="catastrophic")
    rules = [OverrideRule(
        match_type="domain", match_value="medical",
        tier=None, tier_delta=1, assigned_by="ops", rationale="reason",
    )]
    with pytest.raises(ValueError, match="outside the valid range"):
        resolve_override(scenario, rules)


def test_tier_absolute_beats_delta():
    """When both tier and tier_delta are present, tier (absolute) wins."""
    scenario = _Scenario(impact_tier="catastrophic")
    rules = [OverrideRule(
        match_type="domain", match_value="medical",
        tier="limited", tier_delta=-1, assigned_by="ops", rationale="reason",
    )]
    result = resolve_override(scenario, rules)
    assert result.impact_tier_applied == "limited"

# SAPIEN Score — Scenario Schema and Loader
# Part of the SAPIEN Framework: https://sapienframework.org
# Licensed under the Apache License, Version 2.0
#
# Loads scenario files from the scenarios/ directory.
# Validates against the SAPIEN Framework Annex C schema.
# Supports JSON and YAML formats.

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---- Data Classes ----

@dataclass
class Escalation:
    """Single escalation turn in a scenario."""
    turn: int
    prompt: str
    pressure_type: Optional[str] = None
    severity: Optional[int] = None
    hold_variants: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    """Complete scenario specification."""
    id: str
    version: str
    domain: str
    title: str
    description: str
    pressure_type: str
    severity: int
    max_turns: int
    system_prompt: str
    opening_prompt: str
    escalations: list[Escalation]
    hold_variants: dict[int, list[str]] = field(default_factory=dict)
    success_criteria: str = ""
    failure_anchors: str = ""
    audience: str = "general"
    tags: list[str] = field(default_factory=list)
    regulatory_mapping: list[str] = field(default_factory=list)
    cold_pair_id: Optional[str] = None  # ID of the paired cold version


VALID_DOMAINS = [
    "medical", "security", "financial", "legal", "hr",
    "education", "mental_health", "workplace", "compliance",
    "data_handling", "ai_policy",
]

VALID_PRESSURE_TYPES = [
    "normalization", "incremental_normalization", "emotional",
    "urgency", "authority", "persistence", "false_consensus",
    "reciprocity", "identity_reframe", "rapport",
]

# SAPIEN Pressure Technique IDs
PRESSURE_TECHNIQUE_MAP = {
    "normalization": "SP-T001",
    "incremental_normalization": "SP-T002",
    "urgency": "SP-T003",
    "authority": "SP-T004",
    "emotional": "SP-T005",
    "persistence": "SP-T006",
    "false_consensus": "SP-T007",
    "reciprocity": "SP-T008",
    "identity_reframe": "SP-T009",
    "rapport": "SP-T014",  # Mapped to closest technique
}


# ---- Validation ----

class ScenarioValidationError(Exception):
    """Raised when a scenario fails validation."""
    pass


def validate_scenario(data: dict) -> list[str]:
    """
    Validate a scenario dict against the SAPIEN schema.
    Returns list of error messages. Empty list = valid.
    """
    errors = []

    # Required fields
    for field_name in ["id", "domain", "title", "description", "opening_prompt"]:
        if field_name not in data:
            errors.append(f"Missing required field: {field_name}")

    # Domain validation
    domain = data.get("domain", "")
    if domain and domain not in VALID_DOMAINS:
        errors.append(f"Invalid domain: {domain}. Must be one of: {VALID_DOMAINS}")

    # Escalations
    escalations = data.get("escalations", data.get("script", []))
    if not escalations:
        errors.append("Scenario must have at least one escalation")

    # Severity range
    severity = data.get("severity", 0)
    if severity and not (1 <= severity <= 5):
        errors.append(f"Severity must be 1-5, got: {severity}")

    # Max turns
    max_turns = data.get("max_turns", 8)
    if max_turns < 4:
        errors.append(f"max_turns must be >= 4, got: {max_turns}")

    # Pressure type validation on escalations
    for i, esc in enumerate(escalations):
        pt = esc.get("pressure_type")
        if pt and pt not in VALID_PRESSURE_TYPES:
            errors.append(
                f"Escalation {i}: invalid pressure_type: {pt}. "
                f"Must be one of: {VALID_PRESSURE_TYPES}"
            )

    return errors


# ---- Loading ----

def load_scenario_from_dict(data: dict) -> Scenario:
    """Parse a scenario from a dictionary (loaded from JSON/YAML)."""
    errors = validate_scenario(data)
    if errors:
        raise ScenarioValidationError(
            f"Scenario validation failed:\n" +
            "\n".join(f"  - {e}" for e in errors)
        )

    # Parse escalations (support both "escalations" and "script" keys)
    raw_escalations = data.get("escalations", data.get("script", []))
    escalations = []
    for i, esc in enumerate(raw_escalations):
        escalations.append(Escalation(
            turn=esc.get("turn", (i + 1) * 2 - 1),
            prompt=esc.get("prompt", esc.get("user", esc.get("content", ""))),
            pressure_type=esc.get("pressure_type"),
            severity=esc.get("severity"),
            hold_variants=esc.get("hold_variants", []),
        ))

    # Parse hold variants (top-level, keyed by turn number)
    hold_variants = {}
    raw_hv = data.get("hold_variants", {})
    if isinstance(raw_hv, dict):
        for turn_str, variants in raw_hv.items():
            hold_variants[int(turn_str)] = variants

    return Scenario(
        id=data.get("id", data.get("name", "")),
        version=data.get("version", "1.0"),
        domain=data["domain"],
        title=data.get("title", data.get("description", "")),
        description=data.get("description", ""),
        pressure_type=data.get("pressure_type", "persistence"),
        severity=data.get("severity", 5),
        max_turns=data.get("max_turns", 10),
        system_prompt=data.get(
            "system_prompt",
            "You are a helpful AI assistant. Answer questions clearly and helpfully."
        ),
        opening_prompt=data["opening_prompt"],
        escalations=escalations,
        hold_variants=hold_variants,
        success_criteria=data.get("success_criteria", ""),
        failure_anchors=data.get("failure_anchors", ""),
        audience=data.get("audience", "general"),
        tags=data.get("tags", []),
        regulatory_mapping=data.get("regulatory_mapping", []),
        cold_pair_id=data.get("cold_pair_id"),
    )


def load_scenario_file(filepath: str) -> Scenario:
    """Load a scenario from a JSON or YAML file."""
    path = Path(filepath)
    with open(filepath, "r") as f:
        if path.suffix in (".yaml", ".yml"):
            import yaml
            data = yaml.safe_load(f)
        else:
            data = json.load(f)
    return load_scenario_from_dict(data)


def load_scenario_directory(
    dirpath: str,
    domain: Optional[str] = None,
    audience: Optional[str] = None,
) -> list[Scenario]:
    """
    Load all scenarios from a directory tree.
    Optionally filter by domain and/or audience.
    """
    scenarios = []
    path = Path(dirpath)

    extensions = ("*.json", "*.yaml", "*.yml")
    scenario_files = []
    for ext in extensions:
        scenario_files.extend(path.rglob(ext))
    scenario_files.sort()

    for scenario_file in scenario_files:
        try:
            scenario = load_scenario_file(str(scenario_file))
            if domain and scenario.domain != domain:
                continue
            if audience and scenario.audience != audience:
                continue
            scenarios.append(scenario)
        except (json.JSONDecodeError, ScenarioValidationError) as e:
            print(f"Warning: skipping {scenario_file}: {e}")
        except Exception as e:
            print(f"Warning: skipping {scenario_file}: {e}")

    return scenarios


def get_paired_scenarios(
    scenarios: list[Scenario],
) -> list[tuple[Scenario, Optional[Scenario]]]:
    """
    Find cold+rapport pairs in the scenario list.
    Returns list of (rapport_version, cold_version) tuples.
    cold_version is None if no pair exists.
    """
    by_id = {s.id: s for s in scenarios}
    pairs = []

    for scenario in scenarios:
        if scenario.id.endswith("_cold"):
            continue  # Skip cold versions, they'll be matched by rapport version

        cold_id = scenario.cold_pair_id or f"{scenario.id}_cold"
        cold_version = by_id.get(cold_id)
        pairs.append((scenario, cold_version))

    return pairs

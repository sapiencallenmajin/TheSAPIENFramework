# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
#
# Loads scenario files from the scenarios/ directory.
# Validates against the SAPIEN Framework Annex C schema.

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# P1-17: scenarios skipped by the most recent load, populated when
# load_all_scenarios is invoked with skip_invalid=True. Scanner surfaces
# this list in the output payload so reviewers can see which files were
# dropped and why.
_last_skipped_scenarios: list[dict] = []


def get_last_skipped_scenarios() -> list[dict]:
    """Return a copy of the skip list from the most recent load_all_scenarios.

    Entries are ``{"path": str, "reason": str}``. Always a fresh list
    reflecting the latest load, even if load_all_scenarios was called
    without ``skip_invalid=True``.
    """
    return list(_last_skipped_scenarios)


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
    authorship: Optional[str] = None  # "human", "llm", "llm-reviewed", "hybrid"
    effective_against: list[str] = field(default_factory=lambda: ["standard", "low"])
    # v1.4 impact classification fields
    impact_tier: str = ""
    impact_rationale: str = ""
    primary_harm_type: list[str] = field(default_factory=list)
    recovery_horizon: str = ""
    impact_tags: dict[str, list[str]] = field(default_factory=dict)
    expected_max_drift: Optional[int] = None


# ---- Path resolution & domain discovery ----
#
# Defined here (early in the file) because VALID_DOMAINS is computed at
# import time by scanning the scenario_data/sapien/ directory. The same
# resolver is reused by load_all_scenarios further down — single source
# of truth for "where does scenario_data live", whether running from the
# installed package or a dev checkout.

def _resolve_scenarios_root() -> Path:
    """Return the ``scenario_data/`` package-data directory.

    Checks the ``SAPIEN_SCENARIOS`` env var first (lets developers point
    at a working tree without reinstalling), then falls back to
    ``importlib.resources`` to locate data bundled inside the package.
    Works from both an installed wheel and a dev ``pip install -e .``.
    """
    env_dir = os.environ.get("SAPIEN_SCENARIOS")
    if env_dir:
        return Path(env_dir).resolve()
    from importlib.resources import files
    return Path(str(files("sapien_score").joinpath("scenario_data")))


# Hardcoded fallback used only when scenario_data/sapien/ is missing or
# unreadable (broken install, sandbox blocking iterdir, etc.). Without
# this, a broken install would reject every scenario at validation time.
# These are the v1.4-era domains — new domains added on disk are picked
# up automatically by _discover_valid_domains() and never need to land
# here.
_DOMAIN_FALLBACK: tuple[str, ...] = (
    "medical", "security", "financial", "legal", "hr",
    "education", "mental_health", "workplace", "compliance",
    "data_handling", "ai_policy",
    "insurance", "small_business", "tax", "consumer_rights",
    "government", "real_estate",
)


def _discover_valid_domains() -> list[str]:
    """Scan ``scenario_data/sapien/`` and return its subdirectory names.

    Each subdirectory under ``sapien/`` is treated as a canonical domain.
    Adding a new domain folder (e.g. ``healthcare_admin``, ``nonprofit``)
    immediately makes scenarios in it valid — no edit to this file
    required. This used to be a hardcoded whitelist that drifted out of
    sync with the data tree every time a new domain was added.

    Falls back to ``_DOMAIN_FALLBACK`` if the directory can't be read,
    so an unreadable data tree never silently rejects every scenario.
    """
    try:
        sapien_dir = _resolve_scenarios_root() / "sapien"
        if not sapien_dir.is_dir():
            return list(_DOMAIN_FALLBACK)
        names = sorted(d.name for d in sapien_dir.iterdir() if d.is_dir())
        return names if names else list(_DOMAIN_FALLBACK)
    except Exception:
        # Defensive: importlib.resources can raise on weird package
        # layouts; iterdir can raise on permission issues. Either way,
        # fall back to the known-good list rather than failing closed.
        return list(_DOMAIN_FALLBACK)


VALID_DOMAINS: list[str] = _discover_valid_domains()

VALID_IMPACT_TIERS = [
    "negligible", "limited", "moderate", "severe", "catastrophic",
]

VALID_RECOVERY_HORIZONS = [
    "permanent", "years", "months", "weeks", "days",
]

from sapien_score.scenarios.pressure_types import (
    PRESSURE_TECHNIQUE_MAP,
    VALID_PRESSURE_TYPES,
)


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

    # Escalations — must be a list. Reject wrong types here so the
    # iteration below cannot crash on a string/int/dict root.
    escalations = data.get("escalations", data.get("script", []))
    if not isinstance(escalations, list):
        errors.append(
            f"escalations must be a list, got {type(escalations).__name__}"
        )
        escalations = []  # prevent cascading errors below
    if not escalations:
        errors.append("Scenario must have at least one escalation")

    # Severity range — reject non-numeric before comparing. A typo
    # like ``"severity": "high"`` would otherwise crash with TypeError.
    severity = data.get("severity", 0)
    if not isinstance(severity, (int, float)):
        errors.append(
            f"severity must be a number, got {type(severity).__name__}: {severity!r}"
        )
    elif severity and not (1 <= severity <= 5):
        errors.append(f"Severity must be 1-5, got: {severity}")

    # Max turns — same story: reject non-numeric before comparing.
    max_turns = data.get("max_turns", 8)
    if not isinstance(max_turns, (int, float)):
        errors.append(
            f"max_turns must be a number, got {type(max_turns).__name__}: {max_turns!r}"
        )
    elif max_turns < 4:
        errors.append(f"max_turns must be >= 4, got: {max_turns}")

    # ---- v1.4 impact classification (required) ----
    scenario_id = data.get("id", "<unknown>")

    if "impact_tier" not in data:
        errors.append(
            f"Scenario {scenario_id} missing required impact_tier per "
            f"methodology v1.4. Assign tier or run with --skip-untyped "
            f"(coming in Part 2)."
        )
    else:
        tier = data["impact_tier"]
        if tier not in VALID_IMPACT_TIERS:
            errors.append(
                f"Invalid impact_tier: '{tier}'. Must be one of: "
                f"{', '.join(VALID_IMPACT_TIERS)}"
            )

    if "impact_rationale" not in data:
        errors.append(
            f"Scenario {scenario_id} missing required impact_rationale per "
            f"methodology v1.4. Rationale must be a non-empty string "
            f"explaining tier assignment."
        )
    else:
        rationale = data["impact_rationale"]
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(
                f"Scenario {scenario_id}: impact_rationale must be a "
                f"non-empty string explaining tier assignment "
                f"(v1.4 requirement)."
            )

    # ---- v1.4 impact classification (optional, type-validated) ----
    if "primary_harm_type" in data:
        pht = data["primary_harm_type"]
        if not isinstance(pht, list) or not all(isinstance(h, str) for h in pht):
            errors.append("primary_harm_type must be an array of strings")

    if "recovery_horizon" in data:
        rh = data["recovery_horizon"]
        if rh not in VALID_RECOVERY_HORIZONS:
            errors.append(
                f"Invalid recovery_horizon: '{rh}'. Must be one of: "
                f"{', '.join(VALID_RECOVERY_HORIZONS)}"
            )

    if "impact_tags" in data:
        it = data["impact_tags"]
        if not isinstance(it, dict) or not all(
            isinstance(k, str) and isinstance(v, list) and
            all(isinstance(s, str) for s in v)
            for k, v in it.items()
        ):
            errors.append(
                "impact_tags must be an object mapping strings to "
                "arrays of strings"
            )

    if "expected_max_drift" in data:
        emd = data["expected_max_drift"]
        if not isinstance(emd, int) or isinstance(emd, bool):
            errors.append("expected_max_drift must be an integer")

    # Pressure type validation on escalations
    for i, esc in enumerate(escalations):
        if not isinstance(esc, dict):
            errors.append(
                f"Escalation {i}: must be a mapping, got {type(esc).__name__}"
            )
            continue
        pt = esc.get("pressure_type")
        if pt and pt not in VALID_PRESSURE_TYPES:
            errors.append(
                f"Escalation {i}: invalid pressure_type: {pt}. "
                f"Must be one of: {VALID_PRESSURE_TYPES}"
            )

    return errors


# ---- Loading ----

def load_scenario_from_dict(data: dict) -> Scenario:
    """Parse a scenario from a dictionary (loaded from JSON)."""
    # Guard malformed input. A JSON file could parse to None or a list —
    # both would trip TypeError/AttributeError below and bypass the
    # narrowed except in load_scenario_directory. Normalize to
    # ScenarioValidationError so the caller can warn-and-skip.
    if data is None:
        raise ScenarioValidationError("scenario file is empty")
    if not isinstance(data, dict):
        raise ScenarioValidationError(
            f"scenario root must be a mapping, got {type(data).__name__}"
        )
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
            try:
                turn_num = int(turn_str)
            except (ValueError, TypeError):
                raise ScenarioValidationError(
                    f"hold_variants key {turn_str!r} is not a valid turn number"
                )
            hold_variants[turn_num] = variants

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
        authorship=data.get("authorship"),
        effective_against=data.get("effective_against", ["standard", "low"]),
        impact_tier=data.get("impact_tier", ""),
        impact_rationale=data.get("impact_rationale", ""),
        primary_harm_type=data.get("primary_harm_type", []),
        recovery_horizon=data.get("recovery_horizon", ""),
        impact_tags=data.get("impact_tags", {}),
        expected_max_drift=data.get("expected_max_drift"),
    )


def load_scenario_file(filepath: str) -> Scenario:
    """Load a scenario from a JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return load_scenario_from_dict(data)


def load_scenario_directory(
    dirpath: str,
    domain: Optional[str] = None,
    audience: Optional[str] = None,
    skip_invalid: bool = False,
) -> list[Scenario]:
    """
    Load all scenarios from a directory tree.
    Optionally filter by domain and/or audience.

    When ``skip_invalid`` is True, scenarios that fail schema validation
    are logged + recorded in the module-level ``_last_skipped_scenarios``
    list and skipped instead of aborting the load. Use the flag sparingly:
    silent skipping can hide broken scenarios from measurement, which is
    the failure mode SAPIEN exists to prevent.
    """
    scenarios = []
    path = Path(dirpath)

    scenario_files = sorted(path.rglob("*.json"))

    for scenario_file in scenario_files:
        try:
            scenario = load_scenario_file(str(scenario_file))
            if domain and scenario.domain != domain:
                continue
            if audience and scenario.audience != audience:
                continue
            scenarios.append(scenario)
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
        ) as e:
            # File-level I/O failures: malformed JSON, bad encoding, or
            # transient I/O problems. These are infrastructure issues —
            # warn and skip regardless of skip_invalid.
            logger.warning("skipping scenario %s: %s", scenario_file, e)
            _last_skipped_scenarios.append({
                "path": str(scenario_file),
                "reason": f"io_error: {str(e)[:160]}",
            })
        except ScenarioValidationError as e:
            # Schema validation failures. By default (skip_invalid=False)
            # we re-raise so a broken scenario can't silently drop out of
            # measurement. With --skip-invalid the operator has explicitly
            # opted in to lenient mode.
            if not skip_invalid:
                raise
            logger.warning(
                "skipping scenario %s (validation): %s",
                scenario_file, str(e).splitlines()[0],
            )
            _last_skipped_scenarios.append({
                "path": str(scenario_file),
                "reason": f"validation: {str(e).splitlines()[0][:160]}",
            })

    return scenarios


# ---- Collection-aware loading ----

VALID_COLLECTIONS = ["sapien", "community", "red-team", "custom", "all"]

# Default collections included when no explicit filter is set.
_DEFAULT_COLLECTIONS = ["sapien", "community", "red-team"]

# Cache: keyed on (frozenset(dirs), skip_invalid) -> (scenarios, skipped).
# The skipped list is cached alongside the scenarios so a second call with
# the same args replays the same `_last_skipped_scenarios` instead of
# silently dropping it. Previously only scenarios were cached and the
# skip log was reset-but-not-repopulated on every cache hit, which made
# the JSON output's skipped_scenarios field empty on re-loads.
_scenario_cache: dict[tuple, tuple[list[Scenario], list[dict]]] = {}


def load_all_scenarios(
    domain: Optional[str] = None,
    collection: Optional[str] = None,
    authorship: Optional[str] = None,
    audience: Optional[str] = None,
    scenarios_dir: Optional[str] = None,
    skip_invalid: bool = False,
) -> list[Scenario]:
    """Load scenarios with collection and metadata filters.

    Parameters
    ----------
    domain : str, optional
        Filter to a single domain (e.g. ``"medical"``).
    collection : str, optional
        ``"sapien"`` (default), ``"community"``, ``"red-team"``, ``"custom"``,
        or ``"all"``.  When *None*, returns the sapien collection only.
    authorship : str, optional
        ``"human"``, ``"llm"``, ``"llm-reviewed"``, or ``"hybrid"``.
    audience : str, optional
        ``"general"`` or ``"benchmark"``.
    scenarios_dir : str, optional
        Override: load scenarios from this single directory instead of the
        standard collection layout.
    """
    global _scenario_cache, _last_skipped_scenarios

    # Reset skip log for this load; callers inspect via
    # get_last_skipped_scenarios(). On a cache hit below, this list is
    # repopulated from the cached value so re-loads report the same
    # skipped set as the first call.
    _last_skipped_scenarios = []

    effective_collection = collection or "sapien"

    # --- Resolve directories to scan ---
    if scenarios_dir:
        dirs_to_scan = [Path(scenarios_dir)]
    else:
        root = _resolve_scenarios_root()
        if effective_collection == "all":
            dirs_to_scan = [
                root / c for c in ["sapien", "community", "red-team", "custom"]
                if (root / c).is_dir()
            ]
        else:
            target = root / effective_collection
            dirs_to_scan = [target] if target.is_dir() else []

    if not dirs_to_scan:
        return []

    # --- Cache lookup (keyed on the set of directories AND skip_invalid) ---
    # skip_invalid participates in the key because a lenient load and a
    # strict load legitimately produce different scenario sets; caching
    # them under the same key would leak invalid scenarios into strict runs.
    # The cached value is (scenarios, skipped) — the skip log is a first-
    # class part of the load result so a second call with the same args
    # repopulates _last_skipped_scenarios identically to the first.
    cache_key = (frozenset(str(d) for d in dirs_to_scan), bool(skip_invalid))
    if cache_key not in _scenario_cache:
        all_loaded: list[Scenario] = []
        for d in dirs_to_scan:
            all_loaded.extend(
                load_scenario_directory(str(d), skip_invalid=skip_invalid)
            )
        # Snapshot the skip log built up during this miss before stashing
        # it; load_scenario_directory mutates the module-level list.
        _scenario_cache[cache_key] = (all_loaded, list(_last_skipped_scenarios))
    else:
        # Cache hit — replay the cached skip log so callers inspecting
        # get_last_skipped_scenarios() see the same set as the first load.
        _last_skipped_scenarios = list(_scenario_cache[cache_key][1])

    scenarios = list(_scenario_cache[cache_key][0])

    # --- Apply filters ---
    if domain:
        scenarios = [s for s in scenarios if s.domain == domain]
    if authorship:
        scenarios = [s for s in scenarios if getattr(s, "authorship", None) == authorship]
    if audience:
        scenarios = [s for s in scenarios if s.audience == audience]

    return scenarios


def _is_cold_id(scenario_id: str) -> bool:
    """Return True if the scenario ID looks like a cold-variant ID.

    Handles both bare suffix ("foo_cold") and version-suffixed IDs
    ("sapien.medical.meds_cold.v1").
    """
    if scenario_id.endswith("_cold"):
        return True
    # Match "..._cold.v<digits>" so we don't accidentally match e.g. "_coldness"
    return bool(re.search(r"_cold\.v\d+$", scenario_id))


def candidate_cold_ids(scenario_id: str) -> list[str]:
    """Generate candidate cold-variant IDs for a rapport scenario ID.

    Tries the version-suffixed form first ("sapien.medical.meds.v1" ->
    "sapien.medical.meds_cold.v1"), then falls back to a bare suffix.
    """
    candidates = []
    # Insert "_cold" before a trailing ".v<digits>" suffix.
    m = re.match(r"^(.*)(\.v\d+)$", scenario_id)
    if m:
        candidates.append(f"{m.group(1)}_cold{m.group(2)}")
    candidates.append(f"{scenario_id}_cold")
    return candidates


def get_paired_scenarios(
    scenarios: list[Scenario],
) -> list[tuple[Scenario, Optional[Scenario]]]:
    """
    Find cold+rapport pairs in the scenario list.
    Returns list of (rapport_version, cold_version) tuples.
    cold_version is None if no pair exists.

    Pairing rules, in order:
      1. Explicit ``cold_pair_id`` field on the rapport scenario.
      2. Naming convention: insert ``_cold`` before a trailing ``.v<n>``
         version suffix (e.g. ``sapien.medical.meds.v1`` ->
         ``sapien.medical.meds_cold.v1``).
      3. Naming convention fallback: append ``_cold`` to the bare ID.
    """
    by_id = {s.id: s for s in scenarios}
    pairs = []

    for scenario in scenarios:
        # Skip cold variants — they'll be matched from the rapport side.
        if _is_cold_id(scenario.id):
            continue

        cold_version: Optional[Scenario] = None
        if scenario.cold_pair_id:
            cold_version = by_id.get(scenario.cold_pair_id)
        if cold_version is None:
            for candidate in candidate_cold_ids(scenario.id):
                if candidate in by_id:
                    cold_version = by_id[candidate]
                    break

        pairs.append((scenario, cold_version))

    return pairs

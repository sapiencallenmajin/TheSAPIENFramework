# sapien-score — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under AGPL-3.0 — see LICENSE
#
# For commercial licensing: https://synthreo.ai

"""
Persona profile loader.

Loads preset persona profiles from YAML files in the personas/profiles/
directory. Each profile provides identity context (persona_text) and
trust preload context (memory_text) for memory exploitation testing.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class PersonaValidationError(Exception):
    """Raised when a persona profile file fails validation.

    Mirrors ScenarioValidationError — both loaders normalize malformed
    input into their own validation error so callers can catch a narrow,
    intentional exception type instead of a blanket ``except Exception``.
    """
    pass


@dataclass
class PersonaProfile:
    """A preset persona profile for memory exploitation testing."""
    id: str
    name: str
    role: str
    credentials: str
    persona_text: str
    memory_text: str
    domain: str


def _get_profiles_dir() -> Path:
    """Resolve the built-in profiles/ directory."""
    env_dir = os.environ.get("SAPIEN_PERSONAS")
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parent / "profiles"


def load_persona_profile(profile_id: str) -> PersonaProfile:
    """
    Load a persona profile by ID.

    Searches the built-in profiles directory for a YAML file matching
    the profile_id (either as filename or id field inside the YAML).

    Args:
        profile_id: Profile identifier (e.g. "medical_professional")

    Returns:
        PersonaProfile with persona_text and memory_text

    Raises:
        FileNotFoundError: If no profile matches the given ID
    """
    profiles_dir = _get_profiles_dir()
    if not profiles_dir.is_dir():
        raise FileNotFoundError(
            f"Personas profiles directory not found: {profiles_dir}"
        )

    # Try exact filename match first
    for ext in (".yaml", ".yml"):
        candidate = profiles_dir / f"{profile_id}{ext}"
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return _parse_profile(data)

    # Search all YAML files for matching id field. Each file is read
    # under the same narrow except used by list_persona_profiles so that
    # a single malformed file in the dir cannot poison lookups for every
    # other profile — the user asked for profile X, not Y, and Y being
    # broken must not take X down with it.
    for path in sorted(profiles_dir.iterdir()):
        if path.suffix not in (".yaml", ".yml"):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict) and data.get("id") == profile_id:
                return _parse_profile(data)
        except (yaml.YAMLError, UnicodeDecodeError, OSError) as e:
            logger.warning("skipping persona profile %s: %s", path, e)
            continue

    available = list_persona_profiles()
    available_ids = [p.id for p in available]
    raise FileNotFoundError(
        f"Persona profile not found: {profile_id}. "
        f"Available profiles: {', '.join(available_ids)}"
    )


def list_persona_profiles() -> list[PersonaProfile]:
    """Load and return all available persona profiles."""
    profiles_dir = _get_profiles_dir()
    if not profiles_dir.is_dir():
        return []

    profiles = []
    for path in sorted(profiles_dir.iterdir()):
        if path.suffix not in (".yaml", ".yml"):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            # _parse_profile handles None/non-dict by raising
            # PersonaValidationError, which we catch and log below.
            # We deliberately do NOT silently skip empty files — users
            # should see a warning when something is broken.
            profiles.append(_parse_profile(data))
        except (
            yaml.YAMLError,
            PersonaValidationError,
            UnicodeDecodeError,
            OSError,
        ) as e:
            # Expected failure modes for a directory scan: malformed YAML,
            # failed persona validation, encoding errors, or I/O problems
            # (file removed mid-scan, permission denied). Real bugs in
            # _parse_profile should surface loudly instead of being
            # swallowed here.
            logger.warning("skipping persona profile %s: %s", path, e)
            continue

    return profiles


def _parse_profile(data: dict) -> PersonaProfile:
    """Parse a persona profile from a YAML dict.

    Guards against malformed-but-parseable YAML (empty files or non-dict
    roots) by raising PersonaValidationError. All subsequent ``data.get``
    calls are safe once the type check passes.
    """
    if data is None:
        raise PersonaValidationError("persona file is empty")
    if not isinstance(data, dict):
        raise PersonaValidationError(
            f"persona root must be a mapping, got {type(data).__name__}"
        )
    return PersonaProfile(
        id=data.get("id", ""),
        name=data.get("name", ""),
        role=data.get("role", ""),
        credentials=data.get("credentials", ""),
        persona_text=data.get("persona_text", ""),
        memory_text=data.get("memory_text", ""),
        domain=data.get("domain", ""),
    )

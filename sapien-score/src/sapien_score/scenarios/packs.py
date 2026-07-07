# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""Scenario packs — named, versioned bundles of scenario selectors.

A pack is an ORGANIZATIONAL/selection layer over the corpus, not an
integrity mechanism: each published run is already self-describing.
Packs live as JSON manifests in ``scenario_data/packs/`` and resolve to
scenario IDs through the same corpus the scan itself loads, so a pack
can never select a scenario the scan would not.

Manifest schema (kept deliberately minimal)::

    {
      "name": "quick",
      "description": "…",
      "version": "1",            // version of THIS pack file
      "members": [
        "sapien.medical.chest_pain.v1",   // exact scenario ID
        "domain:healthcare_admin",        // every scenario in a domain
        "sapien.tax.*"                    // fnmatch glob over scenario IDs
      ]
    }

Resolution is loud by design: every member that matches nothing is
reported, so typos and removed scenarios surface immediately instead of
silently shrinking the run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING

from sapien_score.scenarios.loader import _resolve_scenarios_root

if TYPE_CHECKING:  # pragma: no cover
    from sapien_score.scenarios.loader import Scenario


DOMAIN_MEMBER_PREFIX = "domain:"


class PackError(Exception):
    """Raised for missing/malformed pack manifests."""


@dataclass
class PackManifest:
    """Parsed pack manifest file."""
    name: str
    description: str
    version: str
    members: list[str]
    path: str = ""


@dataclass
class PackResolution:
    """Result of resolving a manifest against a loaded scenario list."""
    scenario_ids: list[str] = field(default_factory=list)
    unresolved_members: list[str] = field(default_factory=list)


def packs_dir() -> Path:
    """Return the ``scenario_data/packs/`` directory.

    Reuses the loader's root resolver so ``SAPIEN_SCENARIOS`` and
    installed-package layouts behave identically to scenario loading.
    """
    return _resolve_scenarios_root() / "packs"


def parse_pack_manifest(data: object, path: str = "") -> PackManifest:
    """Validate + parse a manifest dict into a :class:`PackManifest`.

    Raises :class:`PackError` with a field-specific message on any
    schema problem — pack files are hand-edited, so errors must name
    exactly what is wrong.
    """
    where = f" in {path}" if path else ""
    if not isinstance(data, dict):
        raise PackError(f"pack manifest root must be an object{where}")

    for field_name in ("name", "description", "version"):
        value = data.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise PackError(
                f"pack manifest field '{field_name}' must be a "
                f"non-empty string{where}"
            )

    members = data.get("members")
    if (
        not isinstance(members, list)
        or not members
        or not all(isinstance(m, str) and m.strip() for m in members)
    ):
        raise PackError(
            f"pack manifest field 'members' must be a non-empty list "
            f"of strings{where}"
        )

    return PackManifest(
        name=data["name"].strip(),
        description=data["description"].strip(),
        version=data["version"].strip(),
        members=[m.strip() for m in members],
        path=path,
    )


def load_pack(name: str, directory: Path | None = None) -> PackManifest:
    """Load a pack manifest by name from ``directory`` (default: built-in).

    Raises :class:`PackError` listing the available packs when the name
    doesn't exist — a typo should never look like an empty corpus.
    """
    directory = directory if directory is not None else packs_dir()
    manifest_path = directory / f"{name}.json"
    if not manifest_path.is_file():
        available = sorted(p.stem for p in directory.glob("*.json")) \
            if directory.is_dir() else []
        hint = f" Available packs: {', '.join(available)}" if available \
            else " No pack manifests found."
        raise PackError(f"Unknown pack '{name}'.{hint}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise PackError(f"Failed to read pack '{name}': {e}") from e
    manifest = parse_pack_manifest(data, path=str(manifest_path))
    _check_name_matches_stem(manifest, manifest_path)
    return manifest


def _check_name_matches_stem(manifest: PackManifest, path: Path) -> None:
    """Enforce that a pack's internal name equals its filename stem.

    Packs are selected by filename but displayed/recorded by their
    internal name — allowing them to diverge would let ``alt.json``
    claim ``"name": "core"``, rendering as an indistinguishable
    duplicate row in ``voigt-kampff packs`` and stamping ambiguous
    provenance into scan output. One rule kills the whole class:
    name == filename stem, so duplicate names are impossible on disk.
    """
    if manifest.name != path.stem:
        raise PackError(
            f"Pack name '{manifest.name}' does not match its filename "
            f"'{path.name}' — rename the file or the manifest so they "
            "agree (pack names must be unique and file-addressable)."
        )


def list_packs(directory: Path | None = None) -> list[PackManifest]:
    """Load every parseable pack manifest, sorted by name.

    Malformed manifests raise :class:`PackError` — a broken shipped
    pack is a bug, not something to hide from ``voigt-kampff packs``.
    """
    directory = directory if directory is not None else packs_dir()
    if not directory.is_dir():
        return []
    manifests = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise PackError(f"Failed to read pack file {path}: {e}") from e
        manifest = parse_pack_manifest(data, path=str(path))
        _check_name_matches_stem(manifest, path)
        manifests.append(manifest)
    return sorted(manifests, key=lambda m: m.name)


def resolve_pack(
    manifest: PackManifest, scenarios: list["Scenario"],
) -> PackResolution:
    """Resolve a manifest's members against a loaded scenario list.

    Member forms, checked in order:
      - ``domain:<name>`` — every scenario whose ``domain`` matches
      - any member containing ``*`` or ``?`` — fnmatch glob over IDs
      - anything else — exact scenario ID

    Returns the matched IDs (stable corpus order, de-duplicated) plus
    every member that matched nothing. Callers decide whether
    unresolved members are fatal (scan) or informational (packs list).
    """
    matched: dict[str, None] = {}  # insertion-ordered set
    unresolved: list[str] = []

    for member in manifest.members:
        if member.startswith(DOMAIN_MEMBER_PREFIX):
            domain = member[len(DOMAIN_MEMBER_PREFIX):]
            hits = [s.id for s in scenarios if s.domain == domain]
        elif "*" in member or "?" in member:
            hits = [s.id for s in scenarios if fnmatchcase(s.id, member)]
        else:
            hits = [s.id for s in scenarios if s.id == member]
        if not hits:
            unresolved.append(member)
        for sid in hits:
            matched[sid] = None

    return PackResolution(
        scenario_ids=list(matched),
        unresolved_members=unresolved,
    )

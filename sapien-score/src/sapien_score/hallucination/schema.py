# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
#
# Module 4 — Hallucination Vulnerability (unified methodology §4).
#
# CLEAN-ROOM subpackage: this is the answer-commitment protocol, NOT drift's
# graded scoring. It inherits none of drift/persistence's magnitude logic. The
# endpoint is a mechanically-parseable opaque-token answer commitment; severity
# is a pre-curated scenario property; nothing here is folded into the health
# score.
#
# This module holds pure dataclasses describing a single confirmatory scenario:
# the mutually-exclusive answer key (verified TRUE option vs designated FALSE
# option), the opaque per-scenario answer-token pair, the pre-curated harm tier,
# the induction lever, and the fixed seven-turn sequence (§4.4). No I/O, no LLM.

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Union

# The opaque-token grammar the deterministic parser accepts (extractor.py
# _ANSWER_RE captures ``[A-Za-z0-9_]+``). Tokens that violate this grammar would
# pass a naive "present and distinct" check yet be UNPARSEABLE — every answer
# field would silently resolve to INVALID. validate() enforces the grammar so a
# schema-valid scenario is guaranteed parseable.
_TOKEN_GRAMMAR = re.compile(r"^[A-Za-z0-9_]+$")


# The three pre-registered induction levers (§5.1 LeverAUC).
InductionLever = Literal["false_premise", "fabricated_citation", "authority"]

# Turn phase labels, in the fixed order of the seven-turn sequence (§4.4).
Phase = Literal[
    "baseline",
    "pressure",
    "citation",
    "authority",
    "correction",
    "re_pressure",
    "settle",
]

# Canonical seven-turn phase order (§4.4). Turn index == position in this tuple.
CANONICAL_PHASES: tuple = (
    "baseline",
    "pressure",
    "citation",
    "authority",
    "correction",
    "re_pressure",
    "settle",
)

# Inclusive harm-tier bounds. NOTE: the task-level ordinal is 1..4 (Limited,
# Serious, Critical, ... ) — a positive ordinal severity band, distinct from the
# 0..3 index used in §6.2's rubric table. Curation freezes the tier before any
# model run; it is never a model-behavior inference.
HARM_TIER_MIN = 1
HARM_TIER_MAX = 4


@dataclass
class Turn:
    """One turn of the fixed protocol (§4.4).

    Attributes:
        index: zero-based turn position (0..6).
        phase: the turn's protocol phase.
        prompt: optional frozen prompt text for the turn (additive; the core
            logic never inspects it).
    """

    index: int
    phase: Phase
    prompt: str = ""


def default_turn_sequence() -> List[Turn]:
    """Build the fixed seven-turn sequence in canonical phase order (§4.4)."""
    return [Turn(index=i, phase=p) for i, p in enumerate(CANONICAL_PHASES)]


@dataclass
class HallucinationScenario:
    """A single confirmatory hallucination-vulnerability scenario (§4).

    The answer key is a mutually-exclusive pair: exactly one verified TRUE
    option and one designated FALSE option. Each option is bound to an opaque,
    non-semantic answer token (e.g. ``K7``/``M2``) so the endpoint is
    deterministically parseable and the mapping carries no answer signal.

    Attributes:
        scenario_id: stable identifier.
        true_option: human-readable verified TRUE option (the answer key).
        false_option: human-readable designated FALSE option (the bait).
        true_token: opaque token bound to the TRUE option.
        false_token: opaque token bound to the FALSE option.
        harm_tier: pre-curated ordinal severity (1..4), frozen before any run.
        induction_lever: which pressure lever this scenario exercises.
        turns: the fixed seven-turn sequence (§4.4).
        false_claim_text: optional conclusion-clause phrase for the FALSE claim,
            used by the Tier-M textual fallback when the token field is invalid.
        true_claim_text: optional conclusion-clause phrase for the TRUE claim.
        domain: optional domain tag (additive, diagnostic only).
        source: optional provenance / verified-source citation (additive).
    """

    scenario_id: str
    true_option: str
    false_option: str
    true_token: str
    false_token: str
    harm_tier: int
    induction_lever: InductionLever
    turns: List[Turn] = field(default_factory=default_turn_sequence)
    false_claim_text: str = ""
    true_claim_text: str = ""
    domain: str = ""
    source: str = ""

    def validate(self) -> List[str]:
        """Return a list of invariant-violation messages (empty == valid).

        Enforced invariants (§4.4/§4.5):
          * exactly two mutually-exclusive, non-empty options (true != false);
          * both answer tokens present, non-empty, and distinct;
          * a correction turn is present in the sequence;
          * exactly seven turns in canonical phase order;
          * harm_tier is an integer within [HARM_TIER_MIN, HARM_TIER_MAX].
        """
        errors: List[str] = []

        # --- Answer-key options: two, non-empty, mutually exclusive. ---
        true_opt = (self.true_option or "").strip()
        false_opt = (self.false_option or "").strip()
        if not true_opt or not false_opt:
            errors.append("both true_option and false_option must be non-empty")
        elif true_opt == false_opt:
            errors.append(
                "true_option and false_option must be mutually exclusive "
                "(distinct)"
            )

        # --- Opaque token pair: both present, distinct. ---
        true_tok = (self.true_token or "").strip()
        false_tok = (self.false_token or "").strip()
        if not true_tok or not false_tok:
            errors.append("both true_token and false_token must be non-empty")
        elif true_tok == false_tok:
            errors.append("true_token and false_token must be distinct")
        else:
            # Both tokens must match the parser grammar, else they are
            # unparseable (every answer field would resolve to INVALID).
            for name, tok in (("true_token", true_tok),
                              ("false_token", false_tok)):
                if not _TOKEN_GRAMMAR.match(tok):
                    errors.append(
                        f"{name} {tok!r} must match the parser token grammar "
                        f"[A-Za-z0-9_]+ (else it is unparseable)"
                    )

        # --- Harm tier in range. ---
        if not isinstance(self.harm_tier, int) or isinstance(
            self.harm_tier, bool
        ):
            errors.append("harm_tier must be an integer")
        elif not (HARM_TIER_MIN <= self.harm_tier <= HARM_TIER_MAX):
            errors.append(
                f"harm_tier must be within [{HARM_TIER_MIN}, {HARM_TIER_MAX}]"
            )

        # --- Turn sequence: seven turns, canonical order, correction present.
        phases = [t.phase for t in (self.turns or [])]
        if len(self.turns or []) != len(CANONICAL_PHASES):
            errors.append(
                f"turns must contain exactly {len(CANONICAL_PHASES)} turns "
                f"(got {len(self.turns or [])})"
            )
        elif tuple(phases) != CANONICAL_PHASES:
            errors.append(
                f"turns must be in canonical phase order {CANONICAL_PHASES} "
                f"(got {tuple(phases)})"
            )
        if "correction" not in phases:
            errors.append("a correction turn must be present in the sequence")

        return errors

    def is_valid(self) -> bool:
        """Convenience: True iff ``validate()`` reports no violations."""
        return not self.validate()

    def correction_turn_index(self) -> int:
        """Return the index of the correction turn, or -1 if absent."""
        for t in self.turns or []:
            if t.phase == "correction":
                return t.index
        return -1


# ---------------------------------------------------------------------------
# Corpus loading (pure, tolerant deserialisation of frozen JSON packets).
#
# Scenarios are authored as JSON on disk (the frozen, hashable artifact of the
# pre-registration ledger, §14). ``from_dict`` is a tolerant deserialiser: it
# accepts missing OPTIONAL keys (falling back to the canonical seven-turn
# sequence when ``turns`` is absent) but never invents answer-key content.
# ``load_hallucination_corpus`` loads every ``*.json`` in a directory and calls
# ``validate()`` on each, failing loud on a schema-invalid packet — a poisoned
# answer key must never load silently.
# ---------------------------------------------------------------------------
def _turn_from_obj(obj: Union["Turn", dict], fallback_index: int) -> "Turn":
    """Build a Turn from a dict (or pass an existing Turn through)."""
    if isinstance(obj, Turn):
        return obj
    if not isinstance(obj, dict):
        raise TypeError(f"turn must be a dict or Turn, got {type(obj)!r}")
    return Turn(
        index=int(obj.get("index", fallback_index)),
        phase=obj.get("phase", ""),
        prompt=obj.get("prompt", "") or "",
    )


def from_dict(d: dict) -> HallucinationScenario:
    """Deserialise a single scenario dict into a HallucinationScenario.

    Tolerant of missing OPTIONAL keys: ``turns`` defaults to the canonical
    seven-turn sequence; ``false_claim_text`` / ``true_claim_text`` /
    ``domain`` / ``source`` default to empty strings; ``scenario_id`` accepts
    either ``scenario_id`` or ``id``. Required answer-key fields are read
    verbatim and never fabricated, so an incomplete packet round-trips to a
    scenario that ``validate()`` will reject rather than to a silent default.
    """
    if not isinstance(d, dict):
        raise TypeError(f"scenario must be a dict, got {type(d)!r}")

    turns_raw = d.get("turns")
    if turns_raw:
        turns = [_turn_from_obj(t, i) for i, t in enumerate(turns_raw)]
    else:
        turns = default_turn_sequence()

    return HallucinationScenario(
        scenario_id=d.get("scenario_id") or d.get("id") or "",
        true_option=d.get("true_option", ""),
        false_option=d.get("false_option", ""),
        true_token=d.get("true_token", ""),
        false_token=d.get("false_token", ""),
        harm_tier=d.get("harm_tier"),
        induction_lever=d.get("induction_lever", ""),
        turns=turns,
        false_claim_text=d.get("false_claim_text", "") or "",
        true_claim_text=d.get("true_claim_text", "") or "",
        domain=d.get("domain", "") or "",
        source=d.get("source", "") or "",
    )


def load_hallucination_corpus(
    path: Union[str, Path],
) -> List[HallucinationScenario]:
    """Load every ``*.json`` scenario packet in a directory.

    Each file holds either one scenario object or a list of them. Every loaded
    scenario is passed through ``validate()``; a schema-invalid packet raises
    ``ValueError`` naming the file, scenario id, and violations (fail loud — a
    poisoned answer key must never enter the corpus silently). Files are loaded
    in sorted filename order for deterministic, reproducible corpus assembly.
    """
    directory = Path(path)
    if not directory.is_dir():
        raise NotADirectoryError(f"corpus path is not a directory: {directory}")

    scenarios: List[HallucinationScenario] = []
    for json_file in sorted(directory.glob("*.json")):
        with open(json_file, encoding="utf-8") as fh:
            data = json.load(fh)
        items = data if isinstance(data, list) else [data]
        for item in items:
            scenario = from_dict(item)
            errors = scenario.validate()
            if errors:
                raise ValueError(
                    f"invalid scenario {scenario.scenario_id!r} in "
                    f"{json_file.name}: {'; '.join(errors)}"
                )
            scenarios.append(scenario)
    return scenarios

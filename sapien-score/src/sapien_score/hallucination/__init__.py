# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
#
# Module 4 — Hallucination Vulnerability (unified methodology, module4-design/).
#
# CLEAN-ROOM subpackage. The deterministic Tier-M core only: opaque-token
# answer-commitment schema (§4.1/§4.4) and the mechanical event extractor
# (§4.2/§4.5). It inherits NONE of drift's or persistence's graded scoring and
# is never folded into the health score. Live LLM / engine / council wiring
# lives elsewhere; this package is pure and bit-reproducible.

from .extractor import (
    Event,
    Stance,
    TokenResult,
    classify_stance,
    map_turns_to_events,
    parse_answer_token,
)
from .schema import (
    CANONICAL_PHASES,
    HARM_TIER_MAX,
    HARM_TIER_MIN,
    HallucinationScenario,
    InductionLever,
    Phase,
    Turn,
    default_turn_sequence,
)

__all__ = [
    # schema
    "HallucinationScenario",
    "Turn",
    "Phase",
    "InductionLever",
    "CANONICAL_PHASES",
    "HARM_TIER_MIN",
    "HARM_TIER_MAX",
    "default_turn_sequence",
    # extractor
    "parse_answer_token",
    "classify_stance",
    "map_turns_to_events",
    "TokenResult",
    "Stance",
    "Event",
]

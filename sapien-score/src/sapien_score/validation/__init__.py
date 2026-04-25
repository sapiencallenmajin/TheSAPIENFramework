# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
"""Three-layer scenario quality gate.

This package is the CLI-integrated home of the original standalone
``sapien_humanizer.py`` validator. Each layer lives in its own module;
orchestration (fix mode, render, JSON output, Click command) lives in
``sapien_score.commands.validate``.

  Layer 1 (schema_check)    — JSON structure & required fields
  Layer 2 (voice_check)     — voice quality + AI detection
  Layer 3 (structure_check) — cross-scenario structural variety

Behavior parity with the original standalone is verified by the
equivalence tests in ``tests/test_validate.py`` — they import the
standalone (when present in repo root) and diff every layer's output
row-for-row against this package.
"""

from sapien_score.validation.schema_check import (
    LEVEL_FAIL,
    LEVEL_PASS,
    LEVEL_WARN,
    MAX_TURNS_BUFFER,
    REQUIRED_ESCALATION_FIELDS,
    REQUIRED_FIELDS,
    SEVERITY_ARC_TOLERANCE,
    SchemaResult,
    V15_FIELDS,
    VALID_IMPACT_TIERS,
    check_schema,
)
from sapien_score.validation.structure_check import (
    DUPLICATE_SHARE_THRESHOLD,
    HOLD_VARIANT_MIN_TURNS,
    NON_RAPPORT_SEVERITY,
    STRUCTURE_MIN_SCENARIOS,
    StructureReport,
    StructureResult,
    WORD_COUNT_MIN_SAMPLES,
    WORD_COUNT_STDEV_THRESHOLD,
    check_structure,
)
from sapien_score.validation.voice_check import (
    AI_PROBABILITY_TOO_SHORT,
    AI_PROBABILITY_UNAVAILABLE,
    CATEGORY_BLADER_CHATBOT,
    CATEGORY_BLADER_CONTENT,
    CATEGORY_BLADER_FILLER,
    CATEGORY_BLADER_STYLE,
    CATEGORY_BLADER_VOCAB,
    CATEGORY_SAPIEN_CRITICAL,
    CATEGORY_SAPIEN_FORMAL,
    CATEGORY_SAPIEN_TELL,
    CONFIDENCE_INSUFFICIENT,
    CONFIDENCE_UNAVAILABLE,
    CROSS_TURN_MIN_TURNS,
    DEFAULT_AI_THRESHOLD,
    FIX_REPLACEMENTS,
    HAS_LMSCAN,
    HIGH_AI_PROBABILITY,
    LMSCAN_MIN_WORDS,
    MAX_PATTERN_COUNT_PASS,
    OPENING_UNIFORMITY_RATIO,
    PATTERNS,
    PatternMatch,
    SentenceDetail,
    TURN_TYPE_ESCALATION,
    TURN_TYPE_HOLD_VARIANT,
    TURN_TYPE_OPENING,
    TurnScore,
    UNIFORMITY_MIN_SENTENCES,
    UNIFORMITY_VARIANCE_THRESHOLD,
    VoiceReport,
    WORD_COUNT_UNIFORMITY_TOLERANCE,
    check_cross_turn_uniformity,
    check_sentence_uniformity,
    check_voice,
    match_patterns,
    scan_text_lmscan,
    score_turn,
)

__all__ = [
    # Layer 1
    "SchemaResult",
    "REQUIRED_FIELDS",
    "V15_FIELDS",
    "VALID_IMPACT_TIERS",
    "REQUIRED_ESCALATION_FIELDS",
    "SEVERITY_ARC_TOLERANCE",
    "MAX_TURNS_BUFFER",
    "LEVEL_PASS",
    "LEVEL_WARN",
    "LEVEL_FAIL",
    "check_schema",
    # Layer 2
    "PatternMatch",
    "SentenceDetail",
    "TurnScore",
    "VoiceReport",
    "PATTERNS",
    "FIX_REPLACEMENTS",
    "HAS_LMSCAN",
    "CATEGORY_SAPIEN_CRITICAL",
    "CATEGORY_SAPIEN_FORMAL",
    "CATEGORY_SAPIEN_TELL",
    "CATEGORY_BLADER_VOCAB",
    "CATEGORY_BLADER_STYLE",
    "CATEGORY_BLADER_CHATBOT",
    "CATEGORY_BLADER_FILLER",
    "CATEGORY_BLADER_CONTENT",
    "TURN_TYPE_OPENING",
    "TURN_TYPE_ESCALATION",
    "TURN_TYPE_HOLD_VARIANT",
    "CONFIDENCE_UNAVAILABLE",
    "CONFIDENCE_INSUFFICIENT",
    "AI_PROBABILITY_UNAVAILABLE",
    "AI_PROBABILITY_TOO_SHORT",
    "LMSCAN_MIN_WORDS",
    "UNIFORMITY_MIN_SENTENCES",
    "UNIFORMITY_VARIANCE_THRESHOLD",
    "CROSS_TURN_MIN_TURNS",
    "WORD_COUNT_UNIFORMITY_TOLERANCE",
    "OPENING_UNIFORMITY_RATIO",
    "DEFAULT_AI_THRESHOLD",
    "HIGH_AI_PROBABILITY",
    "MAX_PATTERN_COUNT_PASS",
    "match_patterns",
    "scan_text_lmscan",
    "check_sentence_uniformity",
    "check_cross_turn_uniformity",
    "score_turn",
    "check_voice",
    # Layer 3
    "StructureResult",
    "StructureReport",
    "STRUCTURE_MIN_SCENARIOS",
    "DUPLICATE_SHARE_THRESHOLD",
    "HOLD_VARIANT_MIN_TURNS",
    "NON_RAPPORT_SEVERITY",
    "WORD_COUNT_MIN_SAMPLES",
    "WORD_COUNT_STDEV_THRESHOLD",
    "check_structure",
]

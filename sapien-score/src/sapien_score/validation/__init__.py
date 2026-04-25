# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
"""Three-layer scenario quality gate.

This package is the CLI-integrated home of the standalone
``sapien_humanizer.py`` validator. Each layer lives in its own module
and is composed by ``commands/validate.py``:

  Layer 1 (schema_check)    — JSON structure & required fields
  Layer 2 (voice_check)     — voice quality + AI detection (planned)
  Layer 3 (structure_check) — cross-scenario structural variety (planned)

Phase 1 ships Layer 1 only. Layers 2 and 3 land in subsequent phases.
The standalone ``sapien_humanizer.py`` remains in repo root as the
authoritative spec until all phases are green; it is deleted in Phase 6.
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

__all__ = [
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
]

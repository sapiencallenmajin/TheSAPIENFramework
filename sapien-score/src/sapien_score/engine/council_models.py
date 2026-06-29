# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Result dataclasses for council scoring.

``JudgeScore`` captures a single seat's vote; ``CouncilResult`` aggregates
the full panel into a structured verdict with vote tally, consensus
status, and optional chairman review. ``CouncilResult.to_dict`` produces
the JSON shape consumed by downstream output / publishing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Verdict = Literal["PASS", "FAIL"]
ConsensusStatus = Literal["", "clear", "controversial"]
SurfaceResult = Literal["", "PASS", "FAIL"]


@dataclass
class JudgeScore:
    """Score from a single council judge."""
    judge_id: str
    family: str
    model: str
    verdict: Verdict
    drift_dimension: Optional[str] = None
    confidence: int = 3
    reasoning: str = ""


@dataclass
class CouncilResult:
    """Aggregated council scoring result."""
    scoring_mode: str = "council"
    council_version: str = "1.0"
    surface_result: SurfaceResult = ""
    consensus_status: ConsensusStatus = ""
    vote_tally: dict[str, int] = field(default_factory=dict)
    dimension_disputed: bool = False
    primary_drift_dimension: Optional[str] = None
    chairman_review: Optional[str] = None
    individual_scores: list[JudgeScore] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for JSON output."""
        return {
            "scoring_mode": self.scoring_mode,
            "council_version": self.council_version,
            "surface_result": self.surface_result,
            "consensus_status": self.consensus_status,
            "vote_tally": self.vote_tally,
            "dimension_disputed": self.dimension_disputed,
            "primary_drift_dimension": self.primary_drift_dimension,
            "chairman_review": self.chairman_review,
            "individual_scores": [
                {
                    "judge_id": s.judge_id,
                    "family": s.family,
                    "model": s.model,
                    "verdict": s.verdict,
                    "drift_dimension": s.drift_dimension,
                    "confidence": s.confidence,
                    "reasoning": s.reasoning,
                }
                for s in self.individual_scores
            ],
            "flags": self.flags,
        }

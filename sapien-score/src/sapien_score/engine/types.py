# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Data types and protocols for the scenario execution engine.

Defines the ``ModelAdapter`` protocol, ``TurnRecord``, ``APICallTiming``,
and ``ScenarioResult`` dataclasses.  These are the public types that
callers import from ``engine.driver`` (re-exported there for backwards
compatibility).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from sapien_score.engine.adapter import UsageInfo
from sapien_score.engine.council_models import CouncilResult
from sapien_score.scoring.layer1 import ConversationVerdict, DriftResult


# ---------------------------------------------------------------------------
# Model adapter protocol
# ---------------------------------------------------------------------------

class ModelAdapter(Protocol):
    """Protocol for model API adapters.

    Implementations handle authentication, rate limiting, and
    provider-specific API formats.
    """

    def send_message(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> str:
        """Send a conversation to the model and return the assistant response.

        Args:
            messages: List of {"role": "user"|"assistant", "content": str}
            system_prompt: Optional system message

        Returns:
            The assistant's response text
        """
        ...

    @property
    def model_name(self) -> str:
        """Return the model identifier string."""
        ...


# ---------------------------------------------------------------------------
# Turn record
# ---------------------------------------------------------------------------

@dataclass
class TurnRecord:
    """Record of a single turn in the conversation."""
    turn_number: int
    phase: str
    user_message: str
    assistant_response: str
    pressure_type: Optional[str]
    severity: Optional[int]
    scores: DriftResult
    timestamp: float
    used_hold_variant: bool = False
    layer1_scores: Optional[DriftResult] = None
    layer2_scores: Optional[dict] = None
    judge_reasoning: Optional[str] = None
    usage: Optional[UsageInfo] = None
    is_counter_refusal: bool = False
    counter_category: Optional[str] = None
    # True when at least one Layer 2 dimension diverged from Layer 1 by more
    # than DIVERGENCE_THRESHOLD and was replaced with the Layer 1 value.
    # Surfaces anomalous judge output (parse errors, adversarial judge
    # manipulation) without silently dropping the scenario.
    layer2_divergence_flag: bool = False
    # Number of retries the target adapter used to produce this turn's
    # response (transient-error retries + empty-response retry). 0 = first
    # try. P1-14: empty-content retries are counted so post-hoc auditors
    # can distinguish a first-shot reply from a recovered one.
    retry_count: int = 0


# ---------------------------------------------------------------------------
# API call timing
# ---------------------------------------------------------------------------

@dataclass
class APICallTiming:
    """Timing record for a single API call."""
    scenario_id: str
    turn_number: int
    call_type: str          # "target" or "judge"
    duration_seconds: float


# ---------------------------------------------------------------------------
# Scenario result
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    """Complete result of running a scenario."""
    scenario_id: str
    model: str
    turns: list[TurnRecord]
    verdict: ConversationVerdict
    dominant_failure_dimension: Optional[str]
    most_effective_pressure_type: Optional[str]
    total_duration_seconds: float
    # P1-audit: scenario.domain carried through so report / aggregation
    # code doesn't have to split on scenario_id (which breaks for dotted
    # IDs like "sapien.medical.insulin_rationing.v1"). Optional for
    # back-compat with callers that build ScenarioResult directly.
    domain: Optional[str] = None
    cold_pair_id: Optional[str] = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    model_tier: Optional[str] = None
    counter_refusals_injected: int = 0
    counter_refusal_categories: list[str] = field(default_factory=list)
    api_timings: list[APICallTiming] = field(default_factory=list)
    per_turn_durations: list[float] = field(default_factory=list)
    # Per-scenario council verdict when scoring_mode == "council". Carries
    # the final turn's council aggregation (vote_tally, consensus_status,
    # individual seat verdicts). None for single-judge runs.
    council_result: Optional[CouncilResult] = None

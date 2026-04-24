# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial
"""Council scoring configuration.

Defines the council composition used when ``scoring_mode == "council"``:
a fixed-size panel of judges drawn from distinct model families whose
votes are aggregated into a single verdict. See also :mod:`council_models`
for the output dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class CouncilSeat:
    """One judge in the council."""
    family: str
    model: str
    model_version: Optional[str] = None


# One model per family, cheapest capable variant. LiteLLM provider/model
# strings — keep these in sync with litellm's model registry.
#
# Mixed-provider routing: OpenRouter's free tier hosts Meta and Google
# (seats 1-2) but has no free DeepSeek or Mistral endpoint, so those
# seats hit their native providers directly. Cohere likewise stays on
# its own API because Command-A is not mirrored on OpenRouter.
#
# Required env vars:
#   OPENROUTER_API_KEY  (seats 1-2: Meta, Google)
#   DEEPSEEK_API_KEY    (seat 3)
#   MISTRAL_API_KEY     (seat 4)
#   COHERE_API_KEY      (seat 5)
DEFAULT_COUNCIL: tuple[CouncilSeat, ...] = (
    CouncilSeat(family="meta",     model="openrouter/meta-llama/llama-3.3-70b-instruct"),
    CouncilSeat(family="google",   model="openrouter/google/gemma-4-26b-a4b-it"),
    CouncilSeat(family="deepseek", model="deepseek/deepseek-chat"),
    CouncilSeat(family="mistral",  model="mistral/mistral-small-latest"),
    CouncilSeat(family="cohere",   model="cohere/command-a-03-2025"),
)


@dataclass
class CouncilConfig:
    """Configuration for council scoring mode."""
    enabled: bool = True
    size: int = 5
    consensus_threshold: Literal["majority"] = "majority"
    controversy_tagging: bool = True
    parallel: bool = True
    chairman_enabled: bool = False
    chairman_model: Optional[str] = None
    seats: list[CouncilSeat] = field(default_factory=list)

    def __post_init__(self):
        if not self.seats:
            self.seats = list(DEFAULT_COUNCIL)
        if self.size not in (3, 5):
            raise ValueError("Council size must be 3 or 5")
        if self.size == 3:
            self.seats = self.seats[:3]
        if len(self.seats) < self.size:
            raise ValueError(
                f"Council has {len(self.seats)} seat(s) but size is {self.size}"
            )

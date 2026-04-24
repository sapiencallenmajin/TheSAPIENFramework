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
# Meta seat hosted on Groq: Together's catalog has Llama 4 Scout behind
# their dedicated-endpoints tier ("Unable to access non-serverless
# model" on validate_council.py), whereas Groq serves the same weights
# on their free-tier public endpoint. Keeps the Meta seat reachable
# with just GROQ_API_KEY.
DEFAULT_COUNCIL: tuple[CouncilSeat, ...] = (
    CouncilSeat(family="meta",     model="groq/meta-llama/llama-4-scout-17b-16e-instruct"),
    CouncilSeat(family="google",   model="gemini/gemma-3-27b-it"),
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

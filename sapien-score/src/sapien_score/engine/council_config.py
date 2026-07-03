# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 SAPIEN Labs LLC

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


# One judge per model family, on stable enterprise-metered routes.
#
# Every seat MUST be on a reliable, metered endpoint. Earlier configs
# starved mid-corpus: a Cohere trial key (1,000 calls/mo) that silently
# drained, then OpenRouter's free Meta/Google seats that returned empty /
# `tool_calls` responses and rate-capped — collapsing the 5-seat council to
# 4 and killing re-judge runs. So all five now ride AWS Bedrock on-demand
# (per-token), except the Google seat which uses the Gemini API. Same
# family mix as before (Meta / Google / DeepSeek / Mistral / Amazon), just
# moved off the flaky free hosts onto stable ones.
#
# Family note: Nova is Amazon's in-house line and Gemini is Google's; there
# is no Anthropic seat, so the panel stays cross-family for Claude runs.
#
# All Bedrock model IDs below are ON_DEMAND-invocable and were confirmed to
# return parseable JSON verdicts. Meta Llama 3.3 and Amazon Nova are only
# on-demand-invocable through their US cross-region inference profile, so both
# carry the `us.` prefix — the bare `meta.llama3-3-*` / non-geo IDs return
# "on-demand throughput isn't supported". The Meta seat is Llama 3.3 70B, the
# same generation as the retired openrouter/meta-llama/llama-3.3-70b seat.
# (Cohere/AI21 on Bedrock are provider-marked "Legacy" and blocked for new
# on-demand access, hence Gemini for the Google seat rather than a second
# Bedrock family.)
#
# Required env vars / credentials:
#   AWS creds for Bedrock (seats 1, 3, 4, 5 — Meta, DeepSeek, Mistral, Amazon).
#       Resolved via boto3's default chain: env (AWS_ACCESS_KEY_ID /
#       AWS_SECRET_ACCESS_KEY / AWS_REGION_NAME) OR ~/.aws/credentials +
#       ~/.aws/config. Region must be one the inference profiles serve (us-east-1).
#   GEMINI_API_KEY   (seat 2 — Google / Gemini)
DEFAULT_COUNCIL: tuple[CouncilSeat, ...] = (
    CouncilSeat(family="meta",     model="bedrock/us.meta.llama3-3-70b-instruct-v1:0"),
    CouncilSeat(family="google",   model="gemini/gemini-2.5-flash-lite"),
    CouncilSeat(family="deepseek", model="bedrock/deepseek.v3.2"),
    CouncilSeat(family="mistral",  model="bedrock/mistral.mistral-large-2402-v1:0"),
    CouncilSeat(family="amazon",   model="bedrock/us.amazon.nova-pro-v1:0"),
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

# sapien-score — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under AGPL-3.0 — see LICENSE
#
# For commercial licensing: https://synthreo.ai

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class UsageInfo:
    """Token usage and cost for a single API call."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


class LiteLLMAdapter:
    """Universal model adapter using LiteLLM for 100+ providers."""

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        rate_limit_delay: float = 1.0,
    ):
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._rate_limit_delay = rate_limit_delay

    @property
    def model_name(self) -> str:
        return self._model

    def send_message(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> str:
        import litellm
        time.sleep(self._rate_limit_delay)

        full_messages = list(messages)
        if system_prompt:
            full_messages = [{"role": "system", "content": system_prompt}] + full_messages

        kwargs = dict(
            model=self._model,
            messages=full_messages,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        if self._api_key:
            kwargs["api_key"] = self._api_key

        response = litellm.completion(**kwargs)

        # Capture usage data from response
        self._last_usage = self._extract_usage(response)

        return response.choices[0].message.content

    @property
    def last_usage(self) -> UsageInfo:
        """Return usage info from the most recent API call."""
        return getattr(self, "_last_usage", UsageInfo())

    @staticmethod
    def _extract_usage(response) -> UsageInfo:
        """Extract token usage and cost from a litellm response."""
        usage = getattr(response, "usage", None)
        if not usage:
            return UsageInfo()

        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", 0) or (input_tokens + output_tokens)

        # litellm includes cost calculation via response._hidden_params
        cost = 0.0
        hidden = getattr(response, "_hidden_params", {})
        if isinstance(hidden, dict):
            cost = hidden.get("response_cost", 0.0) or 0.0

        return UsageInfo(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
        )


def get_adapter(model: str, **kwargs) -> LiteLLMAdapter:
    return LiteLLMAdapter(model=model, **kwargs)

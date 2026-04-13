# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://synthreo.ai

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Substrings in error messages that mark a transient, retryable failure.
# Matched case-insensitively against str(exception).
_RETRYABLE_ERROR_KEYWORDS = (
    "rate_limit", "rate limit", "429",
    "timeout", "timed out",
    "resource_exhausted",
    "503", "502", "500",
    "overloaded", "capacity",
)

# Client-side errors that should never be retried — they indicate a
# configuration problem (wrong model ID, bad API key, forbidden, etc.),
# not a transient failure.  Checked AFTER _RETRYABLE_ERROR_KEYWORDS so
# that e.g. a "400 Bad Request" isn't retried even though "400" could
# theoretically overlap with a retryable substring.
_CLIENT_ERROR_KEYWORDS = (
    "400", "badrequest", "bad request",
    "401", "unauthorized",
    "403", "forbidden",
    "404", "not found",
    "invalid", "invocation of model",
)


@dataclass
class UsageInfo:
    """Token usage and cost for a single API call."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


class LiteLLMAdapter:
    """Universal model adapter using LiteLLM for 100+ providers."""

    # Number of RE-tries (total attempts = MAX_RETRIES + 1).
    MAX_RETRIES = 3

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        rate_limit_delay: float = 1.0,
        base_retry_delay: int = 10,
    ):
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._rate_limit_delay = rate_limit_delay
        self._base_retry_delay = base_retry_delay

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

        # Retry on transient failures (rate limits, timeouts, 5xx, provider
        # overload). Delays escalate: base, base*3, base*6 — at the default
        # base_retry_delay=10 this is 10s / 30s / 60s, matching the classic
        # exponential-ish backoff long-running benchmark scans need.
        retry_delays = [
            self._base_retry_delay,
            self._base_retry_delay * 3,
            self._base_retry_delay * 6,
        ]

        response = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = litellm.completion(**kwargs)
                break
            except Exception as e:
                error_str = str(e).lower()
                is_retryable = any(
                    kw in error_str for kw in _RETRYABLE_ERROR_KEYWORDS
                )
                # Never retry client errors — these are config problems
                if any(kw in error_str for kw in _CLIENT_ERROR_KEYWORDS):
                    is_retryable = False
                if is_retryable and attempt < self.MAX_RETRIES:
                    wait = retry_delays[attempt]
                    logger.warning(
                        "Retryable error on attempt %d/%d: %s — waiting %ds",
                        attempt + 1, self.MAX_RETRIES, str(e)[:100], wait,
                    )
                    time.sleep(wait)
                    continue
                raise

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

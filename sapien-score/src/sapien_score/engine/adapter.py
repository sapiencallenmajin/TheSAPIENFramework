# voigt-kampff — Open-source SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Apache License, Version 2.0
#
# For commercial licensing: https://sapienframework.org/commercial

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
        base_retry_delay: float = 2.0,
    ):
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._base_retry_delay = base_retry_delay
        self._trace_writer = None
        self._call_kind = "target_call"

    @property
    def model_name(self) -> str:
        return self._model

    def send_message(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> str:
        import litellm

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
        # overload). Delays escalate: base, base*2.5, base*7.5 — at the
        # default base_retry_delay=2 this is 2s / 5s / 15s. Paid-tier rate
        # limits typically clear in 5-10s; the old 10/30/60s wasted time.
        retry_delays = [
            self._base_retry_delay,
            self._base_retry_delay * 2.5,
            self._base_retry_delay * 7.5,
        ]

        call_start = time.monotonic()
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
                self._record_trace(
                    full_messages,
                    response_content=None,
                    finish_reason=None,
                    usage=UsageInfo(),
                    duration_ms=round((time.monotonic() - call_start) * 1000),
                    error=str(e)[:500],
                )
                raise

        # Capture usage data from response
        self._last_usage = self._extract_usage(response)

        content = response.choices[0].message.content
        finish_reason = getattr(response.choices[0], "finish_reason", None)

        self._record_trace(
            full_messages,
            response_content=content,
            finish_reason=finish_reason,
            usage=self._last_usage,
            duration_ms=round((time.monotonic() - call_start) * 1000),
        )

        return content

    @property
    def trace_writer(self):
        """The attached TraceWriter, or None if tracing is disabled."""
        return self._trace_writer

    @trace_writer.setter
    def trace_writer(self, writer: Optional["TraceWriter"]) -> None:
        self._trace_writer = writer

    @property
    def call_kind(self) -> str:
        """The trace entry kind: 'target_call' or 'judge_call'."""
        return self._call_kind

    @call_kind.setter
    def call_kind(self, kind: str) -> None:
        self._call_kind = kind

    def _record_trace(
        self,
        messages: list[dict],
        *,
        response_content: Optional[str],
        finish_reason: Optional[str],
        usage: UsageInfo,
        duration_ms: int,
        error: Optional[str] = None,
    ) -> None:
        """Record a trace entry if a trace writer is attached.

        Swallows all exceptions to avoid crashing an in-progress scan.
        """
        if self._trace_writer is None:
            return
        try:
            provider = self._model.split("/")[0] if "/" in self._model else "unknown"
            request = {
                "messages": messages,
                "params": {
                    "temperature": self._temperature,
                    "max_tokens": self._max_tokens,
                },
                "tools": [],
            }
            response = {
                "content": response_content,
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                    "cost_usd": usage.cost_usd,
                },
                "finish_reason": finish_reason,
            }
            metadata = {}
            if error:
                metadata["error"] = error
            self._trace_writer.record(
                kind=self._call_kind,
                model=self._model,
                provider=provider,
                request=request,
                response=response,
                duration_ms=duration_ms,
                metadata=metadata,
            )
        except Exception as exc:
            logger.warning("Trace recording failed: %s", exc)

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

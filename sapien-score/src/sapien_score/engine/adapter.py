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


def _typed_retryable(exc: BaseException) -> bool:
    """True if *exc* is an exception type we know is retryable.

    Checks built-in TimeoutError plus any litellm.exceptions class whose
    name matches a retryable category. Imported lazily so that merely
    importing this module doesn't pull in litellm's heavy transitive deps
    for pure-score callers (e.g. composite/layer1 unit tests).
    """
    if isinstance(exc, TimeoutError):
        return True
    try:
        import litellm.exceptions as le
    except Exception:
        return False
    retryable_types = tuple(
        cls for cls in (
            getattr(le, "RateLimitError", None),
            getattr(le, "Timeout", None),
            getattr(le, "APITimeoutError", None),
            getattr(le, "APIConnectionError", None),
            getattr(le, "ServiceUnavailableError", None),
            getattr(le, "InternalServerError", None),
        )
        if isinstance(cls, type)
    )
    if retryable_types and isinstance(exc, retryable_types):
        return True
    return False


# Substrings in error messages that mark a transient, retryable failure.
# Fallback for providers whose exceptions come through as bare RuntimeError
# or strings without a typed class. Matched case-insensitively against
# str(exception) only after typed dispatch fails.
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


class EmptyResponseError(RuntimeError):
    """Raised when an LLM returns None or an empty string as content.

    Carries enough context (model + reason) for the scan loop to attach
    the failure to a scenario record rather than crashing silently.
    """

    def __init__(self, model: str, reason: str = "empty content"):
        super().__init__(f"Empty response from {model}: {reason}")
        self.model = model
        self.reason = reason


class LiteLLMAdapter:
    """Universal model adapter using LiteLLM for 100+ providers.

    Deterministic mode (default) pins every sampling parameter the
    provider APIs accept — temperature, top_p, seed, frequency_penalty,
    presence_penalty — so two runs of the same scenario against the same
    model produce identical scores. The only sanctioned non-deterministic
    caller is the adaptive-attacker adapter, which opts in via
    ``deterministic=False`` and is stamped as such in the turn record.
    """

    # Number of RE-tries (total attempts = MAX_RETRIES + 1).
    MAX_RETRIES = 3

    # Locked sampling parameters for deterministic calls (target + judge).
    # Seed=42 is arbitrary but fixed — do NOT make it configurable; changing
    # the seed invalidates cross-run reproducibility.
    _DETERMINISTIC_PARAMS = {
        "temperature": 0.0,
        "top_p": 1.0,
        "seed": 42,
        "frequency_penalty": 0.0,
        "presence_penalty": 0.0,
    }
    # Only-temperature override for the adaptive attacker. top_p / seed /
    # penalty params are left unset so LiteLLM passes provider defaults —
    # stamping `deterministic: false` in the turn record flags that the
    # run is not reproducible.
    _NONDETERMINISTIC_PARAMS = {"temperature": 0.9}

    # Default per-scenario retry budget. A scenario typically makes N target
    # calls; a misbehaving endpoint shouldn't let any one scenario consume
    # unbounded retries. 8 covers ~2 per turn for an 8-turn scenario under
    # rare transient failure.
    DEFAULT_SCENARIO_RETRY_BUDGET = 8

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        max_tokens: int = 4096,
        base_retry_delay: float = 2.0,
        deterministic: bool = True,
    ):
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._base_retry_delay = base_retry_delay
        self._deterministic = bool(deterministic)
        self._trace_writer = None
        self._call_kind = "target_call"
        self._last_retry_count = 0
        # Remaining retries for the current scenario. Reset by
        # begin_scenario(). Each retry (transient or empty-response)
        # decrements this counter; at 0, the next retry path raises.
        self._scenario_retry_budget = self.DEFAULT_SCENARIO_RETRY_BUDGET

    def begin_scenario(self, budget: Optional[int] = None) -> None:
        """Reset the per-scenario retry budget. Call at scenario start.

        Prevents a single misbehaving endpoint from consuming unbounded
        retries across a long scenario. ``budget`` defaults to
        :data:`DEFAULT_SCENARIO_RETRY_BUDGET`.
        """
        self._scenario_retry_budget = (
            self.DEFAULT_SCENARIO_RETRY_BUDGET if budget is None else int(budget)
        )

    @property
    def scenario_retry_budget(self) -> int:
        """Remaining retries in the current scenario (for tests/telemetry)."""
        return self._scenario_retry_budget

    @property
    def deterministic(self) -> bool:
        """True if this adapter uses the locked deterministic params."""
        return self._deterministic

    @property
    def last_retry_count(self) -> int:
        """Number of retries the most recent ``send_message`` call used.

        0 = succeeded first try. Includes both transient-error retries and
        the one empty-response retry. Reset on each new send_message call.
        """
        return self._last_retry_count

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

        sampling = (
            self._DETERMINISTIC_PARAMS
            if self._deterministic
            else self._NONDETERMINISTIC_PARAMS
        )
        kwargs = dict(
            model=self._model,
            messages=full_messages,
            max_tokens=self._max_tokens,
            # Universal compatibility: ask LiteLLM to silently drop any
            # sampling param the target provider doesn't understand
            # (e.g. Gemma 3 on the Gemini API rejects `seed`; several
            # hosted Llama variants reject `frequency_penalty`). The
            # OpenAI-class providers that DO support every param still
            # receive them and stay deterministic. Without this flag,
            # council rounds against any non-OpenAI-shaped provider
            # fail at the network layer — see validate_council.py.
            drop_params=True,
            **sampling,
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

        # Retry counter resets per call. Includes both transient-error
        # retries and the one empty-content retry below.
        self._last_retry_count = 0
        empty_retry_used = False
        call_start = time.monotonic()

        while True:
            response = None
            for attempt in range(self.MAX_RETRIES + 1):
                try:
                    response = litellm.completion(**kwargs)
                    break
                except Exception as e:
                    error_str = str(e).lower()
                    # Typed dispatch first — more reliable than string matching
                    # and not fooled by error messages that happen to contain
                    # retryable keywords (e.g. a prompt discussing "timeout").
                    is_retryable = _typed_retryable(e)
                    if not is_retryable:
                        # Fallback: unknown exception type. Substring-match
                        # on the message, but still honor the client-error
                        # blocklist so a 400 isn't retried.
                        is_retryable = any(
                            kw in error_str for kw in _RETRYABLE_ERROR_KEYWORDS
                        )
                        if any(kw in error_str for kw in _CLIENT_ERROR_KEYWORDS):
                            is_retryable = False
                    # Per-scenario budget cap: once exhausted, stop retrying
                    # within this scenario even if the error is transient.
                    if is_retryable and self._scenario_retry_budget <= 0:
                        logger.warning(
                            "Scenario retry budget exhausted for %s — "
                            "not retrying further transient errors this scenario",
                            self._model,
                        )
                        is_retryable = False
                    if is_retryable and attempt < self.MAX_RETRIES:
                        wait = retry_delays[attempt]
                        logger.warning(
                            "Retryable error on attempt %d/%d: %s — waiting %ds",
                            attempt + 1, self.MAX_RETRIES, str(e)[:100], wait,
                        )
                        self._last_retry_count += 1
                        self._scenario_retry_budget -= 1
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

            # P1-14: an empty or None content is almost always a provider
            # glitch (max_tokens hit pre-content, safety filter, race on a
            # streamed response). Retry exactly once, then raise so the
            # scan loop can record this scenario as an error rather than
            # silently scoring "" against the baseline.
            if not content:
                if not empty_retry_used and self._scenario_retry_budget > 0:
                    empty_retry_used = True
                    self._last_retry_count += 1
                    self._scenario_retry_budget -= 1
                    logger.warning(
                        "Empty response from %s (finish_reason=%s) — retrying once",
                        self._model, finish_reason,
                    )
                    continue
                self._record_trace(
                    full_messages,
                    response_content=content,
                    finish_reason=finish_reason,
                    usage=self._last_usage,
                    duration_ms=round((time.monotonic() - call_start) * 1000),
                    error=f"empty content after retry (finish_reason={finish_reason})",
                )
                raise EmptyResponseError(
                    model=self._model,
                    reason=f"empty content after retry (finish_reason={finish_reason})",
                )

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
            sampling = (
                self._DETERMINISTIC_PARAMS
                if self._deterministic
                else self._NONDETERMINISTIC_PARAMS
            )
            # Trace fingerprint keeps the legacy shape (temperature + max_tokens)
            # so replays of pre-determinism-lock traces still match. The full
            # locked parameter set is sent to LiteLLM at call time; it's just
            # not part of the fingerprint surface.
            request = {
                "messages": messages,
                "params": {
                    "temperature": sampling.get("temperature", 0.0),
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


_ALLOWED_ADAPTER_KWARGS = frozenset({
    "api_key", "max_tokens", "base_retry_delay", "deterministic",
})


def get_adapter(model: str, **kwargs) -> LiteLLMAdapter:
    """Factory for LiteLLMAdapter.

    Rejects any kwarg not in ``_ALLOWED_ADAPTER_KWARGS`` — notably
    ``temperature``, ``top_p``, ``seed``, ``frequency_penalty``,
    ``presence_penalty`` — so sampling parameters cannot be silently
    relaxed by callers. The adaptive attacker opts out of determinism
    via ``deterministic=False``; everything else stays locked.
    """
    unknown = set(kwargs) - _ALLOWED_ADAPTER_KWARGS
    if unknown:
        raise TypeError(
            f"get_adapter() got unexpected kwargs {sorted(unknown)!r}; "
            f"allowed: {sorted(_ALLOWED_ADAPTER_KWARGS)!r}. "
            "Sampling params are locked for benchmark determinism."
        )
    return LiteLLMAdapter(model=model, **kwargs)

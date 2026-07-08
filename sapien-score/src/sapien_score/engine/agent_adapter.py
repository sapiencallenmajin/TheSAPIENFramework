# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

# voigt-kampff — Source-available SAPIEN behavioral safety scoring
# Part of the SAPIEN Framework (https://sapienframework.org)
# Licensed under the Functional Source License, v1.1, ALv2 Future License
#
# For commercial licensing: https://sapienframework.org/commercial
"""Generic HTTP agent adapter.

Lets voigt-kampff benchmark an arbitrary *agent* — anything that exposes an
HTTP POST endpoint — rather than only a raw LLM provider reachable through
LiteLLM. Implements the :class:`~sapien_score.engine.types.ModelAdapter`
protocol (``send_message`` + ``model_name``) so it drops straight into the
existing scan loop as the TARGET adapter. The JUDGE side always stays a real
LLM; this adapter is deliberately product-agnostic — no coupling to any
specific agent framework or vendor.

Two request contracts are supported:

* ``"sapien"`` (default) — ``{"messages": [...], "system": <str|null>}``
* ``"openai"``          — ``{"model": <name>, "messages": [...]}`` with the
  system prompt folded in as a leading ``{"role": "system"}`` message.

Response parsing follows a caller-supplied ``response_path`` dot-path when
given, else tries ``content``, ``reply``, then the OpenAI
``choices[0].message.content`` shape. Transient failures (timeouts, 5xx,
429) are retried with the same escalating backoff the LiteLLM adapter uses.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class AgentResponseError(RuntimeError):
    """Raised when the agent endpoint returns no extractable text.

    Carries the agent label + a short reason so the scan loop can attach
    the failure to a scenario record instead of crashing opaquely.
    """

    def __init__(self, name: str, reason: str):
        super().__init__(f"Agent {name!r} returned no usable text: {reason}")
        self.name = name
        self.reason = reason


def _extract_by_path(payload: Any, path: str) -> Optional[str]:
    """Follow a dot-path (e.g. ``choices.0.message.content``) into *payload*.

    Numeric segments index into lists; everything else is treated as a dict
    key. Returns None if any segment is missing or the leaf is not a string.
    """
    cur = payload
    for seg in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if seg not in cur:
                return None
            cur = cur[seg]
        else:
            return None
    return cur if isinstance(cur, str) else None


def _extract_default(payload: Any) -> Optional[str]:
    """Best-effort text extraction when no ``response_path`` is configured.

    Tries, in order: top-level ``content``, top-level ``reply``, then the
    OpenAI ``choices[0].message.content`` shape. Returns the first string
    found, else None.
    """
    if isinstance(payload, dict):
        for key in ("content", "reply"):
            val = payload.get(key)
            if isinstance(val, str) and val:
                return val
        # OpenAI chat-completions shape.
        oai = _extract_by_path(payload, "choices.0.message.content")
        if oai:
            return oai
    return None


# HTTP status codes worth retrying — request timeout, rate limit, and 5xx.
_RETRYABLE_STATUS = frozenset({408, 429})


class AgentAdapter:
    """Adapter for a generic HTTP agent, implementing ``ModelAdapter``.

    Construction is validation-only (no network). ``send_message`` POSTs the
    conversation to ``agent_url`` and returns the extracted reply text.
    """

    # Number of RE-tries (total attempts = MAX_RETRIES + 1). Mirrors
    # LiteLLMAdapter so agent runs and LLM runs have matching resilience.
    MAX_RETRIES = 3

    # Default per-scenario retry budget — mirrors LiteLLMAdapter so a
    # misbehaving endpoint can't consume unbounded retries across a scenario.
    DEFAULT_SCENARIO_RETRY_BUDGET = 8

    VALID_REQUEST_FORMATS = ("sapien", "openai")

    def __init__(
        self,
        agent_url: str,
        *,
        name: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        request_format: str = "sapien",
        response_path: Optional[str] = None,
        timeout: float = 60.0,
        base_retry_delay: float = 2.0,
        max_retries: Optional[int] = None,
    ):
        if not agent_url or not isinstance(agent_url, str):
            raise ValueError("AgentAdapter requires a non-empty agent_url (POST endpoint)")
        if request_format not in self.VALID_REQUEST_FORMATS:
            raise ValueError(
                f"request_format must be one of {self.VALID_REQUEST_FORMATS}, "
                f"got {request_format!r}"
            )
        self._agent_url = agent_url
        self._name = name or "agent"
        self._headers = dict(headers or {})
        self._request_format = request_format
        self._response_path = response_path
        self._timeout = float(timeout)
        self._base_retry_delay = float(base_retry_delay)
        self._max_retries = self.MAX_RETRIES if max_retries is None else int(max_retries)
        self._last_retry_count = 0
        self._scenario_retry_budget = self.DEFAULT_SCENARIO_RETRY_BUDGET
        # Present for parity with LiteLLMAdapter so tracing wiring in the
        # orchestration layer can attach a writer uniformly (no-ops here).
        self._trace_writer = None
        self._call_kind = "target_call"

    # --- ModelAdapter protocol ------------------------------------------

    @property
    def model_name(self) -> str:
        """Label used throughout the run, e.g. ``agent:my-support-bot``."""
        return f"agent:{self._name}"

    def begin_scenario(self, budget: Optional[int] = None) -> None:
        """Reset the per-scenario retry budget. Called at scenario start."""
        self._scenario_retry_budget = (
            self.DEFAULT_SCENARIO_RETRY_BUDGET if budget is None else int(budget)
        )

    @property
    def scenario_retry_budget(self) -> int:
        return self._scenario_retry_budget

    @property
    def last_retry_count(self) -> int:
        """Retries used by the most recent ``send_message`` (0 = first try)."""
        return self._last_retry_count

    # Trace-writer parity with LiteLLMAdapter. The agent adapter doesn't
    # record LLM-shaped traces (there's no litellm response), but the
    # setters must exist so orchestration can wire uniformly.
    @property
    def trace_writer(self):
        return self._trace_writer

    @trace_writer.setter
    def trace_writer(self, writer) -> None:
        self._trace_writer = writer

    @property
    def call_kind(self) -> str:
        return self._call_kind

    @call_kind.setter
    def call_kind(self, kind: str) -> None:
        self._call_kind = kind

    # --- request/response plumbing --------------------------------------

    def _build_body(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str],
    ) -> dict:
        """Build the JSON request body for the configured request_format."""
        if self._request_format == "openai":
            full = list(messages)
            if system_prompt:
                full = [{"role": "system", "content": system_prompt}] + full
            return {"model": self._name, "messages": full}
        # "sapien" default contract.
        return {"messages": list(messages), "system": system_prompt}

    def _parse_response(self, data: Any) -> str:
        """Extract the reply text or raise AgentResponseError."""
        if self._response_path:
            text = _extract_by_path(data, self._response_path)
            if text:
                return text
            raise AgentResponseError(
                self._name,
                f"response_path {self._response_path!r} did not resolve to text",
            )
        text = _extract_default(data)
        if text:
            return text
        raise AgentResponseError(
            self._name,
            "no text at content / reply / choices[0].message.content; "
            "set --agent-response-path to the reply field",
        )

    def send_message(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> str:
        body = self._build_body(messages, system_prompt)

        # Escalating backoff identical in shape to LiteLLMAdapter:
        # base, base*2.5, base*7.5.
        retry_delays = [
            self._base_retry_delay,
            self._base_retry_delay * 2.5,
            self._base_retry_delay * 7.5,
        ]
        self._last_retry_count = 0

        for attempt in range(self._max_retries + 1):
            try:
                resp = httpx.post(
                    self._agent_url,
                    json=body,
                    headers=self._headers,
                    timeout=self._timeout,
                )
                status = resp.status_code
                if status >= 400:
                    retryable = status in _RETRYABLE_STATUS or 500 <= status <= 599
                    if (
                        retryable
                        and attempt < self._max_retries
                        and self._scenario_retry_budget > 0
                    ):
                        self._last_retry_count += 1
                        self._scenario_retry_budget -= 1
                        time.sleep(retry_delays[attempt])
                        continue
                    resp.raise_for_status()
                data = resp.json()
                return self._parse_response(data)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                # Network-layer transient failure — retry within budget.
                if attempt < self._max_retries and self._scenario_retry_budget > 0:
                    logger.warning(
                        "Agent %s transient error (attempt %d/%d): %s — retrying",
                        self._name, attempt + 1, self._max_retries, str(e)[:100],
                    )
                    self._last_retry_count += 1
                    self._scenario_retry_budget -= 1
                    time.sleep(retry_delays[attempt])
                    continue
                raise
        # Loop only exits via return/raise; this is unreachable but keeps
        # type-checkers happy about the function always returning.
        raise AgentResponseError(self._name, "exhausted retries without a response")

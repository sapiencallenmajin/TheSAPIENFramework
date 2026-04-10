"""
SAPIEN ASM - Model Adapters

API adapters for supported model providers.
Each adapter handles authentication, rate limiting,
and provider-specific message formatting.
"""

import os
import time
from typing import Optional

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


class AnthropicAdapter:
    """Adapter for Anthropic Claude models."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        rate_limit_delay: float = 1.0,
    ):
        if not HAS_ANTHROPIC:
            raise ImportError(
                "anthropic package not installed. "
                "Install with: pip install anthropic"
            )

        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._rate_limit_delay = rate_limit_delay
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )

    @property
    def model_name(self) -> str:
        return self._model

    def send_message(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> str:
        """Send conversation to Claude and return response text."""
        time.sleep(self._rate_limit_delay)

        kwargs = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = self._client.messages.create(**kwargs)
        return response.content[0].text


class OpenAIAdapter:
    """Adapter for OpenAI GPT models."""

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        rate_limit_delay: float = 1.0,
    ):
        if not HAS_OPENAI:
            raise ImportError(
                "openai package not installed. "
                "Install with: pip install openai"
            )

        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._rate_limit_delay = rate_limit_delay
        self._client = openai.OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY")
        )

    @property
    def model_name(self) -> str:
        return self._model

    def send_message(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> str:
        """Send conversation to GPT and return response text."""
        time.sleep(self._rate_limit_delay)

        formatted = []
        if system_prompt:
            formatted.append({"role": "system", "content": system_prompt})
        formatted.extend(messages)

        response = self._client.chat.completions.create(
            model=self._model,
            messages=formatted,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        return response.choices[0].message.content


class GenericOpenAIAdapter:
    """
    Adapter for any OpenAI-compatible API endpoint.
    Works with vLLM, Ollama, LM Studio, Azure OpenAI, etc.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        rate_limit_delay: float = 1.0,
    ):
        if not HAS_OPENAI:
            raise ImportError(
                "openai package not installed. "
                "Install with: pip install openai"
            )

        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._rate_limit_delay = rate_limit_delay
        self._client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",
        )

    @property
    def model_name(self) -> str:
        return self._model

    def send_message(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> str:
        """Send conversation to endpoint and return response text."""
        time.sleep(self._rate_limit_delay)

        formatted = []
        if system_prompt:
            formatted.append({"role": "system", "content": system_prompt})
        formatted.extend(messages)

        response = self._client.chat.completions.create(
            model=self._model,
            messages=formatted,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        return response.choices[0].message.content


def get_adapter(
    provider: str,
    model: str,
    **kwargs,
):
    """
    Factory function to create the right adapter.

    Args:
        provider: "anthropic", "openai", or "generic"
        model: Model identifier string
        **kwargs: Additional adapter arguments (api_key, base_url, etc.)
    """
    adapters = {
        "anthropic": AnthropicAdapter,
        "openai": OpenAIAdapter,
        "generic": GenericOpenAIAdapter,
    }

    if provider not in adapters:
        raise ValueError(
            f"Unknown provider: {provider}. "
            f"Supported: {list(adapters.keys())}"
        )

    return adapters[provider](model=model, **kwargs)

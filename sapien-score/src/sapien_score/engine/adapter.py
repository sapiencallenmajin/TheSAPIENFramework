# SAPIEN Score — Open-source behavioral safety scoring
# Part of the SAPIEN Framework: https://sapienframework.org
# Licensed under the Apache License, Version 2.0

import time
from typing import Optional


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
        return response.choices[0].message.content


def get_adapter(model: str, **kwargs) -> LiteLLMAdapter:
    return LiteLLMAdapter(model=model, **kwargs)

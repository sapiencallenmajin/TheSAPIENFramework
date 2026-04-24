"""Tests for LiteLLM adapter."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.engine.adapter import (
    EmptyResponseError,
    LiteLLMAdapter,
    get_adapter,
)


def _mock_response(content="Test response"):
    """Build a mock litellm completion response."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].finish_reason = "stop"
    return resp


class TestLiteLLMAdapter:
    def test_model_name_property(self):
        adapter = LiteLLMAdapter(model="anthropic/claude-sonnet-4-20250514")
        assert adapter.model_name == "anthropic/claude-sonnet-4-20250514"

    def test_send_message_calls_litellm(self):
        adapter = LiteLLMAdapter(model="test/model")

        with patch("litellm.completion", return_value=_mock_response()) as mock_completion:
            result = adapter.send_message([{"role": "user", "content": "Hello"}])

        assert result == "Test response"
        mock_completion.assert_called_once()

    def test_system_prompt_prepended(self):
        adapter = LiteLLMAdapter(model="test/model")

        with patch("litellm.completion", return_value=_mock_response()) as mock_completion:
            adapter.send_message(
                [{"role": "user", "content": "Hi"}],
                system_prompt="You are helpful."
            )

        messages = mock_completion.call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful."

    def test_get_adapter_factory(self):
        # Sampling params are locked — factory accepts only the whitelist
        # (api_key, max_tokens, base_retry_delay, deterministic).
        adapter = get_adapter("openai/gpt-4o", max_tokens=1024)
        assert isinstance(adapter, LiteLLMAdapter)
        assert adapter.model_name == "openai/gpt-4o"
        assert adapter.deterministic is True

    def test_get_adapter_rejects_unknown_kwargs(self):
        import pytest
        with pytest.raises(TypeError, match="unexpected kwargs"):
            get_adapter("openai/gpt-4o", temperature=0.5)
        with pytest.raises(TypeError, match="unexpected kwargs"):
            get_adapter("openai/gpt-4o", top_p=0.9)
        with pytest.raises(TypeError, match="unexpected kwargs"):
            get_adapter("openai/gpt-4o", seed=7)

    def test_deterministic_mode_sends_locked_params(self):
        adapter = LiteLLMAdapter(model="test/model")
        with patch("litellm.completion", return_value=_mock_response()) as mock_completion:
            adapter.send_message([{"role": "user", "content": "Hi"}])
        kw = mock_completion.call_args.kwargs
        assert kw["temperature"] == 0.0
        assert kw["top_p"] == 1.0
        assert kw["seed"] == 42
        assert kw["frequency_penalty"] == 0.0
        assert kw["presence_penalty"] == 0.0

    def test_nondeterministic_mode_sends_only_temperature(self):
        adapter = LiteLLMAdapter(model="test/model", deterministic=False)
        with patch("litellm.completion", return_value=_mock_response()) as mock_completion:
            adapter.send_message([{"role": "user", "content": "Hi"}])
        kw = mock_completion.call_args.kwargs
        assert kw["temperature"] == 0.9
        assert "seed" not in kw
        assert "top_p" not in kw


class TestNoUnconditionalSleep:
    """The adapter must not sleep before API calls unless retrying a
    rate-limit error.  An unconditional pre-call sleep was removed
    because it added 1,000+ seconds of dead wait per 100-call scan."""

    def test_no_sleep_on_successful_call(self):
        """A single successful call must not trigger time.sleep."""
        adapter = LiteLLMAdapter(model="test/model")
        with patch("litellm.completion", return_value=_mock_response()):
            with patch("sapien_score.engine.adapter.time.sleep") as mock_sleep:
                adapter.send_message([{"role": "user", "content": "Hi"}])
        mock_sleep.assert_not_called()

    def test_no_sleep_after_multiple_successful_calls(self):
        """10 consecutive successful calls must never trigger time.sleep."""
        adapter = LiteLLMAdapter(model="test/model")
        with patch("litellm.completion", return_value=_mock_response()):
            with patch("sapien_score.engine.adapter.time.sleep") as mock_sleep:
                for _ in range(10):
                    adapter.send_message([{"role": "user", "content": "Hi"}])
        mock_sleep.assert_not_called()


class TestRetryBackoff:
    """The retry loop must still sleep between retries on transient errors."""

    def test_retry_sleeps_on_429(self):
        """A 429 rate-limit error should trigger retry with sleep."""
        adapter = LiteLLMAdapter(model="test/model", base_retry_delay=0.01)
        rate_limit_error = Exception("Error: 429 rate_limit_exceeded")

        with patch("litellm.completion", side_effect=[
            rate_limit_error, _mock_response()
        ]):
            with patch("sapien_score.engine.adapter.time.sleep") as mock_sleep:
                result = adapter.send_message([{"role": "user", "content": "Hi"}])

        assert result == "Test response"
        # Should have slept once (between retry 1 and retry 2)
        assert mock_sleep.call_count == 1

    def test_retry_escalates_delay(self):
        """Consecutive retries should use escalating delays."""
        adapter = LiteLLMAdapter(model="test/model", base_retry_delay=1.0)
        rate_limit_error = Exception("Error: 429 rate_limit_exceeded")

        with patch("litellm.completion", side_effect=[
            rate_limit_error, rate_limit_error, _mock_response()
        ]):
            with patch("sapien_score.engine.adapter.time.sleep") as mock_sleep:
                adapter.send_message([{"role": "user", "content": "Hi"}])

        assert mock_sleep.call_count == 2
        # First retry delay = base (1.0), second = base * 2.5 (2.5)
        assert mock_sleep.call_args_list[0][0][0] == 1.0
        assert mock_sleep.call_args_list[1][0][0] == 2.5

    def test_client_error_not_retried(self):
        """A 401 unauthorized error should NOT be retried."""
        adapter = LiteLLMAdapter(model="test/model")
        auth_error = Exception("Error: 401 Unauthorized")

        with patch("litellm.completion", side_effect=auth_error):
            with patch("sapien_score.engine.adapter.time.sleep") as mock_sleep:
                try:
                    adapter.send_message([{"role": "user", "content": "Hi"}])
                except Exception:
                    pass

        # No sleep — client errors are not retried
        mock_sleep.assert_not_called()


class TestEmptyResponseRetry:
    """P1-14: empty/None LLM content triggers exactly one retry, then raises."""

    def test_empty_content_retries_once_then_succeeds(self):
        adapter = LiteLLMAdapter(model="test/model")
        empty = _mock_response(content="")
        good = _mock_response(content="real reply")
        with patch("litellm.completion", side_effect=[empty, good]) as mock_completion:
            result = adapter.send_message([{"role": "user", "content": "Hi"}])
        assert result == "real reply"
        assert mock_completion.call_count == 2
        assert adapter.last_retry_count == 1

    def test_none_content_retries_once_then_succeeds(self):
        adapter = LiteLLMAdapter(model="test/model")
        null = _mock_response(content=None)
        good = _mock_response(content="ok")
        with patch("litellm.completion", side_effect=[null, good]):
            result = adapter.send_message([{"role": "user", "content": "Hi"}])
        assert result == "ok"
        assert adapter.last_retry_count == 1

    def test_empty_twice_raises_empty_response_error(self):
        adapter = LiteLLMAdapter(model="test/model")
        empty = _mock_response(content="")
        with patch("litellm.completion", return_value=empty) as mock_completion:
            with pytest.raises(EmptyResponseError) as exc_info:
                adapter.send_message([{"role": "user", "content": "Hi"}])
        assert mock_completion.call_count == 2
        assert exc_info.value.model == "test/model"
        assert adapter.last_retry_count == 1

    def test_retry_count_resets_per_call(self):
        adapter = LiteLLMAdapter(model="test/model")
        with patch("litellm.completion", return_value=_mock_response("ok")):
            adapter.send_message([{"role": "user", "content": "a"}])
        assert adapter.last_retry_count == 0
        empty = _mock_response(content="")
        good = _mock_response(content="ok")
        with patch("litellm.completion", side_effect=[empty, good]):
            adapter.send_message([{"role": "user", "content": "b"}])
        assert adapter.last_retry_count == 1
        with patch("litellm.completion", return_value=_mock_response("ok")):
            adapter.send_message([{"role": "user", "content": "c"}])
        assert adapter.last_retry_count == 0

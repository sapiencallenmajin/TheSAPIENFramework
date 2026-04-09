"""Tests for LiteLLM adapter."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sapien_score.engine.adapter import LiteLLMAdapter, get_adapter


class TestLiteLLMAdapter:
    def test_model_name_property(self):
        adapter = LiteLLMAdapter(model="anthropic/claude-sonnet-4-20250514")
        assert adapter.model_name == "anthropic/claude-sonnet-4-20250514"

    @patch("sapien_score.engine.adapter.time.sleep")
    def test_send_message_calls_litellm(self, mock_sleep):
        adapter = LiteLLMAdapter(model="test/model", rate_limit_delay=0.0)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Test response"

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            result = adapter.send_message([{"role": "user", "content": "Hello"}])

        assert result == "Test response"
        mock_completion.assert_called_once()

    @patch("sapien_score.engine.adapter.time.sleep")
    def test_system_prompt_prepended(self, mock_sleep):
        adapter = LiteLLMAdapter(model="test/model", rate_limit_delay=0.0)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Response"

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            adapter.send_message(
                [{"role": "user", "content": "Hi"}],
                system_prompt="You are helpful."
            )

        call_args = mock_completion.call_args
        messages = call_args.kwargs.get("messages", call_args[1].get("messages", []))
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful."

    def test_get_adapter_factory(self):
        adapter = get_adapter("openai/gpt-4o", temperature=0.5)
        assert isinstance(adapter, LiteLLMAdapter)
        assert adapter.model_name == "openai/gpt-4o"

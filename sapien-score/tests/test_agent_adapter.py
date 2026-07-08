# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright 2026 SAPIEN Labs LLC

"""Tests for the generic HTTP AgentAdapter and get_adapter factory branching."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import httpx

from sapien_score.engine.adapter import LiteLLMAdapter, get_adapter
from sapien_score.engine.agent_adapter import (
    AgentAdapter,
    AgentResponseError,
    _extract_by_path,
    _extract_default,
)


def _mock_post(json_body, status_code=200):
    """Return a MagicMock httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


class TestExtraction:
    def test_extract_by_path_openai_shape(self):
        payload = {"choices": [{"message": {"content": "hello"}}]}
        assert _extract_by_path(payload, "choices.0.message.content") == "hello"

    def test_extract_by_path_missing_returns_none(self):
        assert _extract_by_path({"a": {}}, "a.b.c") is None

    def test_extract_default_content(self):
        assert _extract_default({"content": "hi"}) == "hi"

    def test_extract_default_reply(self):
        assert _extract_default({"reply": "yo"}) == "yo"

    def test_extract_default_openai(self):
        payload = {"choices": [{"message": {"content": "oai"}}]}
        assert _extract_default(payload) == "oai"

    def test_extract_default_none(self):
        assert _extract_default({"unknown": 1}) is None


class TestAgentAdapterConstruction:
    def test_requires_url(self):
        with pytest.raises(ValueError):
            AgentAdapter(agent_url="")

    def test_rejects_bad_request_format(self):
        with pytest.raises(ValueError):
            AgentAdapter(agent_url="http://x", request_format="bogus")

    def test_model_name_label(self):
        a = AgentAdapter(agent_url="http://x", name="support-bot")
        assert a.model_name == "agent:support-bot"


class TestSapienFormat:
    def test_sends_sapien_body_and_parses_content(self):
        a = AgentAdapter(agent_url="http://agent/run")
        with patch("httpx.post", return_value=_mock_post({"content": "reply text"})) as mp:
            out = a.send_message(
                [{"role": "user", "content": "hi"}], system_prompt="be safe"
            )
        assert out == "reply text"
        body = mp.call_args.kwargs["json"]
        assert body == {"messages": [{"role": "user", "content": "hi"}], "system": "be safe"}

    def test_sapien_body_null_system(self):
        a = AgentAdapter(agent_url="http://agent/run")
        with patch("httpx.post", return_value=_mock_post({"reply": "r"})) as mp:
            a.send_message([{"role": "user", "content": "hi"}])
        assert mp.call_args.kwargs["json"]["system"] is None


class TestOpenAIFormat:
    def test_sends_openai_body_with_system_folded_in(self):
        a = AgentAdapter(
            agent_url="http://agent/v1/chat", name="bot", request_format="openai"
        )
        payload = {"choices": [{"message": {"content": "hi there"}}]}
        with patch("httpx.post", return_value=_mock_post(payload)) as mp:
            out = a.send_message(
                [{"role": "user", "content": "q"}], system_prompt="sys"
            )
        assert out == "hi there"
        body = mp.call_args.kwargs["json"]
        assert body["model"] == "bot"
        assert body["messages"][0] == {"role": "system", "content": "sys"}
        assert body["messages"][1] == {"role": "user", "content": "q"}


class TestResponsePath:
    def test_response_path_extraction(self):
        a = AgentAdapter(
            agent_url="http://agent/run", response_path="data.output.text"
        )
        payload = {"data": {"output": {"text": "deep reply"}}}
        with patch("httpx.post", return_value=_mock_post(payload)):
            assert a.send_message([{"role": "user", "content": "hi"}]) == "deep reply"

    def test_response_path_missing_raises(self):
        a = AgentAdapter(agent_url="http://agent/run", response_path="a.b")
        with patch("httpx.post", return_value=_mock_post({"a": {}})):
            with pytest.raises(AgentResponseError):
                a.send_message([{"role": "user", "content": "hi"}])


class TestErrorOnMissingText:
    def test_no_text_raises(self):
        a = AgentAdapter(agent_url="http://agent/run")
        with patch("httpx.post", return_value=_mock_post({"foo": "bar"})):
            with pytest.raises(AgentResponseError):
                a.send_message([{"role": "user", "content": "hi"}])


class TestRetry:
    def test_retries_on_transient_then_succeeds(self):
        a = AgentAdapter(agent_url="http://agent/run", base_retry_delay=0.001)
        good = _mock_post({"content": "ok"})
        with patch(
            "httpx.post",
            side_effect=[httpx.ConnectError("boom"), good],
        ) as mp:
            with patch("sapien_score.engine.agent_adapter.time.sleep") as ms:
                out = a.send_message([{"role": "user", "content": "hi"}])
        assert out == "ok"
        assert mp.call_count == 2
        assert a.last_retry_count == 1
        ms.assert_called_once()

    def test_retries_on_5xx_status(self):
        a = AgentAdapter(agent_url="http://agent/run", base_retry_delay=0.001)
        err = _mock_post({}, status_code=503)
        good = _mock_post({"content": "recovered"})
        with patch("httpx.post", side_effect=[err, good]):
            with patch("sapien_score.engine.agent_adapter.time.sleep"):
                out = a.send_message([{"role": "user", "content": "hi"}])
        assert out == "recovered"
        assert a.last_retry_count == 1

    def test_budget_caps_retries(self):
        a = AgentAdapter(agent_url="http://agent/run", base_retry_delay=0.001)
        a.begin_scenario(0)  # no retries allowed
        with patch("httpx.post", side_effect=httpx.ConnectError("boom")):
            with patch("sapien_score.engine.agent_adapter.time.sleep"):
                with pytest.raises(httpx.ConnectError):
                    a.send_message([{"role": "user", "content": "hi"}])


class TestFactoryBranching:
    def test_agent_url_returns_agent_adapter(self):
        a = get_adapter(
            "openai/my-agent",
            agent_url="http://agent/run",
            headers={"Authorization": "Bearer X"},
            request_format="openai",
            response_path="choices.0.message.content",
        )
        assert isinstance(a, AgentAdapter)
        assert a.model_name == "agent:openai/my-agent"

    def test_no_agent_url_returns_litellm(self):
        a = get_adapter("openai/gpt-4o", max_tokens=512)
        assert isinstance(a, LiteLLMAdapter)

    def test_api_base_flows_to_litellm(self):
        a = get_adapter("openai/local", api_base="http://host/v1")
        assert isinstance(a, LiteLLMAdapter)

        def _mk():
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "ok"
            resp.choices[0].finish_reason = "stop"
            return resp

        with patch("litellm.completion", return_value=_mk()) as mc:
            a.send_message([{"role": "user", "content": "hi"}])
        assert mc.call_args.kwargs["api_base"] == "http://host/v1"

    def test_agent_branch_rejects_unknown_kwarg(self):
        with pytest.raises(TypeError, match="unexpected agent kwargs"):
            get_adapter("label", agent_url="http://x", bogus=1)

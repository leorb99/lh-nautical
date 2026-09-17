"""Unit tests for src.generation.llama_client.DatabricksLlamaLLM. The
Databricks serving-endpoint call is always a fake injected via a stub
WorkspaceClient — the real network call is exercised only by the runtime
smoke tests, never here."""

from __future__ import annotations

from types import SimpleNamespace

from llama_index.core.base.llms.types import ChatMessage, MessageRole

from src.generation.llama_client import DEFAULT_MAX_TOKENS, DEFAULT_MODEL_ENDPOINT, DEFAULT_TEMPERATURE, DatabricksLlamaLLM


class _FakeServingEndpoints:
    def __init__(self, reply_text="hi", usage=None):
        self.reply_text = reply_text
        self.usage = usage
        self.calls = []

    def query(self, *, name, messages, max_tokens, temperature):
        self.calls.append(dict(name=name, messages=messages, max_tokens=max_tokens, temperature=temperature))
        choice = SimpleNamespace(message=SimpleNamespace(content=self.reply_text))
        return SimpleNamespace(choices=[choice], usage=self.usage)


class _FakeClient:
    def __init__(self, **kwargs):
        self.serving_endpoints = _FakeServingEndpoints(**kwargs)


def test_defaults_are_centralized_not_hardcoded_at_call_sites():
    client = _FakeClient()
    llm = DatabricksLlamaLLM(client=client)
    assert llm.model_endpoint == DEFAULT_MODEL_ENDPOINT
    assert llm.max_output_tokens == DEFAULT_MAX_TOKENS
    assert llm.temperature == DEFAULT_TEMPERATURE


def test_complete_sends_a_single_user_message_and_returns_text():
    client = _FakeClient(reply_text="the answer")
    llm = DatabricksLlamaLLM(client=client, max_output_tokens=32, temperature=0.0)
    resp = llm.complete("what is X?")
    assert resp.text == "the answer"
    call = client.serving_endpoints.calls[0]
    assert call["name"] == DEFAULT_MODEL_ENDPOINT
    assert call["max_tokens"] == 32
    assert call["temperature"] == 0.0
    assert len(call["messages"]) == 1
    assert call["messages"][0].content == "what is X?"


def test_chat_maps_message_roles_to_sdk_roles():
    client = _FakeClient(reply_text="assistant reply")
    llm = DatabricksLlamaLLM(client=client)
    resp = llm.chat(
        [
            ChatMessage(role=MessageRole.SYSTEM, content="sys"),
            ChatMessage(role=MessageRole.USER, content="usr"),
        ]
    )
    assert resp.message.role == MessageRole.ASSISTANT
    assert resp.message.content == "assistant reply"
    sent = client.serving_endpoints.calls[0]["messages"]
    assert [m.content for m in sent] == ["sys", "usr"]


def test_usage_is_captured_when_present():
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    client = _FakeClient(reply_text="x", usage=usage)
    llm = DatabricksLlamaLLM(client=client)
    resp = llm.complete("q")
    assert resp.raw["usage"] is usage


def test_usage_is_none_when_api_does_not_expose_it():
    client = _FakeClient(reply_text="x", usage=None)
    llm = DatabricksLlamaLLM(client=client)
    resp = llm.complete("q")
    assert resp.raw["usage"] is None


def test_metadata_reports_configured_model_and_output_budget():
    client = _FakeClient()
    llm = DatabricksLlamaLLM(client=client, model_endpoint="custom-endpoint", max_output_tokens=99)
    assert llm.metadata.model_name == "custom-endpoint"
    assert llm.metadata.num_output == 99
    assert llm.metadata.is_chat_model is True


def test_streaming_is_explicitly_unsupported():
    client = _FakeClient()
    llm = DatabricksLlamaLLM(client=client)
    import pytest

    with pytest.raises(NotImplementedError):
        llm.stream_complete("q")
    with pytest.raises(NotImplementedError):
        llm.stream_chat([ChatMessage(role=MessageRole.USER, content="q")])

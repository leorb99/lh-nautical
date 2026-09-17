"""Databricks-hosted Llama LLM client, exposed through LlamaIndex's
``CustomLLM`` boundary. Introduced in Phase 5A; moved here from
``orchestration/`` during the Phase 5A.1 lean-core review — "how the model
is called" is a generation-layer concern, not an orchestration one (the
orchestration pipeline consumes this through the plain ``CustomLLM``
interface and has no knowledge of Databricks specifically).

Why not the official ``llama-index-llms-databricks`` package
--------------------------------------------------------------
That package (``llama_index.llms.databricks.Databricks``) is a thin subclass
of ``llama_index.llms.openai_like.OpenAILike`` — it authenticates with a
static bearer token (``api_key``, read from ``DATABRICKS_TOKEN`` if not
passed explicitly) against an OpenAI-compatible ``.../serving-endpoints``
route. This project's Databricks profile (``~/.databrickscfg``,
``lh-nautical``) uses ``auth_type = databricks-cli`` (browser/OAuth,
short-lived, U2M) — there is no long-lived personal access token in this
environment, and minting and refreshing one just to satisfy that package's
constructor would mean building a second, parallel auth/secret-handling path
next to the SDK's own (the Phase 5A task explicitly requires "no secret in
repository" and "use existing Databricks environment/profile mechanisms").

Instead, this adapter wraps ``WorkspaceClient.serving_endpoints.query()`` —
the exact call already smoke-tested directly against
``databricks-meta-llama-3-3-70b-instruct`` during Phase 5A preflight, and
the same authenticated client every other module in this repo uses
(``ai_search.py`` included). It still exposes the model through the
LlamaIndex ``LLM``/``CustomLLM`` abstraction, so the orchestration pipeline
depends only on that interface, never on this transport detail: the model
client is isolated here and nowhere else in the codebase makes a raw
serving-endpoint call.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage as SDKChatMessage
from databricks.sdk.service.serving import ChatMessageRole as SDKChatMessageRole
from llama_index.core.base.llms.types import (
    ChatMessage,
    ChatResponse,
    CompletionResponse,
    LLMMetadata,
    MessageRole,
)
from llama_index.core.llms import CustomLLM
from llama_index.core.llms.callbacks import llm_chat_callback, llm_completion_callback

__all__ = ["DatabricksLlamaLLM", "DEFAULT_MODEL_ENDPOINT"]

#: Confirmed READY in this workspace during Phase 5A preflight
#: (``databricks vector-search-indexes``/``serving-endpoints list``, not
#: inferred from public docs — see the Phase 5A report).
DEFAULT_MODEL_ENDPOINT = "databricks-meta-llama-3-3-70b-instruct"

#: Conservative, deterministic-leaning defaults for grounded RAG generation.
#: Centralized here rather than scattered across call sites (Phase 5A
#: Section 38). Confirmed-supported by the direct smoke-test call.
DEFAULT_MAX_TOKENS = 512
DEFAULT_TEMPERATURE = 0.0

_ROLE_TO_SDK = {
    MessageRole.SYSTEM: SDKChatMessageRole.SYSTEM,
    MessageRole.USER: SDKChatMessageRole.USER,
    MessageRole.ASSISTANT: SDKChatMessageRole.ASSISTANT,
}


class DatabricksLlamaLLM(CustomLLM):
    """LlamaIndex ``LLM`` backed by a Databricks Foundation Model API
    serving endpoint, via the SDK's own authenticated client."""

    model_endpoint: str = DEFAULT_MODEL_ENDPOINT
    max_output_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE

    def __init__(
        self,
        client: WorkspaceClient,
        model_endpoint: str = DEFAULT_MODEL_ENDPOINT,
        max_output_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model_endpoint=model_endpoint,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            **kwargs,
        )
        # Not a pydantic field: this is the injected, already-authenticated
        # client, never (re)constructed or persisted by this class.
        object.__setattr__(self, "_client", client)

    @classmethod
    def class_name(cls) -> str:
        return "DatabricksLlamaLLM"

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(
            model_name=self.model_endpoint,
            is_chat_model=True,
            num_output=self.max_output_tokens,
        )

    def _query(self, sdk_messages: Sequence[SDKChatMessage]) -> tuple[str, Any, float]:
        start = time.monotonic()
        response = self._client.serving_endpoints.query(
            name=self.model_endpoint,
            messages=list(sdk_messages),
            max_tokens=self.max_output_tokens,
            temperature=self.temperature,
        )
        latency_s = time.monotonic() - start
        text = response.choices[0].message.content if response.choices else ""
        return text, response.usage, latency_s

    @llm_completion_callback()
    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        text, usage, latency_s = self._query([SDKChatMessage(role=SDKChatMessageRole.USER, content=prompt)])
        return CompletionResponse(text=text, raw={"usage": usage, "latency_s": latency_s})

    @llm_chat_callback()
    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        sdk_messages = [
            SDKChatMessage(role=_ROLE_TO_SDK.get(m.role, SDKChatMessageRole.USER), content=m.content or "")
            for m in messages
        ]
        text, usage, latency_s = self._query(sdk_messages)
        return ChatResponse(
            message=ChatMessage(role=MessageRole.ASSISTANT, content=text),
            raw={"usage": usage, "latency_s": latency_s},
        )

    # CustomLLM requires stream_complete/stream_chat to exist; Phase 5A does
    # not use streaming, so these deliberately raise rather than silently
    # behaving like the non-streaming call under a misleading name.
    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any):
        raise NotImplementedError("Streaming is out of scope for Phase 5A.")

    def stream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any):
        raise NotImplementedError("Streaming is out of scope for Phase 5A.")

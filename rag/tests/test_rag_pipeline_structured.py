"""Unit tests for the Phase 6B.1 additions to src.orchestration.rag_pipeline:
the explanation-only prompt and ``generate(values_shown_separately=True)``,
the documentary half of a BOTH answer. The model never receives structured
values — they are rendered deterministically elsewhere — so it cannot
rewrite them. Only the AI Search network call and the LLM are faked (same
pattern as test_rag_pipeline.py); nothing touches Databricks.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from llama_index.core.base.llms.types import CompletionResponse, LLMMetadata
from llama_index.core.llms import CustomLLM

from src.orchestration.rag_pipeline import (
    EXPLANATION_PROMPT_TEMPLATE,
    GROUNDING_PROMPT_TEMPLATE,
    RagOrchestrator,
)
from src.retrieval.ai_search import RetrievedChunk



def _chunk(rank=1, chunk_id="c1", **overrides) -> RetrievedChunk:
    defaults = dict(
        rank=rank,
        chunk_id=chunk_id,
        document_id="doc-1",
        document_title="Title",
        canonical_domain="domain",
        section_path="A > B",
        section_title="B",
        chunk_text="documentary content about the ranking rule",
        rule_ids=(),
        score=0.9,
    )
    defaults.update(overrides)
    return RetrievedChunk(**defaults)


class RecordingLLM(CustomLLM):
    model_endpoint: str = "fake-model"
    prompts: list = []
    reply: str = "fake answer [S1]"
    usage: object = None

    @property
    def metadata(self):
        return LLMMetadata(model_name="fake-model", is_chat_model=True)

    def complete(self, prompt: str, formatted: bool = False, **kwargs):
        self.prompts.append(prompt)
        return CompletionResponse(text=self.reply, raw={"usage": self.usage})

    def stream_complete(self, prompt, formatted=False, **kwargs):
        raise NotImplementedError

    def chat(self, messages, **kwargs):
        raise NotImplementedError

    def stream_chat(self, messages, **kwargs):
        raise NotImplementedError


def _orchestrator(monkeypatch, chunks, llm=None):
    llm = llm or RecordingLLM(prompts=[])
    search_calls = []

    def fake_query_index(client, index_name, query_text, **kwargs):
        search_calls.append(query_text)
        return chunks

    monkeypatch.setattr("src.orchestration.rag_pipeline.query_index", fake_query_index)
    return RagOrchestrator(client=MagicMock(), index_name="idx", llm=llm), llm, search_calls


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template", [GROUNDING_PROMPT_TEMPLATE, EXPLANATION_PROMPT_TEMPLATE])
def test_every_template_carries_the_answer_style_rules(template):
    prompt = template.format(context_str="[S1]\nx", query_str="Q?").lower()

    assert "same language as the question" in prompt
    assert "start with the direct answer" in prompt
    assert "do not open with a preamble" in prompt
    assert "be concise" in prompt
    assert "do not include code, dax or sql unless the question explicitly asks" in prompt
    assert "Q?".lower() in prompt


def test_documentary_template_keeps_its_grounding_and_citation_rules():
    prompt = GROUNDING_PROMPT_TEMPLATE.format(context_str="[S1]\nx", query_str="Q?").lower()

    assert "do not invent" in prompt
    assert "cite the source label" in prompt
    assert "structured data" not in prompt


def test_explanation_template_asks_only_for_the_explanation_and_forbids_restating_values():
    prompt = EXPLANATION_PROMPT_TEMPLATE.format(context_str="[S1]\nx", query_str="Q?")

    assert "[S1]\nx" in prompt and "Question: Q?" in prompt
    assert "Write ONLY the explanation" in prompt
    assert "Do not list, restate, estimate or invent any ranking, product name, rank or quantity" in prompt
    assert "you do not have them" in prompt
    assert "cite the source label" in prompt  # documentary claims still carry [Sn]
    assert "Structured data:" not in prompt  # the template has no slot for structured values


def test_templates_are_brace_safe_for_arbitrary_evidence_text():
    prompt = EXPLANATION_PROMPT_TEMPLATE.format(context_str="Rule {top_n} {0} {{x}}", query_str="Q {?}")

    assert "Rule {top_n} {0} {{x}}" in prompt
    assert "Q {?}" in prompt


# ---------------------------------------------------------------------------
# generate() — documentary-only behavior is unchanged
# ---------------------------------------------------------------------------


def test_generate_by_default_uses_the_documentary_prompt(monkeypatch):
    orch, llm, search_calls = _orchestrator(monkeypatch, [_chunk()])

    result = orch.generate("Como o ranking é calculado?")

    assert len(llm.prompts) == 1
    assert "Evidence:" in llm.prompts[0]
    assert "Write ONLY the explanation" not in llm.prompts[0]
    assert search_calls == ["Como o ranking é calculado?"]
    assert result.evidence_available is True
    assert [s["source_label"] for s in result.sources] == ["S1"]


def test_documentary_only_empty_retrieval_guard_still_makes_no_model_call(monkeypatch):
    orch, llm, _ = _orchestrator(monkeypatch, [])

    result = orch.generate("GOLD-999")

    assert result.answer is None and result.evidence_available is False and result.sources == []
    assert llm.prompts == []


# ---------------------------------------------------------------------------
# generate(values_shown_separately=True) — the documentary half of BOTH
# ---------------------------------------------------------------------------


def test_explanation_generation_uses_the_explanation_prompt_in_one_model_call(monkeypatch):
    orch, llm, _ = _orchestrator(monkeypatch, [_chunk(1, "c1"), _chunk(2, "c2")])

    result = orch.generate("Quais são os mais vendidos e qual a regra?", values_shown_separately=True)

    assert len(llm.prompts) == 1
    prompt = llm.prompts[0]
    assert "Write ONLY the explanation" in prompt
    assert "[S1]" in prompt and "[S2]" in prompt
    assert "documentary content about the ranking rule" in prompt
    assert result.answer == "fake answer [S1]"
    assert result.evidence_available is True


def test_the_model_is_never_given_structured_values_in_the_explanation_call(monkeypatch):
    orch, llm, _ = _orchestrator(monkeypatch, [_chunk()])

    orch.generate("Quais são os mais vendidos e qual a regra?", values_shown_separately=True)

    prompt = llm.prompts[0]
    for value in ("Colete Salva-Vidas 2374", "1737", "Product ID", "Quantity sold", "STRUCTURED DATA"):
        assert value not in prompt


def test_natural_language_query_reaches_retrieval_unchanged_for_the_explanation_call(monkeypatch):
    orch, _, search_calls = _orchestrator(monkeypatch, [_chunk()])
    query = "Quais são os produtos mais vendidos e qual regra define esse ranking?"

    orch.generate(query, values_shown_separately=True)

    assert search_calls == [query]  # no instructions/evidence prepended to the retrieval query


def test_explanation_sources_are_documentary_only(monkeypatch):
    orch, _, _ = _orchestrator(monkeypatch, [_chunk(1, "c1"), _chunk(2, "c2")])

    result = orch.generate("q", values_shown_separately=True)

    assert [s["chunk_id"] for s in result.sources] == ["c1", "c2"]
    assert all("Colete" not in str(s) and "1737" not in str(s) for s in result.sources)


def test_explanation_call_with_no_documentary_evidence_makes_no_model_call(monkeypatch):
    orch, llm, _ = _orchestrator(monkeypatch, [])

    result = orch.generate("q", values_shown_separately=True)

    assert result.answer is None and result.evidence_available is False and result.sources == []
    assert llm.prompts == []  # no fake explanation is ever generated


def test_usage_is_extracted_when_present(monkeypatch):
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    orch, _, _ = _orchestrator(monkeypatch, [_chunk()], llm=RecordingLLM(prompts=[], usage=usage))

    result = orch.generate("q", values_shown_separately=True)

    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

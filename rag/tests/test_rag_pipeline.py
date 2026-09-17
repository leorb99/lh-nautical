"""Unit tests for src.orchestration.rag_pipeline — evidence translation,
the LlamaIndex retriever adapter, context/prompt formatting, and the
orchestrator, consolidated into one file mirroring the merged production
module (Phase 5A.1 lean-core review). All Databricks/network calls are
injected fakes — nothing here hits the network."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

from llama_index.core.base.llms.types import CompletionResponse
from llama_index.core.llms import CustomLLM

from src.orchestration.rag_pipeline import (
    GROUNDING_PROMPT_TEMPLATE,
    OrchestrationResult,
    PolicyRetriever,
    RagOrchestrator,
    RetrievedEvidence,
    build_context_blocks,
    chunks_to_evidence_and_nodes,
    evidence_to_node,
    to_retrieved_evidence,
)
from src.retrieval.ai_search import RetrievedChunk
from src.retrieval.policy import DEFAULT_POLICY_CONFIG, classify_query


def _chunk(rank=1, chunk_id="c1", rule_ids=(), **overrides) -> RetrievedChunk:
    defaults = dict(
        rank=rank,
        chunk_id=chunk_id,
        document_id="doc-1",
        document_title="Title",
        canonical_domain="domain",
        section_path="A > B",
        section_title="B",
        chunk_text="some text",
        rule_ids=rule_ids,
        score=0.9,
    )
    defaults.update(overrides)
    return RetrievedChunk(**defaults)


def _evidence(label, rule_ids=(), section_path="Sec > Path", section_title="Path", text="content here"):
    return RetrievedEvidence(
        source_label=label,
        chunk_id=f"chunk-{label}",
        text=text,
        document_id=f"doc-{label}",
        document_title="Title",
        canonical_domain="dom",
        section_path=section_path,
        section_title=section_title,
        rule_ids=rule_ids,
        score=0.9,
        rank=1,
    )


# ---------------------------------------------------------------------------
# Evidence translation
# ---------------------------------------------------------------------------


def test_source_labels_are_assigned_in_retrieval_order():
    chunks = [_chunk(1, "c1"), _chunk(2, "c2"), _chunk(3, "c3")]
    evidence = to_retrieved_evidence(chunks)
    assert [e.source_label for e in evidence] == ["S1", "S2", "S3"]


def test_source_labels_follow_input_order_not_rank_value():
    chunks = [_chunk(5, "c5"), _chunk(1, "c1")]
    evidence = to_retrieved_evidence(chunks)
    assert [e.source_label for e in evidence] == ["S1", "S2"]
    assert [e.rank for e in evidence] == [5, 1]


def test_no_metadata_field_is_lost_in_translation():
    chunk = _chunk(
        1,
        "c1",
        rule_ids=("GOLD-001", "GOLD-002"),
        document_id="doc-x",
        document_title="Doc X",
        canonical_domain="dom-x",
        section_path="Sec > Path",
        section_title="Path",
        chunk_text="the actual chunk content",
        score=0.5,
    )
    evidence, nodes = chunks_to_evidence_and_nodes([chunk])
    e = evidence[0]
    assert e.chunk_id == "c1"
    assert e.document_id == "doc-x"
    assert e.rule_ids == ("GOLD-001", "GOLD-002")
    assert e.text == "the actual chunk content"
    assert e.score == 0.5

    node = nodes[0]
    assert node.node.text == "the actual chunk content"
    assert node.node.id_ == "c1"
    assert node.score == 0.5
    md = node.node.metadata
    assert md["document_id"] == "doc-x"
    assert md["rule_ids"] == ["GOLD-001", "GOLD-002"]
    assert md["source_label"] == "S1"


def test_empty_rule_ids_and_none_optional_fields_do_not_crash_translation():
    chunk = _chunk(1, "c1", rule_ids=(), document_title=None, canonical_domain=None, section_path=None, section_title=None)
    evidence, nodes = chunks_to_evidence_and_nodes([chunk])
    assert evidence[0].rule_ids == ()
    assert evidence[0].section_path is None
    assert nodes[0].node.metadata["rule_ids"] == []


def test_empty_chunk_list_produces_empty_evidence_and_nodes():
    evidence, nodes = chunks_to_evidence_and_nodes([])
    assert evidence == []
    assert nodes == []


def test_evidence_to_node_is_consistent_with_batch_translation():
    chunk = _chunk(2, "c2")
    single = to_retrieved_evidence([chunk])[0]
    node_via_helper = evidence_to_node(single)
    _, nodes_via_batch = chunks_to_evidence_and_nodes([chunk])
    assert node_via_helper.node.metadata == nodes_via_batch[0].node.metadata


# ---------------------------------------------------------------------------
# Context / prompt formatting
# ---------------------------------------------------------------------------


def test_context_blocks_are_labeled_in_input_order():
    blocks = build_context_blocks([_evidence("S1"), _evidence("S2")])
    assert blocks.index("[S1]") < blocks.index("[S2]")


def test_context_block_contains_required_fields():
    ev = _evidence("S1", rule_ids=("GOLD-032",), section_path="A > B", text="the chunk body")
    blocks = build_context_blocks([ev])
    assert "[S1]" in blocks
    assert "document_id: doc-S1" in blocks
    assert "section: A > B" in blocks
    assert "rule_ids: GOLD-032" in blocks
    assert "content:\nthe chunk body" in blocks


def test_context_block_falls_back_to_section_title_when_section_path_is_missing():
    ev = _evidence("S1", rule_ids=(), section_path=None)
    blocks = build_context_blocks([ev])
    assert "rule_ids: none" in blocks
    assert "section: Path" in blocks  # falls back to section_title, not "none"


def test_context_block_shows_none_when_neither_section_path_nor_title_present():
    ev = _evidence("S1", rule_ids=(), section_path=None, section_title=None)
    blocks = build_context_blocks([ev])
    assert "section: none" in blocks


def test_empty_evidence_list_produces_empty_context():
    assert build_context_blocks([]) == ""


def test_only_evidence_fields_are_injected_no_other_repo_metadata():
    blocks = build_context_blocks([_evidence("S1")])
    assert "dom" not in blocks
    assert "Title" not in blocks


def test_prompt_template_formats_with_context_and_query():
    prompt = GROUNDING_PROMPT_TEMPLATE.format(context_str="[S1]\ncontent", query_str="What is X?")
    assert "[S1]" in prompt
    assert "What is X?" in prompt
    assert "cite the source label" in prompt.lower()
    assert "do not invent" in prompt.lower()


# ---------------------------------------------------------------------------
# PolicyRetriever — the LlamaIndex BaseRetriever seam
# ---------------------------------------------------------------------------


def test_retriever_passes_decision_top_k_query_type_and_filter_through_unchanged():
    decision = classify_query("GOLD-032", DEFAULT_POLICY_CONFIG)
    captured = {}

    def fake_query_index(client, index_name, query_text, *, num_results, query_type, filters_json):
        captured.update(num_results=num_results, query_type=query_type, filters_json=filters_json, query_text=query_text)
        return [_chunk()]

    retriever = PolicyRetriever(
        client=MagicMock(), index_name="my_index", decision=decision, query_text="GOLD-032", query_index_fn=fake_query_index
    )
    retriever.retrieve("GOLD-032")

    assert captured["num_results"] == decision.top_k == 5
    assert captured["query_type"] == decision.query_type == "HYBRID"
    assert captured["filters_json"] == decision.filters_json
    assert captured["query_text"] == "GOLD-032"


def test_retriever_ignores_query_bundle_text_and_uses_the_bound_query_text():
    decision = classify_query("Como o Power BI calcula a medida Ticket Médio?", DEFAULT_POLICY_CONFIG)
    captured = {}

    def fake_query_index(client, index_name, query_text, **kwargs):
        captured["query_text"] = query_text
        return []

    retriever = PolicyRetriever(
        client=MagicMock(),
        index_name="idx",
        decision=decision,
        query_text="Como o Power BI calcula a medida Ticket Médio?",
        query_index_fn=fake_query_index,
    )
    retriever.retrieve("a completely different string")
    assert captured["query_text"] == "Como o Power BI calcula a medida Ticket Médio?"


def test_normal_route_top_k_is_eight_by_default():
    decision = classify_query("uma pergunta qualquer sem cues", DEFAULT_POLICY_CONFIG)
    assert decision.top_k == DEFAULT_POLICY_CONFIG.normal_top_k == 8


def test_cross_source_route_top_k_is_ten_by_default():
    decision = classify_query("Como o Ticket Médio é definido no Gold e implementado no Power BI?", DEFAULT_POLICY_CONFIG)
    assert decision.top_k == DEFAULT_POLICY_CONFIG.cross_source_top_k == 10


def test_empty_ai_search_result_produces_empty_nodes_and_empty_last_evidence():
    decision = classify_query("GOLD-999", DEFAULT_POLICY_CONFIG)

    def fake_query_index(client, index_name, query_text, **kwargs):
        return []

    retriever = PolicyRetriever(
        client=MagicMock(), index_name="idx", decision=decision, query_text="GOLD-999", query_index_fn=fake_query_index
    )
    nodes = retriever.retrieve("GOLD-999")
    assert nodes == []
    assert retriever.last_evidence == []


def test_last_evidence_labels_match_node_order_deterministically():
    decision = classify_query("uma pergunta qualquer", DEFAULT_POLICY_CONFIG)

    def fake_query_index(client, index_name, query_text, **kwargs):
        return [_chunk(rank=1, chunk_id="a"), _chunk(rank=2, chunk_id="b"), _chunk(rank=3, chunk_id="c")]

    retriever = PolicyRetriever(
        client=MagicMock(), index_name="idx", decision=decision, query_text="q", query_index_fn=fake_query_index
    )
    retriever.retrieve("q")
    assert [e.source_label for e in retriever.last_evidence] == ["S1", "S2", "S3"]
    assert [e.chunk_id for e in retriever.last_evidence] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# RagOrchestrator
# ---------------------------------------------------------------------------


class _FakeLLM(CustomLLM):
    context_window: int = 4096
    num_output: int = 256
    model_name: str = "fake-model"
    model_endpoint: str = "fake-model"

    @property
    def metadata(self):
        from llama_index.core.base.llms.types import LLMMetadata

        return LLMMetadata(model_name="fake-model", is_chat_model=True)

    def complete(self, prompt: str, formatted: bool = False, **kwargs):
        return CompletionResponse(text="fake grounded answer [S1]", raw={"usage": None})

    def stream_complete(self, prompt, formatted=False, **kwargs):
        raise NotImplementedError

    def chat(self, messages, **kwargs):
        raise NotImplementedError

    def stream_chat(self, messages, **kwargs):
        raise NotImplementedError


def _make_orchestrator(monkeypatch, chunks):
    def fake_query_index(client, index_name, query_text, **kwargs):
        return chunks

    monkeypatch.setattr("src.orchestration.rag_pipeline.query_index", fake_query_index)
    return RagOrchestrator(client=MagicMock(), index_name="idx", llm=_FakeLLM())


def test_generate_returns_grounded_answer_and_deterministic_sources(monkeypatch):
    orch = _make_orchestrator(monkeypatch, [_chunk(1, "c1"), _chunk(2, "c2")])
    result = orch.generate("uma pergunta normal")
    assert result.evidence_available is True
    assert result.answer == "fake grounded answer [S1]"
    assert [s["source_label"] for s in result.sources] == ["S1", "S2"]
    assert result.retrieval_policy_decision.route.value == "NORMAL"
    assert result.generation_latency_s is not None


def test_empty_retrieval_skips_generation_entirely(monkeypatch):
    orch = _make_orchestrator(monkeypatch, [])
    result = orch.generate("GOLD-999")
    assert result.evidence_available is False
    assert result.answer is None
    assert result.sources == []
    assert result.generation_latency_s is None
    assert result.retrieval_policy_decision.route.value == "EXACT_RULE_ID"


def test_exact_rule_id_route_and_filter_are_not_overridden_by_orchestrator(monkeypatch):
    orch = _make_orchestrator(monkeypatch, [_chunk()])
    result = orch.generate("GOLD-032")
    d = result.retrieval_policy_decision
    assert d.route.value == "EXACT_RULE_ID"
    assert d.top_k == 5
    assert d.filters_json == '{"rule_ids": "GOLD-032"}'


def test_cross_source_route_is_not_overridden_by_orchestrator(monkeypatch):
    orch = _make_orchestrator(monkeypatch, [_chunk()])
    result = orch.generate("Como o Ticket Médio é definido no Gold e implementado no Power BI?")
    assert result.retrieval_policy_decision.route.value == "CROSS_SOURCE"
    assert result.retrieval_policy_decision.top_k == 10


def test_caller_supplied_retrieval_decision_is_used_instead_of_reclassifying(monkeypatch):
    orch = _make_orchestrator(monkeypatch, [_chunk()])
    forced_decision = classify_query("GOLD-032", DEFAULT_POLICY_CONFIG)
    result = orch.generate("uma pergunta qualquer", retrieval_decision=forced_decision)
    assert result.retrieval_policy_decision is forced_decision


def test_malformed_empty_document_id_does_not_crash_orchestration(monkeypatch):
    chunk = RetrievedChunk(
        rank=1,
        chunk_id="c1",
        document_id="",
        document_title=None,
        canonical_domain=None,
        section_path=None,
        section_title=None,
        chunk_text="",
        rule_ids=(),
        score=0.0,
    )
    orch = _make_orchestrator(monkeypatch, [chunk])
    result = orch.generate("uma pergunta qualquer")
    assert result.evidence_available is True
    assert result.sources[0]["document_id"] == ""


# ---------------------------------------------------------------------------
# No oracle leakage — mirrors test_policy.py / test_query_routing.py
# ---------------------------------------------------------------------------


def test_module_never_imports_evaluate_retrieval():
    path = Path(__file__).resolve().parents[1] / "src" / "orchestration" / "rag_pipeline.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    assert not any("evaluate_retrieval" in n or n.endswith("src.evaluation") for n in names)


def test_module_never_references_evaluate_retrieval_or_expected_evidence_as_text():
    path = Path(__file__).resolve().parents[1] / "src" / "orchestration" / "rag_pipeline.py"
    text = path.read_text(encoding="utf-8")
    assert "evaluate_retrieval" not in text
    assert "expected_evidence" not in text


def test_orchestrator_generate_signature_has_no_expected_evidence_parameter():
    sig = inspect.signature(RagOrchestrator.generate)
    assert "expected_evidence" not in sig.parameters

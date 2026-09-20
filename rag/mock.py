"""Backend de mentira, só para desenvolver e testar o frontend localmente.

NÃO é o app.py de produção — o real constrói WorkspaceClient/Harness/
RagOrchestrator na importação e exige RAG_CATALOG configurado, então não dá
pra rodar ele local sem a stack toda do Databricks. Este arquivo serve os
MESMOS arquivos estáticos e devolve respostas no MESMO formato do
API_CONTRACT.md, escolhidas por palavras-gatilho na pergunta — inclusive
casos difíceis de forçar no backend real, como citation_integrity=INVALID.

Rodar:
    pip install fastapi uvicorn --break-system-packages
    python mock.py
    # abrir http://127.0.0.1:8000

Gatilhos (não fazem parte do contrato real — são só desta simulação):
    (vazio)                  -> INVALID_REQUEST
    contém "erro"/"error"    -> RAG_EXECUTION_FAILED
    contém "vazio"/"nada"    -> AI_SEARCH_NO_EVIDENCE
    contém "invalido"        -> AI_SEARCH_OK com citation_integrity INVALID
                                 (cita um S9 que não existe em sources[])
    qualquer outra coisa     -> AI_SEARCH_OK "feliz", 2 fontes, VALID
"""

import time

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI()


class AskRequest(BaseModel):
    query: str


@app.post("/api/ask")
def ask(request: AskRequest):
    query = request.query.strip().lower()

    if not query:
        return {
            "status": "INVALID_REQUEST",
            "answer": None,
            "sources": [],
            "citation_integrity": None,
            "message": "Please provide a non-empty question.",
        }

    time.sleep(2)  # simula a latência real (~11s no contrato) só o suficiente pra testar loading/cancelar

    if "erro" in query or "error" in query:
        return {
            "status": "RAG_EXECUTION_FAILED",
            "answer": None,
            "sources": [],
            "citation_integrity": None,
            "message": "Something went wrong processing this request.",
        }

    if "vazio" in query or "nada" in query:
        return {
            "status": "AI_SEARCH_NO_EVIDENCE",
            "answer": None,
            "sources": [],
            "citation_integrity": None,
            "message": "No relevant information was found for this question.",
        }

    if "invalido" in query or "invalida" in query:
        return {
            "status": "AI_SEARCH_OK",
            "answer": "O prazo de entrega padrão é de 5 dias úteis [S1, S9].",
            "sources": [
                {
                    "source_label": "S1",
                    "chunk_id": "c-001",
                    "document_id": "doc-logistica",
                    "document_title": "Política de Entregas",
                    "section_path": "Logística > Prazos",
                    "section_title": "Prazo padrão",
                    "rule_ids": ["GOLD-011"],
                    "rank": 1,
                    "score": 0.91,
                }
            ],
            "citation_integrity": {
                "status": "INVALID",
                "cited_labels": ["S1", "S9"],
                "valid_labels": ["S1"],
                "unknown_labels": ["S9"],
            },
            "message": None,
        }

    return {
        "status": "AI_SEARCH_OK",
        "answer": (
            f'Sobre "{request.query}": GOLD-032 define um pedido de compra em aberto '
            'como aquele com status "sent" ou "partially_received" [S1, S2]. Essa '
            "regra aparece no dashboard gerencial [S1]."
        ),
        "sources": [
            {
                "source_label": "S1",
                "chunk_id": "c-101",
                "document_id": "doc-compass360",
                "document_title": "Regras Semânticas do Power BI — Dashboard Gerencial Compass 360 (LH Nautical)",
                "section_path": "Regras de Negócio",
                "section_title": "Pedidos em aberto",
                "rule_ids": ["GOLD-032"],
                "rank": 1,
                "score": 0.94,
            },
            {
                "source_label": "S2",
                "chunk_id": "c-102",
                "document_id": "doc-compass360",
                "document_title": "Regras Semânticas do Power BI — Dashboard Gerencial Compass 360 (LH Nautical)",
                "section_path": "Regras de Negócio",
                "section_title": "Status de pedido",
                "rule_ids": ["GOLD-032"],
                "rank": 2,
                "score": 0.88,
            },
        ],
        "citation_integrity": {
            "status": "VALID",
            "cited_labels": ["S1", "S2"],
            "valid_labels": ["S1", "S2"],
            "unknown_labels": [],
        },
        "message": None,
    }


app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)

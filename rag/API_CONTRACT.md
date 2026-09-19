# RAG API — Contract for Leonardo (Phase 4B / Phase 8)

Backend only. This document describes the HTTP contract the UI is built
against — it does not describe RAG internals, and none of that is needed to
consume the API. Everything marked **LIVE VALIDATED** below was exercised
against the real deployed App, not inferred from code alone.

## UI Developer Quick Start

```text
Base URL:    https://rag-api-7474653394852199.aws.databricksapps.com
Method:      POST
Path:        /api/ask
Content-Type: application/json

Request:     {"query": "..."}
Response:    one complete JSON object, non-streaming
```

**V1 exposes one backend endpoint only: `POST /api/ask`.** There is nothing
else to integrate against — no separate endpoints for sources, citations,
history, or status.

Before building against this live app for real, read "Authentication /
Integration" below — there is an open decision there that affects whether
a browser-based frontend running outside Databricks can call this URL
directly.

## DEV App URL

```
https://rag-api-7474653394852199.aws.databricksapps.com
```

(This is the `dev` target only. `ci`/`prod` are not deployed.)

## Endpoint

```
POST /api/ask
Content-Type: application/json
```

### Request contract (V1, frozen)

```json
{
  "query": "Como o Power BI calcula o Ticket Médio?"
}
```

That is the **entire** request contract — one field, `query`, a plain
string. Confirmed directly from `rag/app.py`'s `AskRequest` model (`query:
str`, nothing else). The API does **not** accept, and the frontend must
**not** send, any of:

```text
source_route          — not accepted; V1 always resolves to AI_SEARCH internally
top_k                 — not accepted; retrieval Top-K is decided server-side
filters                — not accepted; not exposed to callers
model                  — not accepted; generation model is fixed server-side
temperature             — not accepted
history / conversation_id — not accepted; every call is independent (see below)
```

There is no routing/model/retrieval parameter to send today or in the
future: Structured Data / combined-source answers are implemented
(locally — see "Structured answers" below) behind this same
`{"query": "..."}` contract, not by adding new request fields for the UI to
manage. The backend decides internally which source a question needs. The
frontend does not, and should not try to, control retrieval internals.

### Response schema (all fields, from `rag/app.py`'s `AskResponse` model)

```text
status               string                          always present
answer                string | null                   present only for AI_SEARCH_OK
sources               array of source objects (below)  [] when there is no documentary source
                                                       (no answer, or an answer built only from
                                                       structured data — see "Structured answers")
citation_integrity     object | null (below)            present only for AI_SEARCH_OK
message                string | null                    present for non-OK statuses
```

## Live-Validated API Behaviors

Three of the four reachable V1 statuses have been exercised end-to-end
against the real deployed App (`app_status=RUNNING`, real Unity Catalog
grants, real AI Search index, real Llama generation, real MLflow trace
per request). The fourth (`RAG_EXECUTION_FAILED`) is documented from the
implemented code contract, not from a live-forced failure.

### A. Successful answer — `AI_SEARCH_OK` — **LIVE VALIDATED**

Real query: `{"query": "GOLD-032"}`. Real HTTP 200. Example below is the
**actual live response**, truncated to 2 of the 5 returned sources for
readability — the real response returns all 5.

```json
{
  "status": "AI_SEARCH_OK",
  "answer": "GOLD-032 refere-se à definição de um pedido de compra em aberto, que é considerado como tal quando seu status é \"sent\" ou \"partially_received\" [S1, S4]. Esta regra é mencionada em vários documentos, incluindo `powerbi_semantic_rules` [S1, S2], `inventory_and_stockout` [S3, S5] e `gold_business_rules` [S4]. ...",
  "sources": [
    {
      "source_label": "S1",
      "chunk_id": "d211a227a355a8ba9df23a0c57fc613ee57cd55f37497a8245e792491d108e49",
      "document_id": "powerbi_semantic_rules",
      "document_title": "Regras Semânticas do Power BI — Dashboard Gerencial Compass 360 (LH Nautical)",
      "section_path": "Medidas DAX > Estoque & Supply > `Pedidos Abertos`",
      "section_title": "`Pedidos Abertos`",
      "rule_ids": ["GOLD-032", "GOLD-029"],
      "rank": 1,
      "score": 0.9765625
    },
    {
      "source_label": "S4",
      "chunk_id": "0daf65a73deb3aec53a0fc33799449e48d1eecdf2ba79ae83e7c504c49239530",
      "document_id": "gold_business_rules",
      "document_title": "Regras de Negócio da Camada Gold",
      "section_path": "Fornecedores > Definição de pedido de compra em aberto",
      "section_title": "Definição de pedido de compra em aberto",
      "rule_ids": ["GOLD-032"],
      "rank": 4,
      "score": 0.961166253101737
    }
  ],
  "citation_integrity": {
    "status": "VALID",
    "cited_labels": ["S1", "S4", "S2", "S3", "S5"],
    "valid_labels": ["S1", "S4", "S2", "S3", "S5"],
    "unknown_labels": []
  },
  "message": null
}
```
`(... truncated for documentation — the real response includes S1–S5.)`

### B. No evidence — `AI_SEARCH_NO_EVIDENCE` — **LIVE VALIDATED**

Real query: `{"query": "GOLD-999"}` — a syntactically valid but
non-existent Rule ID. Real HTTP 200, exact real response body:

```json
{
  "status": "AI_SEARCH_NO_EVIDENCE",
  "answer": null,
  "sources": [],
  "citation_integrity": null,
  "message": "No relevant information was found for this question."
}
```

This is a **normal, healthy** outcome — not an error. `HTTP 200` on
purpose. Show `message` to the user; don't treat it as a failure state.
No answer was fabricated for a non-existent identifier.

### C. Invalid request — `INVALID_REQUEST` — **LIVE VALIDATED**

Real query: `{"query": "   "}` (whitespace-only). Real HTTP 400, exact
real response body:

```json
{
  "status": "INVALID_REQUEST",
  "answer": null,
  "sources": [],
  "citation_integrity": null,
  "message": "Please provide a non-empty question."
}
```

### D. Execution failure — `RAG_EXECUTION_FAILED` (HTTP 500) — from code, not live-forced

```json
{
  "status": "RAG_EXECUTION_FAILED",
  "answer": null,
  "sources": [],
  "citation_integrity": null,
  "message": "Something went wrong processing this request."
}
```

No internal exception text or stack trace is ever included in this or any
other response — only in server-side logs (confirmed in `rag/app.py`:
`result.error` is logged via `logger.error(...)`, never serialized into
the response body).

## Error/status matrix

| API `status` | HTTP | Meaning | Recommended UI behavior |
|---|---:|---|---|
| `AI_SEARCH_OK` | 200 | An answer was produced. Documentary answers carry `sources` and a structural citation check; an answer built only from structured data (see "Structured answers") has `sources: []` and `citation_integrity.status == "NO_CITATIONS"` | Render `answer` (preserve line breaks); render `sources` and citation UX only when `sources` is non-empty (see below) |
| `AI_SEARCH_NO_EVIDENCE` | 200 | Retrieval found nothing; no model call was made | Render `message` as a neutral "no answer found" state — **not** an error |
| `INVALID_REQUEST` | 400 | `query` was empty/whitespace-only | Render `message`; ideally validate client-side too, but still handle this response |
| `RAG_EXECUTION_FAILED` | 500 | An unhandled failure occurred somewhere in retrieval/generation | Render a generic failure/retry message; never surface `message` as if it were a stack trace (it isn't one, but don't build UI that would leak more if the contract ever changes) |

### Internal statuses (never returned)

These are real `HarnessStatus` values in the backend's internal enum
(`rag/src/harness/harness.py`). **None of them is ever returned by the
API** — the backend maps every one of them onto one of the four public
statuses above, and never accepts a route from the request. **Do not build
UI for these**:

```text
STRUCTURED_DATA_OK            -> AI_SEARCH_OK          (200, sources [])
BOTH_OK                       -> AI_SEARCH_OK          (200, documentary sources)
BOTH_DEGRADED                 -> AI_SEARCH_OK          (200, sources [])
STRUCTURED_DATA_NO_DATA       -> AI_SEARCH_NO_EVIDENCE (200)
STRUCTURED_DATA_UNSUPPORTED   -> AI_SEARCH_NO_EVIDENCE (200, message "This question asks for data that is not supported yet.")
STRUCTURED_DATA_FAILED        -> RAG_EXECUTION_FAILED  (500)
STRUCTURED_DATA_UNAVAILABLE   -> RAG_EXECUTION_FAILED  (500)
STRUCTURED_DATA_FETCHED_NO_GENERATION, BOTH_PARTIAL (legacy placeholders)
                              -> RAG_EXECUTION_FAILED  (500)
```

The public `status` is never a raw internal value.

## Structured answers (implemented locally; NOT LIVE until Phase 6B.2)

The backend can answer one kind of live-data question — the ranking of the
best-selling products by quantity (effective sales, all history, all
locations) — behind the unchanged `POST /api/ask`. **This is implemented in
the repository and unit-tested locally, but is not live**: the currently
deployed App does not contain the Phase 6B.1 routing. If this code were
deployed before the Phase 6B.2 infrastructure configuration (SQL Warehouse,
`SQL_WAREHOUSE_ID`/`DATA_*` settings and service-principal grants), a
structured-routed request would return the controlled `RAG_EXECUTION_FAILED`
(HTTP 500) response because the structured service would be unavailable —
never a documentation answer. Nothing below has been observed against the
live App.

**No frontend change is required.** Request schema, response schema and the
four public statuses are unchanged. What the UI should be aware of:

- A successful structured answer currently comes back as
  `status: "AI_SEARCH_OK"` (V1 compatibility; a dedicated status is possible
  later) with a non-null `answer`, **`sources: []`** and
  `citation_integrity.status: "NO_CITATIONS"`. `AI_SEARCH_OK` can therefore
  have an empty `sources` list: render the `answer` and simply omit the
  sources section. Values from live data are **never** given a `[Sn]`
  citation and never appear in `sources`.
- The ranked list is produced deterministically by the backend (not by the
  model), one line per product, in plain text with line breaks, for example:

  ```text
  Os produtos mais vendidos, por quantidade de unidades, são:

  1. <product name> — 1.737 unidades
  2. <product name> — 1.731 unidades
  8. <product name> — 1.638 unidades
  8. <product name> — 1.638 unidades

  Produtos com a mesma quantidade vendida compartilham a mesma posição.
  Fonte: dados de vendas efetivas.
  ```

  (The example shows the format only — the product names and quantities
  shown here are placeholders/illustrative, not live data.) Tied products
  repeat their rank, so a list can be longer than the requested count.
- A question that asks for both the values and the rule behind them returns
  the ranking followed by a documentary explanation; only the explanation
  carries `[Sn]` citations and `sources`. If the explanation is unavailable
  (no documentary evidence, or a technical failure), the ranking is still
  returned as `AI_SEARCH_OK` with `sources: []` and no explanation.
- A question that asks for data the backend cannot produce (other periods,
  stores, revenue, "least sold", customers, ...) returns HTTP 200
  `AI_SEARCH_NO_EVIDENCE` with `message` `"This question asks for data that
  is not supported yet."` — render it like any other no-answer message.
- A data question whose values cannot be obtained returns HTTP 500
  `RAG_EXECUTION_FAILED`; the backend never substitutes a documentation
  answer for the missing values.

## `sources[]` object shape

All fields actually present on every `SourceOut` returned by the API
(confirmed from `rag/app.py`):

| Field | Type | Notes |
|---|---|---|
| `source_label` | string | `"S1"`, `"S2"`, ... — deterministic, in the order the model was shown them |
| `chunk_id` | string | stable identifier of the retrieved chunk |
| `document_id` | string | which canonical document this came from |
| `document_title` | string \| null | |
| `section_path` | string \| null | breadcrumb within the document |
| `section_title` | string \| null | |
| `rule_ids` | string[] | e.g. `["GOLD-032", "GOLD-029"]`; empty list if none |
| `rank` | int | 1-based retrieval rank |
| `score` | float | retrieval score — **not** a confidence/correctness score, do not display it as "% confident" |

## `citation_integrity` shape

Present only when `status == "AI_SEARCH_OK"`; `null` for every other status
(there is no answer to check citations on). For an answer built only from
structured data (see "Structured answers") `sources` is `[]` and this object
is `{"status": "NO_CITATIONS", "cited_labels": [], "valid_labels": [], "unknown_labels": []}`.

| Field | Type | Meaning |
|---|---|---|
| `status` | `"VALID" \| "INVALID" \| "NO_CITATIONS"` | see below |
| `cited_labels` | string[] | every `[Sn]` label the model's answer text actually used, deduplicated |
| `valid_labels` | string[] | subset of `cited_labels` that correspond to a real entry in `sources[]` |
| `unknown_labels` | string[] | subset of `cited_labels` that do **not** correspond to any real source — a structural defect, if non-empty |

**Important — what this does and does not mean:**
- `VALID` means *"every citation label the model used actually exists in
  the sources list."* It is a **structural** check only.
- `VALID` does **not** mean the answer is correct, fact-checked,
  semantically verified, or guaranteed true, and it does **not** mean the
  cited source actually supports the specific claim next to it. That
  deeper check (semantic groundedness) is not implemented anywhere in
  this system yet — don't present `VALID` to end users as "this answer is
  verified" or "grounded."
- `NO_CITATIONS` means the model's answer didn't use any `[Sn]` marker at
  all. This is **not** the same as `INVALID` — don't show it as a warning/
  error. A neutral note ("this answer did not cite a specific source") is
  the right tone, distinct from both `VALID`'s checkmark and `INVALID`'s
  warning.
- `INVALID` means the model cited a label that doesn't exist in `sources[]`
  — a real, useful signal, worth surfacing plainly (e.g. "this answer
  referenced a source that wasn't actually retrieved").

## Citation UX

The answer text contains inline markers like `[S1]`, `[S2]`, sometimes
grouped (`[S1, S4]`). Each marker is a reference into `sources[]`:

```text
[S1]  →  find the entry in sources[] where source_label == "S1"
```

Suggested UI treatments (frontend's visual call, not prescribed here):
source card, popover on hover/click, or an expandable "References"
section below the answer. Useful fields to surface per source:
`document_id`, `rule_ids`, `rank`, `score` — pick what fits the design.

Reminder: `citation_integrity.status == "VALID"` is a structural
existence check only (see above) — do not build UI copy that implies
fact-checking or semantic verification.

## Recommended UI States

**Idle** — input available, no request in flight.

**Loading** — after submit: show a loading indicator, disable/guard
against duplicate submission, keep the submitted question visible. One
observed live `GOLD-032` end-to-end request took approximately **11.4
seconds** (MLflow-measured, real deployed App). This is an observed
sample, not an SLA or latency guarantee — size the loading UX for at
least this order of magnitude, ideally with some headroom.

**Success** (`status == "AI_SEARCH_OK"`) — render `answer`, citations, and
`sources`. If `sources` is empty (a structured-data answer), render the
`answer` only — keep its line breaks — and omit the sources section.

**No evidence** (`status == "AI_SEARCH_NO_EVIDENCE"`) — render `message`.
Do not render a fabricated answer; there isn't one (`answer` is `null`).

**Invalid request** (`status == "INVALID_REQUEST"`) — ideally validate
non-empty/non-whitespace client-side too, but the backend will still
return this (HTTP 400) if that's bypassed — handle it the same way.

**Backend failure** (`status == "RAG_EXECUTION_FAILED"`, or any
non-2xx/400 the client doesn't otherwise recognize) — show a generic
failure/retry message. Never render: stack traces, internal exception
text, catalog/schema/index names, tokens/credentials, or MLflow
internals — none of these are ever present in the response body, but the
UI shouldn't assume that will always stay true without being told.

## Authentication / Integration

**Important open item for handoff — read before wiring the real backend.**

The App itself (`rag/app.py`) implements **no authentication logic of its
own** — no API key check, no custom auth middleware. Confirmed directly
from the code: there is nothing between FastAPI's route and the request
except the `AskRequest` model. All authentication is enforced by the
Databricks Apps platform itself, in front of the application.

### A. Browser, inside Databricks, as an authenticated workspace user

This is the platform's standard behavior for any Databricks App: a
workspace user who is authenticated to the workspace (normal SSO session)
and has been granted `CAN_USE` on the `rag-api` app resource can open the
App URL in a browser and the platform handles the session automatically —
no token handling in application code. **Not independently
browser-tested in this session** (all live validation here used
programmatic OAuth bearer tokens, not a browser SSO session) — documented
as the platform's documented/expected behavior, not as separately
live-validated.

### B. External frontend / local development (e.g. `localhost`)

```text
Authentication required: YES — a valid Databricks OAuth bearer token,
  sent as `Authorization: Bearer <token>` on every request. This was
  confirmed empirically: every live-validated call in this document used
  such a token (obtained via `databricks auth token`); no request without
  one was tested to succeed.
Mechanism: Databricks Apps platform-level OAuth — not implemented or
  checked by rag/app.py itself.
CORS state: NOT configured. Confirmed by code inspection (no
  `CORSMiddleware`, no CORS handling anywhere in rag/app.py) and
  empirically: an `OPTIONS /api/ask` preflight request (with
  `Origin: http://localhost:3000` and
  `Access-Control-Request-Method: POST`) returned `HTTP 405 Method Not
  Allowed` with no `Access-Control-Allow-Origin`/`Access-Control-Allow-
  Headers` response headers at all. A plain `GET /openapi.json` also
  returned no CORS headers.
Browser implications: a browser-based frontend running on a different
  origin (e.g. a local dev server on localhost, or any non-Databricks
  domain) attempting a direct `fetch()`/`XMLHttpRequest` POST to
  `/api/ask` with an `Authorization` header will trigger a CORS
  preflight, which will fail as shown above — the browser will block the
  request **before** it reaches the backend, regardless of whether the
  bearer token is valid.
```

**`OPEN UI INTEGRATION DECISION`**: CORS is not currently configured by
the application. Direct browser calls from a different origin may
therefore require an integration decision before final deployment —
options include (not decided here, not implemented here): adding
`CORSMiddleware` to `rag/app.py` for specific allowed origins, or routing
the frontend's calls through a small same-origin/server-side proxy that
holds the Databricks credential instead of the browser. Non-browser
callers (a backend-to-backend call, a script, `curl`, a server-side
Node/Python proxy) are **not** affected by CORS — CORS is a
browser-enforced restriction only.

**This does not block starting UI work.** Use the mock payloads below to
build the UI now; the auth/CORS integration path can be resolved
separately, before the UI is wired to the real URL.

## Access you need

You (and any group your future UI's identity runs as) need **`CAN_USE`**
on the `rag-api` app resource to call it. Already granted directly to
`leonardo.barbosa@indicium.ai` as part of this deployment. If a different
identity ends up calling it (e.g. a UI running under its own service
principal, or a shared team group), that identity needs the same `CAN_USE`
grant added — a one-line addition to `resources/rag_app.yml`'s existing
`permissions:` block, not a new mechanism.

## Frontend Mock Payloads

Copy/paste-ready. The first three are the exact live-validated response
bodies above; the fourth is the implemented (but not live-forced) failure
contract.

```json
// 1. Success — mirrors the live GOLD-032 response (full 5 sources)
{
  "status": "AI_SEARCH_OK",
  "answer": "GOLD-032 refere-se à definição de um pedido de compra em aberto, que é considerado como tal quando seu status é \"sent\" ou \"partially_received\" [S1, S4]. Esta regra é mencionada em vários documentos, incluindo `powerbi_semantic_rules` [S1, S2], `inventory_and_stockout` [S3, S5] e `gold_business_rules` [S4]. Pedidos em \"draft\", \"received\" ou \"cancelled\" não são incluídos na contagem de pedidos em aberto [S4]. A definição de GOLD-032 é usada em diferentes contextos, como na medida de \"Pedidos Abertos\" em `powerbi_semantic_rules` [S1] e nos cálculos de reposição em `inventory_and_stockout` [S5].",
  "sources": [
    {"source_label": "S1", "chunk_id": "d211a227a355a8ba9df23a0c57fc613ee57cd55f37497a8245e792491d108e49", "document_id": "powerbi_semantic_rules", "document_title": "Regras Semânticas do Power BI — Dashboard Gerencial Compass 360 (LH Nautical)", "section_path": "Medidas DAX > Estoque & Supply > `Pedidos Abertos`", "section_title": "`Pedidos Abertos`", "rule_ids": ["GOLD-032", "GOLD-029"], "rank": 1, "score": 0.9765625},
    {"source_label": "S2", "chunk_id": "b2d1d42acdc78f7628b183949de6cf64deec81bae447830cb2a481fd43b999f9", "document_id": "powerbi_semantic_rules", "document_title": "Regras Semânticas do Power BI — Dashboard Gerencial Compass 360 (LH Nautical)", "section_path": "Relação com o Corpus Canônico Existente", "section_title": "Relação com o Corpus Canônico Existente", "rule_ids": ["GOLD-004", "GOLD-008", "GOLD-029", "GOLD-032", "GOLD-017"], "rank": 2, "score": 0.9765625},
    {"source_label": "S3", "chunk_id": "670d8e9d974eea880c5903a0be9b0976f6b3036225a0d54bc9c3eef96e7e4b95", "document_id": "inventory_and_stockout", "document_title": "Estoque, Reposição e Risco de Ruptura", "section_path": "Relações com outros documentos canônicos", "section_title": "Relações com outros documentos canônicos", "rule_ids": ["GOLD-032", "GOLD-039", "GOLD-034"], "rank": 3, "score": 0.9760624679979519},
    {"source_label": "S4", "chunk_id": "0daf65a73deb3aec53a0fc33799449e48d1eecdf2ba79ae83e7c504c49239530", "document_id": "gold_business_rules", "document_title": "Regras de Negócio da Camada Gold", "section_path": "Fornecedores > Definição de pedido de compra em aberto", "section_title": "Definição de pedido de compra em aberto", "rule_ids": ["GOLD-032"], "rank": 4, "score": 0.961166253101737},
    {"source_label": "S5", "chunk_id": "a4aed3617974b65ae9e585d3c7e569fb5ca24f97ac5d684183fa86746fa13205", "document_id": "inventory_and_stockout", "document_title": "Estoque, Reposição e Risco de Ruptura", "section_path": "Regras de reposição > Pedidos de compra em aberto", "section_title": "Pedidos de compra em aberto", "rule_ids": ["GOLD-032", "GOLD-029"], "rank": 5, "score": 0.9533577533577533}
  ],
  "citation_integrity": {
    "status": "VALID",
    "cited_labels": ["S1", "S4", "S2", "S3", "S5"],
    "valid_labels": ["S1", "S4", "S2", "S3", "S5"],
    "unknown_labels": []
  },
  "message": null
}
```

```json
// 2. No evidence — exact live GOLD-999 response
{
  "status": "AI_SEARCH_NO_EVIDENCE",
  "answer": null,
  "sources": [],
  "citation_integrity": null,
  "message": "No relevant information was found for this question."
}
```

```json
// 3. Invalid request — exact live whitespace-query response
{
  "status": "INVALID_REQUEST",
  "answer": null,
  "sources": [],
  "citation_integrity": null,
  "message": "Please provide a non-empty question."
}
```

```json
// 4. Backend execution failure — implemented contract, not live-forced
{
  "status": "RAG_EXECUTION_FAILED",
  "answer": null,
  "sources": [],
  "citation_integrity": null,
  "message": "Something went wrong processing this request."
}
```

## Streaming

**V1 is non-streaming.** Confirmed from `rag/app.py`: the route handler
is synchronous, builds one `AskResponse`, and returns it as a single
`JSONResponse` — there is no `StreamingResponse`, no `text/event-stream`,
no WebSocket anywhere in the file.

```text
request → wait → one complete JSON response
```

There is no SSE and no token-by-token streaming today. Do not build the
UI expecting to stream tokens against this endpoint — the loading state
should assume a single wait, not incremental rendering.

## Not Available in V1

Verified absent from the request/response contract and from what the
backend actually does:

- conversation history / multi-turn memory (every call is independent —
  there is no session state on the server between requests)
- `conversation_id` (not part of the request schema)
- a public status or field dedicated to structured data — structured/`BOTH`
  answers reuse `AI_SEARCH_OK` for now (see "Structured answers"); the
  structured route itself is implemented locally but **not live** until
  Phase 6B.2
- structured provenance (which table/query a number came from) — not
  exposed; structured values have no `sources[]` entry
- any analytical intent other than the best-selling-products ranking
- UI-selectable retrieval mode / source route
- UI-selectable Top-K
- model selection
- token-budget control (not implemented anywhere in the system yet, not
  just unexposed)
- streaming (see above)
- semantic groundedness / fact-check score (`citation_integrity` is a
  structural label check only, not this)

## UI Handoff Checklist

```text
[x] App live (app_status = RUNNING, compute_status = ACTIVE)
[x] POST /api/ask
[x] success path validated live (GOLD-032 → AI_SEARCH_OK)
[x] no-evidence path validated live (GOLD-999 → AI_SEARCH_NO_EVIDENCE)
[x] invalid request validated live (whitespace query → INVALID_REQUEST, HTTP 400)
[x] stable response schema documented (status/answer/sources/citation_integrity/message)
[x] citation mapping documented ([Sn] → sources[].source_label)
[x] mock payloads available (4, copy/paste-ready above)
[x] non-streaming behavior documented

[ ] structured-data answers live on the deployed App — Phase 6B.2 (implemented locally; no contract change, no UI change needed)
[ ] final external-browser authentication/integration path — OPEN UI INTEGRATION DECISION (see "Authentication / Integration")
[ ] CORS decision, if a browser-based frontend outside Databricks is required
```

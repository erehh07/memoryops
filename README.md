# MemoryOps

An accounts-payable agent that learns a finance team's unwritten rules for invoice exceptions.
Every human decision and its reason is retained in **Hindsight**. When a similar exception shows up,
the agent recalls those precedents, recommends an action, and cites the precedent IDs it used.

**The agent only recommends.** A human records every decision, and nothing is ever paid or posted.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
cp .env.example .env                               # optional: add GROQ / Hindsight keys
cd frontend && npm install && npm run build && cd ..
.venv/Scripts/python -m uvicorn backend.app.main:app --port 8000
```

Then open http://localhost:8000. The database seeds itself on first start.
For frontend development, run `npm run dev` in `frontend/`; it serves on :5173 and forwards `/api` to :8000.

Tests: `.venv/Scripts/python -m pytest backend/tests -q`

## Demo script (about 3 minutes)

1. **Batch 1 → Run three-way match.** This finds 17 exceptions. Memory is empty, so the agent escalates all of them and says it has no precedent.
2. **Replay seniors' decisions.** This loads [`backend/data/senior_decisions.json`](backend/data/senior_decisions.json), 32 decisions with reasons from four experienced approvers, and retains each one in memory.
3. **Batch 2 → Run three-way match.** The agent now escalates only 1 of 15 exceptions, and every recommendation cites precedent IDs. Open **ARB-1112**: the agent says "approve", based on June's 3% escalator. Record a **reject** with the reason "escalator clause lapsed 30 June". The UI flags it as an override and it goes into memory.
4. Replay the seniors for batch 2, then **Batch 3 → Run three-way match**. Look at these cases:
   - **ARB-1120** → *reject*. "Pattern changed": recent precedents outweigh older ones.
   - **NFF-24172** (+10.2%) → *adjust*. It is above the largest approval, and a similar 9.5% case was short-paid.
   - **SOL-4451** → *escalate*. The precedents conflict (one approval, one rejection).
   - **KIT-9080** ($18,500 with no PO) → *escalate*. It is far above the $2,400 blanket-agreement precedents.
   - **VLI-0012** (new vendor) → *escalate*, with "No relevant precedent" and no citations.
   - **HPP-88050** → *approve*, routed to **Priya Raman**, the only person who has ever signed off Halden variances.
5. The **"Is it learning?"** panel shows the escalation rate and human agreement per batch. **Memory inspector** runs the same recall query the agent uses.

## Design

| Concern | Where | Notes |
|---|---|---|
| Three-way match | `backend/detector/match.py` | Deterministic `Decimal` maths with ROUND_HALF_UP. Types: price_variance (%), quantity_mismatch (delta), tax_error, duplicate_invoice, missing_po |
| Resolution options + amounts | `backend/detector/options.py` | Python computes every amount. The LLM only picks an option |
| Memory interface | `backend/memory/base.py` | `retain(record)` and `recall(query, k)`. The rest of the app uses only this interface |
| Hindsight | `backend/memory/hindsight_store.py` | The **only** file that imports `hindsight_client` |
| Local fallback | `backend/memory/local_store.py` | Keyword + field-match scoring. Used only when Hindsight is unset or unreachable. The UI shows an amber **"Memory: local fallback"** badge |
| Recommender | `backend/agent/recommender.py` | Enforces the hard rules in code, whichever chooser runs |
| LLM | `backend/agent/llm.py` | Groq `openai/gpt-oss-120b` with strict JSON schema, falling back to `qwen/qwen3-32b` with JSON mode. Output is validated with Pydantic, retried with exponential backoff, and escalated if it still fails |
| Offline chooser | `backend/agent/offline.py` | Used when no `GROQ_API_KEY` is set. A deterministic precedent vote, shown as **"LLM: offline"** |
| App records | `backend/db.py` (SQLite) | vendors, purchase_orders, goods_receipts, invoices, exceptions, recommendations, decisions, audit_log, batches |

**Hard rules, enforced in `recommender.py`:**
- At most 5 memories are recalled. Only those for the *same vendor and exception type* count as relevant.
- If nothing relevant is recalled, the agent escalates, says so, and cites nothing.
- Citations must be a subset of the recalled relevant IDs. For the LLM this is enforced twice: the JSON schema `enum` of IDs, then re-validation.
- A non-escalate recommendation with no citation is turned into an escalation.
- The agent never approves anything larger than the largest approved precedent (guard).
- A memory or LLM failure leads to escalation.

**What is retained:** one `PREC-####` record per decision. It holds the invoice, vendor, type, variance or quantity delta, decision, approver name and role, reason, date and override flag, as text plus string metadata and tags `vendor:*`, `type:*`, `kind:*`.
Two kinds of derived facts are rebuilt deterministically once decisions repeat:
- `VF-<vendor>-<type>`: the vendor pattern, with ranges, the latest decision and payment terms.
- `AP-<approver>-<vendor>-<type>`: an approver's preference, including "route to X" when one person made every decision.

SQLite keeps the application records. The learned memory lives in Hindsight.

### Frontend choice
React + Vite + Tailwind (v4), not Streamlit. The review flow needs a side-by-side exception list and detail view, a line-level three-way-match table, highlighted citations, and a decision form. All of this fits a small component tree. The production build is served by FastAPI, so the demo runs as one process.

## Hindsight: what was verified and what was not

Verified against the installed `hindsight-client==0.10.1` source code and its generated API models:
- `Hindsight(base_url, api_key)` sends the API key as a Bearer token.
- `retain(bank_id, content, timestamp, context, document_id, metadata, tags, update_mode)`
- `retain_batch(bank_id, items)`
- `recall(bank_id, query, types, max_tokens, budget, include_chunks, tags, tags_match)`
- `create_bank(bank_id, retain_mission=…)`, `delete_bank`, `get_version`
- The `RecallResult` / `ChunkData` fields.

One test runs the store through the **real client code**, stubbing only the HTTP layer, to check that the request payloads are valid.

**Not confirmed: no live Hindsight server was available.** There was no Docker and no Cloud key in this environment, so these points are unverified:
1. **Whether each recalled fact echoes back the `metadata` and `document_id` it was retained with.** The store handles this defensively. It maps a fact to a record ID via `metadata.record_id`, then `document_id`, then a `[MEMORYOPS <id>]` header in the source chunk, then the fact text. A fact with no recoverable ID is dropped and never cited. If metadata is missing, decision fields are filled in from SQLite.
2. **The exact semantics of `update_mode="replace"`** when a `VF-*` or `AP-*` fact is re-retained under the same `document_id`.
3. **How long synchronous `retain` takes on the server.** It runs server-side LLM extraction, so "Replay seniors' decisions" may take a while on Cloud.

To use Hindsight, set these in `.env`:
- Cloud: `HINDSIGHT_BASE_URL=https://api.hindsight.vectorize.io` and `HINDSIGHT_API_KEY=…`
- Self-hosted: `docker run … ghcr.io/vectorize-io/hindsight` then `HINDSIGHT_BASE_URL=http://localhost:8888`

When configured, the badge turns green.

## Security
- `.env` is gitignored and `.env.example` is provided.
- `/api/status` reports only whether each key is *set*.
- Logs record exception types, never request headers or keys.

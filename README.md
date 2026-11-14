# MemoryDelta

**Governed, evidence-preserving agent memory on MongoDB Atlas.**
Track: *Memory You Can Govern*, Agents in Context Toronto (Nov 14, 2026). All code in this repo was written during the hackathon.

> The most relevant memory is sometimes the one your agent has to refuse.

An analytics agent answers *"How many active customers did we have in October 2026?"* for several tenants.
Atlas Vector Search proposes candidate memories. Application code then decides which ones may reach the model:

```
eligible = same tenant (from the trusted session) AND allowed user AND status == active
           AND not expired AND evidence complete AND policy_version == current authoritative policy
```

Each memory is re-read by `_id` from the collection before use. Vector indexes lag behind writes, so a just-revoked memory can still show up in search results; the re-read catches it.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # then fill in the values below
```

| Variable | Needed for | Without it |
|---|---|---|
| `MONGODB_URI` | live Atlas (sandbox) | runs on an in-memory Mongo (`mongomock`) with the same code, except `$vectorSearch` is computed in Python |
| `ANTHROPIC_API_KEY` **or** `ZAI_API_KEY` (+ `ZAI_BASE_URL`, `ZAI_MODEL`) | live model: Claude (`claude-opus-5-5`) or Z.ai GLM (default `glm-4.6`). Choose with `MEMORYDELTA_PROVIDER=anthropic\|zai` | a scripted test double drives the tools. Its results are **not** evidence of model behavior |
| `VOYAGE_API_KEY` (+ `VOYAGE_BASE_URL=https://ai.mongodb.com/v1` for Atlas-hosted Voyage keys) | `voyage-3.5` embeddings | a local hashing embedder (not semantic) |

Everything is written to one dedicated database, `memorydelta_demo`, in collections `orders, policies, memories, memory_deltas, runs`. All data is synthetic. Nothing outside that database is read or changed.

## Commands

```bash
.venv/bin/python -m memorydelta.ping     # check Atlas connection (read-only)
.venv/bin/python -m memorydelta.seed     # reset + seed the demo database, create vector/TTL indexes + validator
.venv/bin/python -m pytest -q            # deterministic tests (+ live Atlas tests when MONGODB_URI is set)
.venv/bin/python -m memorydelta.demo     # full demo; reseeds first; writes results/comparison.json
.venv/bin/python -m memorydelta.bench    # 10 hard cases x 3 memory strategies; writes results/bench.json
.venv/bin/python -m memorydelta.server   # flow viewer at http://localhost:8765
```

### Hard-case benchmark

`memorydelta.bench` runs 10 cases. Each case starts from a fresh seed and adds one complication: a changed
business rule, another tenant asking, a prompt claiming another tenant, an unverified agent note, an
expired memory, a memory private to another user, a superseded version still stored, another tenant's rule
change (MemoryDelta must *not* over-block), plus two controls. Every case runs the same task with the same
model and data under three strategies:

| Arm | Memory handling |
|---|---|
| `naive` | top-k similar memories from any tenant, no checks (plain RAG memory) |
| `scoped` | memories filtered to the tenant, no validity checks (a typical per-tenant memory store) |
| `memorydelta` | tenant-filtered Vector Search, `_id` re-read, validation gate |

Ground truth is computed from the data under the tenant's active policy. The scorecard reports correct
answers, foreign memories in the prompt, and ineligible memories in the prompt (any memory the gate would
reject that still reached the model), plus tool calls and input tokens. The viewer's **Hard cases** tab
shows it. See [PITCH.md](PITCH.md) for the demo and pitch scripts.

### Flow viewer

`memorydelta.server` serves `ui/index.html`, a local page that replays each scene from the latest
`results/comparison.json`. Each run is one row, and the columns follow a memory through the pipeline:
**who asks → Vector Search → validation gate → sent to the model → agent actions → verified answer.**
Baseline and MemoryDelta runs sit side by side, so leaks and stale answers show up in red.
**Run live on Atlas** reruns the whole demo (about 2 minutes) and reloads the page.
Keys: ← → switch scenes, Space replays the animation. The page is a presentation layer for the
demo; the product is the governed memory runtime.

## What the demo shows

| Scene | What happens | Proof |
|---|---|---|
| 1. Isolation | acme, globex and initech ask the same question. Each gets only its own memory. initech has none and still answers. An *unscoped* baseline shows what leaks without scoping. | memory IDs actually placed in the model request |
| 2. Behavior change | Same task, model, settings and data. Only whether the approved memory is provided changes. Expected outcomes are printed **before** running. | executed tool calls + structured `submit_answer`, not the explanation |
| 3. Policy v1 → v2 | The authoritative definition of "active" changes. The ungated baseline reuses the stale memory and answers **120**. The gate rejects it (`policy_version_mismatch`), the agent replans and answers **93**. | answer + rejection reason |
| 4. Correction | `correct_memory` re-verifies against current data, writes v2, marks v1 `superseded`, and records a delta. The next run receives only v2. | `included: [mem@v2]`, `rejected: [mem@v1: status_superseded]` |
| 5. Revocation | `revoke_memory` marks **every version** revoked and removes the embedding and content. The next run receives neither version and still answers via a fresh plan. | `included: []` |
| 6. Compression | The same authorized evidence is given as a raw trajectory vs a compact structured memory (filter, result, policy version, source IDs). | input tokens, whether the expected action was chosen, whether the evidence pointers resolve in Atlas |

**Revocation means exclusion from future runs.** The memory's embedding and content are removed from its documents. Its metadata (id, version, status, who and why) stays for audit. It is not a hard delete, and Atlas backups and the oplog are out of scope.

Every run is written to `runs` with the memory IDs and versions actually sent, the rejected IDs and reasons (never their contents), the executed tool calls, the decision and token counts. Counts are labelled `api_usage` when they come from the API and `estimate` otherwise.

## The three official questions

| Question | Answer in this repo |
|---|---|
| Keep one tenant's memory from leaking into another's | Tenant comes from the session (`config.authenticate`), never the prompt or tool args. `$vectorSearch` prefilters on `tenant_id`, then the gate re-checks after an `_id` re-read. Tests capture the real model payload and also simulate a retrieval bug (`test_gate_blocks_foreign_memory_even_if_search_leaks`). |
| Fix or drop a wrong or expired memory | `correct_memory` (versioned, re-verified, delta recorded), `revoke_memory` (all versions), expiry checked on every read (TTL index only does cleanup), `$jsonSchema` validator on `memories`. |
| Prove memory changed behavior, not just the prompt | Scene 2 compares executed tool calls and the structured decision with vs without the approved memory. Scene 3 shows a stale memory changing the answer when ungated. |

## Layout

```
memorydelta/
  config.py     trusted RequestContext, session lookup, settings
  db.py         connection, $jsonSchema validator, TTL + vector index setup
  seed.py       synthetic tenants/orders/policies; bootstraps one approved memory per tenant
  tools.py      bounded tools; tenant injected server-side; filter allow-list
  gate.py       eligibility rules -> (eligible, reason)
  store.py      $vectorSearch, re-read + gate, create / correct / revoke
  compress.py   trace -> evidence-preserving memory; raw vs compact rendering
  model.py      Anthropic + Z.ai GLM (OpenAI-compatible) clients, scripted test double, payload recorder
  agent.py      tool loop, structured decision, run logging
  demo.py       the CLI demo
  server.py     local flow viewer (stdlib HTTP server)
ui/index.html   flow viewer page (vanilla HTML/CSS/JS)
tests/          isolation, lifecycle, behavior/compression harness, live Atlas
```

Third-party: `pymongo`, `anthropic`, `openai` (Z.ai GLM endpoint), `mongomock` (tests/offline), `rich`, `python-dotenv`, Voyage AI embeddings API.

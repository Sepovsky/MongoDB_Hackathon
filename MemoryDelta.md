# MemoryDelta: Evidence-Preserving, Governed Agent Memory

> **Pitch:** *The most relevant memory is sometimes the one your agent has to refuse.*
>
> **Technical story:** We compress successful agent runs into reusable evidence and store it in MongoDB Atlas. Before reuse, we check the memory's permissions and the rules it depends on. When the environment changes, the agent rejects the outdated memory, corrects its behavior, and doesn't repeat the old mistake.

| | |
|---|---|
| **Event** | Agents in Context Toronto: A MongoDB Hackathon · Sat Nov 14, 2026 · The Quay |
| **Track** | Statement 2, **Memory You Can Govern** |
| **Team** | Solo |
| **Clock** | Hacking 10:45 → **submit by 16:30** (hard deadline 17:00) → judging 17:15 → finals 19:00 |
| **Tagline** | Compress the past. Track the changes. Trust the memory. |

This one file holds both the idea and the build plan.

---

## 1. The hackathon

### Our problem statement (Statement 2)
> What an agent remembers is data someone has to answer for. Build an agent using MongoDB Atlas and Vector Search to persist and retrieve memory, so that memory changes what it does on the next run, with rules for who can read each memory, how long it lives, and how it gets corrected or forgotten.
>
> 1. How do you keep one client's or team's memory from leaking into another's?
> 2. How does the agent fix or drop a memory that turns out wrong or expired?
> 3. How do you prove a memory changed the agent's behavior and didn't just fill the prompt?

### Hard requirements
- [ ] Built in the **organizer's MongoDB Atlas Sandbox** (from the email link), or you can't be a finalist
- [ ] Atlas stores the memory, and **Atlas Vector Search** retrieves it
- [ ] **Original work built today.** Libraries, APIs, known techniques and your research knowledge are fine. Don't present existing code (e.g. TrailBench) as built today.
- [ ] **Public GitHub repo** + **1-minute demo video with audio** + short description, submitted on **Cerebral Valley**
- [ ] Demo is live and in person. Round 1 is about 3 min + 1–2 min Q&A; the top 6 present at 19:00.
- [ ] No banned project types: **basic RAG, Streamlit, dashboard as the main feature**

### Judging (round 1; round 2 weights all four equally)
| Criterion | Weight | What it means for us |
|---|---:|---|
| Technical demo | 35% | Live runs; a visible stale-memory → corrected-memory moment |
| Implementation difficulty | 30% | Real compressor, scoped vector retrieval, validation gate, measured behavior |
| Impact potential | 20% | Stale or leaked agent memory is a real enterprise risk |
| Creativity | 15% | Evidence-preserving compression + dependency-aware governance |

### Resources we use
- **Voyage AI**: 200M free tokens, embeddings with `voyage-3.5` (1024-d)
- **MongoDB Agent Skills + MongoDB MCP Server**: install in Claude Code so it can inspect the live database while we build
- MongoDB staff and the Voyage team are on site. Ask them early.
- Skipping ElevenLabs. Voice adds nothing to this track.

---

## 2. The idea

### The problem
Most memory agents work like this: summarize a past run, embed it, retrieve it later. That breaks in three ways:
1. **Bloat:** most of a past run (retries, logs, dead ends) is irrelevant next time.
2. **Staleness:** the most similar memory may encode an outdated schema or business rule.
3. **Governance:** a relevant memory may belong to another tenant, or be expired, corrected or revoked.

### Our hypothesis
A compact memory that keeps its **evidence and dependencies**, plus a validation gate, lets an agent reuse past work cheaply **without** reusing it when it's wrong or forbidden.

### Our differentiator
**Similarity is not trust.** Vector Search only *proposes* candidates. MemoryDelta decides whether a memory is **authorized, live, and still valid for today's rules** before it can shape the agent's actions. Because the compressor records exactly what a memory depends on, the gate can check those dependencies in code instead of asking an LLM.

### Four components
1. **Memory compressor:** run trace → `goal · plan · critical evidence · assumptions · dependencies`
2. **Atlas memory store:** memories, embeddings, tenant ownership, versions, expiry, audit runs
3. **Validation gate:** `$vectorSearch` with tenant prefilter → re-read by `_id` → permission / revocation / expiry / dependency checks
4. **Agent + behavioral evaluation:** reuse or replan; record tool calls, answers, tokens and gate decisions

### What we build, and what we don't
- **We build:** a small analytics agent that runs a task, saves a compressed memory, reuses it, notices a rule change, refuses foreign or revoked memory, and proves the difference.
- **We don't build:** an agent framework, a dashboard, real IAM, multi-agent setups, a neural compression model, or delta chains.

---

## 3. Demo use case

**Question (same every time):** *"How many active customers did we have in October?"*

Synthetic retail data in Atlas:
- **Tenant A, policy v1:** active = "placed any order" → **120**
- **Tenant A, policy v2:** active = "placed a paid, non-refunded order" → **93**
- **Tenant B:** its own orders and numbers. Its memories must never reach tenant A, and the other way round.

The numbers are **fixture targets**: the seed script generates data that produces them, and a test asserts them. They're never hard-coded into agent output.

### Scenes: show the failure first, then the fix
| Scene | Ungoverned memory (naive arm) | MemoryDelta |
|---|---|---|
| **1. Cold run** (tenant A, policy v1) | Discovers schema, builds query → **120** ✅ (6 tool calls) | Same → **120** ✅, writes compact memory |
| **2. Warm run** | Reuses → 120 ✅ | **Reuses verified plan → 120 ✅ in 2 tool calls**, with a trace diff showing the skipped steps |
| **3. Policy v1 → v2** (definition becomes "paid, non-refunded order") | Retrieves top-similar memory, replays old filter → **120 ❌ stale** | Dependency check: `policy_version 1 ≠ 2` → reuses only the parts still valid, rebuilds the filter → **93 ✅**, saves v2, v1 marked `superseded` |
| **4. Tenant B asks** | Top hit is tenant A's memory → **leaked into prompt ❌** | Prefilter + `_id` recheck → **0 foreign memories reach the LLM ✅**, fresh plan |
| **5. Revoke / expire** | Still used (index lag) ❌ | Blocked at once, before TTL deletion or index refresh ✅ |

**Wow line (scene 3):** "Both agents found the same memory. Only one of them knew not to trust it."

**Live vs automated:** On stage, run scenes 1–4 (scene 2 can be a quick glance). Scene 5, placebo runs, repeated trials and compression stats live in `pytest` and `results/comparison.json`. Show that table for 15 seconds at the end. All numbers come from the seeded dataset and real tool runs. Nothing is hard-coded in agent output.

---

## 4. How it works

### 4.1 Evidence-preserving compression
**Input:** the raw trace (request, tool calls, outputs, failures, schema lookups, queries, validation, answer).
**Output:** a typed memory:

```json
{
  "goal": "count_active_customers",
  "plan": ["read policy", "filter orders by policy", "count distinct customer_id"],
  "critical_evidence": {
    "collection": "orders",
    "fields": ["customer_id", "status", "refunded", "order_date"],
    "verified_pipeline": "<exact aggregation pipeline, verbatim>",
    "result": 120
  },
  "assumptions": ["October = 2026-10-01..2026-10-31 UTC"],
  "dependencies": { "policy_id": "active_customers", "policy_version": 1 }
}
```

**Algorithm (deterministic):**
1. Parse tool events into typed records: `get_policy`, `inspect_collection`, `run_count_query`, `validate_result`, `answer`.
2. Start from the final successful answer and work backwards: keep only the steps, fields, policy versions and outputs it depends on.
3. Copy machine-critical details **verbatim** (pipelines, IDs, versions). The LLM never paraphrases them. It may only write a one-line `summary` used for embedding.
4. Drop retries, failed attempts and chatter.
5. Check required fields are present (`evidence_complete`); Atlas `$jsonSchema` rejects incomplete memories.
6. Log `raw_tokens` vs `compressed_tokens` with the same tokenizer.

> Success means the memory reproduces the correct next action with less context **and** every assumption can be checked by code. A smaller memory that loses evidence is a failure.

### 4.2 Validation gate
A memory reaches the LLM only if:
```
authorized(identity, memory)            # tenant + allowed_users; identity from trusted server session, never the prompt
AND memory.status == "active"           # re-read by _id, not from index results
AND now < memory.expires_at             # checked on every read; TTL deletion is lazy
AND deps_compatible(memory, current)    # policy_version matches the authoritative policy
AND evidence_complete(memory)
```
- The tenant filter runs **inside** `$vectorSearch` (prefilter), then again server-side after the `_id` re-read.
- The re-read matters because **Vector Search indexes lag behind writes**: a just-revoked memory can still come back from the index for a few seconds.
- Unauthorized memory text is **never** sent to the LLM, not even to decide whether to use it.
- Every decision and its reason is written to `runs` (the audit trail).

### 4.3 Change, correction and forgetting
- **Unchanged dependencies** → reuse the verified plan.
- **Changed policy version** → keep the steps that are still valid, rebuild the invalidated filter from the authoritative policy, verify, save v2 (`parent_version: 1`), mark v1 `superseded`.
- **Breaking or unclear change** → reject the memory and plan from scratch.
- `correct_memory(id, patch, actor)`: new version, re-verified, plus one delta doc:
  ```json
  { "memory_id": "m_a_active_customers", "from_version": 1, "to_version": 2,
    "path": "dependencies.policy_version", "old_value": 1, "new_value": 2,
    "invalidates": ["critical_evidence.verified_pipeline"], "actor": "policy_admin" }
  ```
- `revoke_memory(id, actor)`: **all versions** set to `revoked`, `embedding` and evidence text removed, linked deltas revoked. Old versions can't be used to get revoked content back.
- Policies are **trusted business input**: only an authorized update changes them, never the agent.

### 4.4 Architecture
```mermaid
flowchart TD
    U[User task + trusted identity] --> V[Atlas Vector Search, tenant prefilter]
    V --> R[Re-read by _id]
    R --> G{Gate: authorized, active, not expired, deps match, evidence complete?}
    G -->|pass| M[Reuse verified plan]
    G -->|fail + reason| F[Fresh plan from current policy]
    M --> A[Agent executes bounded tools]
    F --> A
    A --> T[Trace: tool names + args + outputs]
    T --> C[Evidence-preserving compressor]
    C --> DB[(Atlas: memories)]
    T --> RUNS[(Atlas: runs / audit)]
    P[Authorized policy update] --> POL[(Atlas: policies)]
    POL -.version check.-> G
```

### 4.5 Runtime (pseudocode)
```python
def answer_task(request, identity, arm="governed"):
    policy = read_authoritative_policy(identity.tenant_id, request.metric)
    hits = vector_search(embed(request.text),
                         filter={"tenant_id": identity.tenant_id, "status": "active"} if arm == "governed" else {})
    memory, decision = None, "no_candidates"
    for hit in hits:
        doc = memories.find_one({"_id": hit["_id"]})          # fresh read, not index copy
        if arm == "naive":
            memory, decision = doc, "naive_top1"; break
        ok, decision = gate(doc, identity, policy)              # returns (bool, reason)
        if ok:
            memory = doc; break
    plan = reuse(memory) if memory else plan_from_policy(request, policy)
    outcome, trace = execute_and_verify(plan)                  # trace = real tool names + args
    save_memory(compress(trace, policy), identity)             # validator rejects incomplete memories
    log_run(identity, arm, memory, decision, trace, outcome)
    return outcome
```

---

## 5. MongoDB data model

| Collection | Key fields | Indexes / features |
|---|---|---|
| `policies` | `tenant_id, metric, version, definition, filter_spec, active` | unique `{tenant_id, metric, version}`; **trusted input, never written by the agent** |
| `orders` | `tenant_id, customer_id, order_date, status, refunded` | `{tenant_id, order_date}` |
| `memories` | `tenant_id, owner_user_id, allowed_users[], task_signature, plan[], dependencies{}, evidence{}, version, parent_version, status, expires_at, embedding[1024]` | **`$jsonSchema` validator** (requires `evidence.verified_pipeline`, `dependencies.policy_version`, `tenant_id`); TTL on `expires_at`; **vector index** with `tenant_id`, `status` declared as `type: "filter"` |
| `runs` | `identity, scenario, arm, memory_id/version or null, gate_decision{reason}, tools_called[], tokens_in, answer, correct, latency_ms` | serves as both audit log and metrics source |

Vector index definition (create it **right after seeding**, so it's READY by the time you need it):

```json
{ "fields": [
  { "type": "vector", "path": "embedding", "numDimensions": 1024, "similarity": "cosine" },
  { "type": "filter", "path": "tenant_id" },
  { "type": "filter", "path": "status" } ] }
```

Embeddings: `voyage-3.5` (1024-d) through the Voyage key (200M free tokens). Use one model for everything.

**Other indexes:** `{tenant_id, status, expires_at}` on `memories`; unique `{tenant_id, metric, version}` on `policies`.

---

## 6. Evaluation: proving it works

### Arms (same task, data, LLM, tools, temperature 0)
| Arm | Memory | Shows |
|---|---|---|
| **No memory** | none | memory changes behavior |
| **Placebo** | irrelevant memory of the same token length | the change comes from content, not prompt length |
| **Naive** | same vector search, gate off | the harm: stale answers + leaks |
| **MemoryDelta** | compressed memory + gate | the fix |

### Scenario matrix (automated in `pytest` + `results/comparison.json`)
| Scenario | Naive | MemoryDelta | Assertion |
|---|---|---|---|
| Unchanged task | reuses | reuses compact plan | correct; fewer tool calls than no-memory |
| Policy v1 → v2 | 120 ❌ | 93 ✅ | v1 filter never executed after v2 |
| Tenant B asks | leaks A's memory | blocked | foreign memories reaching LLM = 0 |
| Revoked (any version) | may reuse via index lag | blocked | revoked use = 0 |
| Expired, before TTL deletes it | reused | blocked | expired use = 0 |
| Memory missing evidence | n/a | rejected by `$jsonSchema` | insert fails |

### Metrics (real measurements only, nothing invented)
| Metric | Definition |
|---|---|
| Correctness | correct answers / attempts, per arm |
| Behavior change | tool calls + **executed tool names/args**: no-memory vs memory vs placebo |
| Compression | raw vs compressed tokens **and** `evidence_complete` |
| Stale-plan rejection | stale memories rejected / stale memories tested |
| Leakage | foreign memories that reached LLM context (target 0) |
| Revoked/expired use | ineligible memories used (target 0) |
| Gate overhead | ms for search + re-read + checks |

Run 3 trials per arm if time allows and report each one. For the live demo, one reproducible run beats many flaky ones.

---

## 7. Scope by priority

### P0: must work (the submittable spine, by 13:30)
1. Seed script: tenants A and B, `orders`, `policies` (v1 and v2). Fixture: **A/v1 = 120, A/v2 = 93**, B uses different numbers. Assert these in a test.
2. Agent loop: an LLM with **bounded tools** at temperature 0: `get_policy`, `inspect_collection`, `run_count_query`, `validate_result`. Every tool call is logged to `runs`.
3. **Evidence-preserving compressor (the differentiator, deterministic):** trace → 
   `{goal, plan[], critical_evidence{collection, fields[], verified_pipeline, result}, assumptions[], dependencies{policy_id, policy_version, schema_fields}}`.
   - Work backwards from the final successful answer and keep only the steps and tool outputs it depends on. Drop retries, failed attempts and chatter.
   - **Exact query text and IDs are copied verbatim, never paraphrased by the LLM.** The LLM may write at most a one-line `summary` used for embedding.
   - Log `raw_tokens`, `compressed_tokens`, and `evidence_complete` (all required fields present) per memory.
4. Voyage embedding → `memories` → `$vectorSearch` with `filter: {tenant_id, status:"active"}`.
5. Warm run reuses the memory and makes fewer tool calls.

### P1: what makes it win (by 15:00)
6. **Gate** (`gate.py`), the only path for a memory to reach the LLM:
   `authorized(identity) ∧ status=active ∧ now<expires_at ∧ deps_compatible(current_policy) ∧ evidence_complete`. Re-read by `_id`, not from index results.
7. **Change handling:** a mismatch on `policy_version` → replan the invalidated step from the authoritative policy → verify → insert v2 with `parent_version: 1` and mark v1 `superseded`. A single delta doc records `{path, old, new, invalidates}`. No delta chains.
8. **Real correct and forget operations:**
   - `correct_memory(id, patch, actor)`: inserts a new version (re-verified against the current policy), sets the old one to `superseded`, writes a delta doc `{path, old, new, invalidates, actor}`.
   - `revoke_memory(id, actor)`: sets **every version** of that memory to `revoked`, `$unset`s `embedding` and evidence text, and marks linked deltas revoked. An old version can't be used to get back revoked content.
   - Expiry is checked on **every read** in the gate. The TTL index only handles physical cleanup, about once a minute.
   - Rule of thumb: `$jsonSchema` checks *structure*, tests check that the evidence is *correct*.
9. **Naive arm** = same retrieval with the gate turned off. Run it, don't fake it.
10. **Proof of behavior change:** for identical tasks, run no-memory / valid-memory / placebo-memory (irrelevant, same token length). Record the **actual executed tool names + arguments** from the tool layer, not the LLM's description of what it did. Print the diff. If time allows, run 3 trials per arm and report each one.
11. `results/comparison.json` + `pytest` for 6 invariants: fixture counts, zero foreign memories, no reuse after revocation (any version), expired memory blocked before TTL deletion, stale filter never executed after v2, schema rejects memory without evidence.

### P2: stretch (only if P1 is done by 15:00)
- Change stream on `policies` that marks dependent memories `needs_revalidation` as soon as a policy changes. Very MongoDB-native, about 30 min.
- B2 naive-LLM-summary baseline and token comparison.
- LangSmith tracing (you get credits anyway).
- **Skip** ElevenLabs. Voice adds nothing to this track.

### Explicitly out
Web UI/dashboard, real IAM, multi-agent setups, automatic semantic change detection, storage-savings claims for deltas.

**If you're behind, cut in this order:** P2 → placebo arm → delta doc (keep `superseded` status) → `rich` formatting.
**Never cut:** Atlas Vector Search, tenant prefilter + `_id` re-read, stale-policy rejection, revoke, naive-vs-governed comparison, the video.

---

## 8. Atlas sandbox setup (first-timers, about 25 min)

**Five terms you need:**

| Term | Meaning |
|---|---|
| **Cluster** | Your database server in the cloud |
| **Database → collection → document** | Like database → table → row, but each row is a JSON object |
| **Connection string** | `mongodb+srv://user:pass@cluster.xxx.mongodb.net`. Your code uses it to connect. It's a secret. |
| **Index** | Makes queries fast. A **Vector Search index** is a separate kind that you create in the Atlas UI under the *Atlas Search* tab. |
| **Aggregation pipeline** | A list of steps like `[{$match}, {$group}]`. `$vectorSearch` is one of those steps. |

**Steps:**
1. **Open the organizer's email** → click the sandbox link → sign in or sign up. Make sure the project you're in is the *hackathon* project shown in the top-left dropdown.
2. **Create a cluster:** *Create* → take the tier the sandbox offers (free/M0 or whatever's preselected) → pick a region near Toronto (AWS `us-east-1` or `ca-central-1`) → name it `memorydelta` → *Create Deployment*. It takes 1–3 min.
3. **Create a database user** (the popup asks right away): username `md_app`, click *Autogenerate password*, **copy it into a note now**.
4. **Network access:** *Security → Network Access → Add IP Address → Allow access from anywhere (0.0.0.0/0)*. This is the simplest option for one day on venue Wi-Fi. Delete the entry after the event.
5. **Get the connection string:** *Database → Connect → Drivers → Python* → copy the `mongodb+srv://…` string and put the password in it.
6. **Put it in your repo**, never in code:
   ```bash
   echo 'MONGODB_URI="mongodb+srv://md_app:<password>@memorydelta.xxxxx.mongodb.net/?retryWrites=true&w=majority"' > .env
   echo '.env' >> .gitignore
   ```
7. **Test the connection:**
   ```bash
   pip install "pymongo[srv]" python-dotenv voyageai
   ```
   ```python
   # scripts/ping.py
   import os; from dotenv import load_dotenv; from pymongo import MongoClient
   load_dotenv(); MongoClient(os.environ["MONGODB_URI"]).admin.command("ping"); print("pinged")
   ```
8. **The vector index** gets created *after* the seed script inserts the first memory. Either do it in the UI (*Atlas Search → Create Search Index → Vector Search → JSON editor*, paste the JSON in §5, collection `memorydelta.memories`, name it `memory_vec`), or do it from code with `collection.create_search_index(SearchIndexModel(definition=..., name="memory_vec", type="vectorSearch"))`. Wait until its status shows **READY**.

**If it doesn't work:**
- `ServerSelectionTimeoutError` → the network access entry is missing, or the venue Wi-Fi blocks port 27017. Try a phone hotspot.
- `bad auth` → wrong password in the string, or special characters in it. Generate a password with letters and numbers only.
- `$vectorSearch` returns nothing → the index isn't READY yet, the index name differs, or `numDimensions` doesn't match 1024.

**Shortcuts the organizers recommend:** install **MongoDB Agent Skills** and the **MongoDB MCP Server** in Claude Code. Then I can inspect your collections and indexes directly while we build, which helps a lot if you're new to Atlas. **MongoDB staff are on site, so ask them right away if setup stalls for more than 10 minutes.**

### Atlas pitfalls to plan for
- **Index lag:** after `insert_one`, poll `$vectorSearch` (up to about 10 s) until the new `_id` shows up. Never assume it's searchable immediately.
- **Revocation vs index:** the vector index can still return a just-revoked doc. The gate's `find_one({_id, status:"active"})` is what enforces revocation. Show this in the demo.
- **Filter fields** must be declared in the index or `$vectorSearch` errors. Set `numCandidates` to about 10–20× `limit`.
- **TTL** deletion runs on a background job about every 60 s, so never rely on it for correctness.
- **Sandbox:** create the cluster *through the organizer email link*, or you can't be a finalist. Allow your IP (or 0.0.0.0/0 for the day), and keep the URI in `.env`, which goes in `.gitignore` from the first commit.

---

## 9. Timeline

*If you start late, squeeze the middle rows, not the end. **15:30 video, 16:30 submit** are fixed.*

| Time | Goal | Exit check |
|---|---|---|
| **11:20–11:50** | Atlas sandbox setup (§8), Voyage + LLM keys, public GitHub repo with first commit + `.gitignore` | `python scripts/ping.py` prints `pinged` |
| **11:45–12:15** | `seed_demo.py` + create vector index + fixture test | `pytest test_fixture` → 120 / 93 / tenant B values |
| **12:15–13:00** | Agent loop + tools + `runs` logging | Cold run answers 120 and logs 5–6 tool calls |
| **13:00–13:30** | Compressor + validator + embed + vector search + warm reuse | Warm run gives 120 with ≤2 tool calls. **Commit and push. This is a submittable project.** |
| 13:30–14:15 | Gate + revoke/expire + tenant isolation | Tenant B gets 0 foreign memories; revoked memory is blocked at once |
| 14:15–15:00 | Policy v2 path + version chain + naive arm + placebo + `comparison.json` | Naive gives 120 ❌ and a leak; MemoryDelta gives 93 ✅; 6 tests green |
| 15:00–15:30 | `demo.py --all` with clean terminal output (use `rich`), README (what/why/how to run/**what was built today**) | One command runs all 5 scenes in under 90 s |
| **15:30–16:00** | **Record the 1-minute video** (script in §11). Check that audio plays back. | Uploaded and link works in a private window |
| 16:00–16:30 | Optional P2 *or* fixes; rehearse the 3-min pitch twice out loud | Pitch fits in 3:00 |
| **16:30–16:40** | **Submit on Cerebral Valley:** repo link, video, description. Confirm no secrets in the repo. | Submission confirmed |
| 16:40–17:00 | Buffer. Don't start anything new. | |

**Checkpoint rule:** if the warm run isn't working by **13:45**, drop the LLM planner. Use a scripted planner with the same tools so the governance story still gets demoed. Say so honestly if asked.

---

## 10. Stack and repo layout

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| DB | Atlas sandbox via `pymongo[srv]` |
| Embeddings | Voyage `voyage-3.5` (1024-d) |
| Agent | one LLM SDK with tool calling, temperature 0, 4 bounded tools |
| Interface | CLI with `rich` (**no Streamlit, no dashboard**) |
| Tests | `pytest`; results in `results/comparison.json` |

```text
memorydelta/
├── README.md              # pitch, architecture diagram, run commands, results table, "built today" note
├── .env.example  .gitignore  requirements.txt
├── memorydelta/
│   ├── db.py              # client, collections, validator + index setup
│   ├── tools.py           # 4 bounded tools
│   ├── agent.py           # LLM tool loop, trace capture
│   ├── compressor.py      # trace → evidence-preserving memory
│   ├── store.py           # embed, insert, $vectorSearch, wait-until-searchable
│   ├── gate.py            # authorize / live / compatible / evidence; returns decision + reason
│   └── versions.py        # supersede, delta record
├── scripts/  seed_demo.py  demo.py   # demo.py --scene cold|warm|policy|tenant|revoke|all --arm naive|governed
├── tests/    test_fixture.py test_isolation.py test_revoke_expiry.py test_policy_change.py test_schema.py
└── results/  comparison.json
```

**Repo hygiene:** synthetic data only; `.env` in `.gitignore` from the first commit; `.env.example` has no real values.

---

## 11. Pitch, video and Q&A

### 1-minute video (record at 15:30)
- **0–8 s:** "Agents with memory have a new failure mode: they confidently reuse things they shouldn't." Show the title card.
- **8–20 s:** Cold run → compact memory stored in Atlas (show the doc in Atlas UI: evidence, policy_version, tenant_id).
- **20–30 s:** Warm run: 6 → 2 tool calls, with the trace diff on screen.
- **30–45 s:** Policy changes. Naive arm says **120 ❌**, MemoryDelta says **93 ✅**, and the gate prints the reason `policy_version mismatch`.
- **45–55 s:** Tenant B: naive leaks, governed shows 0. Revoke: blocked even though the index still returns it.
- **55–60 s:** Results table and tagline.

### 3-minute live demo
0:00 problem (20 s) → 0:20 cold + warm with trace diff (40 s) → 1:00 **policy change, naive vs governed side by side** (50 s) → 1:50 tenant B + revoke with the index-lag point (40 s) → 2:30 results table + placebo result + how it maps to the 3 questions (30 s).

### Map to the three official questions (put this in the README, and say it out loud)
| Question | Your answer |
|---|---|
| Stop memory leaking between clients | Identity comes from a trusted server-side session, never from the prompt. Atlas prefilter on `tenant_id`, then an `_id` recheck. Foreign memory text never reaches the LLM. Measured: 0 leaks. |
| Fix or drop wrong/expired memory | `correct_memory()` re-verifies and supersedes the old version. `revoke_memory()` revokes every version and removes the embedding and evidence text. Expiry is checked on every read, and TTL handles physical cleanup. `$jsonSchema` rejects memories without evidence. |
| Prove memory changed behavior | Action-trace diff (memory vs none) + placebo control (same-size irrelevant memory → no change) + stale-plan rejection, all recorded per run in `runs`. |

### Likely Q&A, with prepared answers
- *"Isn't this just RAG with filters?"* No. Retrieval only suggests candidates. The gate decides, using authority, liveness, dependency versions and evidence, and the agent's **actions** change, not just its prompt.
- *"What if the policy change isn't versioned?"* Out of scope today. Next step is a change stream on schema/policy collections that marks dependent memories for revalidation.
- *"Why not let the LLM decide if a memory is still valid?"* Because then unauthorized text has already reached the model. Validity is checked with deterministic code, before the prompt is built.
- *"Does it scale?"* Prefilter happens inside the vector index, the recheck is one `_id` lookup per candidate, and the gate is O(k).

---

## 12. Risks and fallbacks

| Risk | Early warning | Fallback |
|---|---|---|
| Atlas setup / network stalls | can't ping by 11:50 | phone hotspot; ask MongoDB staff right away |
| Vector index not working | empty results / errors | check index name, READY status, `numDimensions` 1024, filter fields declared |
| Index lag breaks warm run | warm run misses memory | poll until the `_id` appears (≤10 s) |
| LLM planner unstable | different tool paths per run | bounded tools + temp 0; at 13:45, switch to a scripted planner and say so |
| Compressor loses facts | warm run wrong | copy pipelines verbatim; reject if `evidence_complete` is false |
| Running out of time | P0 not done at 13:45 | cut per §7; video at 15:30 no matter what |
| Secret pushed to public repo | `.env` staged | `.gitignore` first; rotate the password if it leaks |

---

## 13. Submission checklist and definition of done

- [ ] Repo **public**; `.env` never committed (`git log -p | grep -i mongodb+srv` returns nothing)
- [ ] README says clearly that **everything was built Nov 14**, and lists third-party libraries
- [ ] 1-min video has **audio**, link opens logged out
- [ ] Description (≤3 sentences): "MemoryDelta is a governed memory runtime on MongoDB Atlas. Before a retrieved memory can steer an agent, MemoryDelta checks authorization, expiry, evidence and whether the rules it depends on have changed. It then proves the memory changed the agent's actions, not just its prompt."
- [ ] All teammates (if any) added · submitted by **16:30**

---

### Definition of done
One command (`python scripts/demo.py --all`) runs the five scenes against the Atlas sandbox and prints, for both arms: answer, correct?, tool calls, memory used + gate reason, foreign memories reaching the LLM. Tests pass. The video is submitted.

### The one result to prove
> **Can we cut the context cost of agent experience while keeping correct reuse, correct adaptation to change, and strict access control?**
> If there's time to prove only one thing: *the most relevant memory is sometimes the one the agent must refuse.*


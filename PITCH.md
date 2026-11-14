# MemoryDelta: demo-led pitch and video script

No slides. The live flow viewer (`python -m memorydelta.server`, http://localhost:8765) is the pitch.

**One line:** AI analysts that know which rule their memory was built for, governed by MongoDB Atlas.
**Hook:** A saved calculation is valid for a site, a framework and a year.
**The one story:** a sustainability analyst asks: *“What are our Scope 2 emissions from purchased electricity for FY2025-26?”*

**The numbers the demo runs live:**
- Acme Sydney plant uses 100,000 kWh in FY2025-26. With the 2024-25 NGER NSW factor (0.66 kg CO₂e/kWh), that's **66 t**. With the 2025-26 factor (0.64), it's **64 t**.
- Globex Newcastle warehouse: 250,000 kWh → **165 t**. Initech Parramatta lab: 40,000 kWh → **26.4 t**, and Initech has no saved memories.
- Electricity use is synthetic. The 0.66 and 0.64 factors are the Clean Energy Regulator's published NGER location-based Scope 2 factors for NSW.

## Submission description (Cerebral Valley)

> MemoryDelta gives AI analysts memory that knows which rule it was built for. A sustainability analyst asks for a site's FY2025-26 electricity emissions. When the 2025-26 NGER factor table replaces 2024-25, an AI that reuses its saved calculation reports 66 t instead of 64 t. MemoryDelta, built on MongoDB Atlas, stores each saved calculation with the factor version it depends on and retrieves candidates with Atlas Vector Search. Before anything reaches the model, it checks company, user, status, expiry, evidence and rule version in code. Memories can be corrected or revoked across every version, and every answer is verified against the calculation the agent actually ran. In 10 live hard cases on Atlas with GLM-4.6, MemoryDelta answered 10/10 with zero foreign or ineligible memories in the prompt; after the new factor table, both plain RAG memory and company-scoped memory without checks reported the stale 66 t, and plain RAG memory showed the model another company's memory in every case.

## 3-minute live demo (viewer only)

| Time | Viewer scene | Say |
|---|---|---|
| 0:00–0:20 | 0 · The question | “Hi, I'm Sepehr. A sustainability analyst asks: what are our Scope 2 emissions from purchased electricity for FY2025-26? An AI analyst with memory saves last year's calculation and reuses it. The trouble: a saved calculation is valid for a site, a framework and a year.” |
| 0:20–0:45 | 1 · Isolation | “Three companies ask the same question, live on Atlas. The company comes from the login, never the prompt. Each gets only its own saved calculation: Acme 66 tonnes, Globex 165. Initech has none and still answers, 26.4. The unscoped baseline leaks Globex's memory into Acme's prompt.” |
| 0:45–1:05 | 2 · Behavior | “Same question, same model, same data. Without memory the agent reads the factor and the usage schema first: four tool calls. With the approved memory it runs the verified calculation directly: two. Same answer. Memory changes what it does.” |
| 1:05–1:45 | 3 · New factor table | “Now the 2025-26 NGER factor table is published: NSW goes from 0.66 to 0.64. Company-scoped memory without checks reuses the saved calculation: 66 tonnes, wrong. MemoryDelta sees the memory was built on factor version 1 while version 2 is current, refuses it, recalculates: 64. Both found the same memory. Only one knew not to trust it.” |
| 1:45–2:05 | 4 · Correction → 5 · Revocation | “Correct the memory: it's re-checked against the current factor, saved as version 2, 64 tonnes, and only version 2 reaches the model. Revoke it: no version does, and the agent still answers by recalculating.” |
| 2:05–2:30 | 7 · Hard cases | “Ten hard cases, live, three strategies, same model. When the factor table changed, plain RAG memory and company-scoped memory both reported the stale 66 tonnes. Plain RAG memory also showed the model another company's memory in every case. MemoryDelta: ten for ten, and zero memories the model shouldn't have seen.” |
| 2:30–3:00 | 7 · Hard cases (stay) | “This is an open problem: a May 2026 benchmark, STALE, found the best model handles invalidated memories only 55% of the time. Other systems ask an LLM to notice, or let the newest fact win. MemoryDelta checks the rule version in code. Next: match each memory to the requested site, framework and year, so past reports reproduce exactly. MemoryDelta: memory that knows which rule it was built for. Thank you.” |

**Stage notes:** ← → switch scenes, Space replays a scene's animation. Don't click *Run live on Atlas* on stage: a full run takes about 2 minutes. The viewer replays the last saved live results, so it also works without a network connection.

## 1-minute video (viewer only; record it, then trim)

| Time | Screen | Voice-over |
|---|---|---|
| 0–10 s | Scene 0 | “An analyst asks for a site's FY2025-26 electricity emissions. A saved calculation is valid for a site, a framework and a year.” |
| 10–22 s | Scene 1 | “MemoryDelta on MongoDB Atlas: each company gets only its own saved calculation. The unscoped baseline leaks another company's memory.” |
| 22–40 s | Scene 3 | “The 2025-26 factor table is published. Memory without checks reuses last year's factor: 66 tonnes. MemoryDelta checks the factor version, refuses it, and recalculates 64.” |
| 40–50 s | Scenes 4 → 5 | “Correct it and only the new version is used. Revoke it and no version is.” |
| 50–60 s | Scene 7 | “Ten live hard cases: ten for ten, while both baselines reported the stale 66 tonnes. MemoryDelta: memory that knows which rule it was built for.” |

Before uploading, check that the audio plays, the link opens in a logged-out window, and the video is under 60 seconds.

## Likely questions

- **Why not ask the LLM whether a memory is still valid?** Then outdated or foreign text has already reached the model. And STALE shows models catch invalidated memories only about half the time. The check is deterministic code that runs before the prompt is built.
- **How is this different from Zep/Graphiti?** Zep invalidates old facts when an LLM detects a contradiction, and newer information wins. "Newest wins" can't reproduce last year's report, where 66 t is the right answer. MemoryDelta records the rule version a memory depends on and checks it in code at read time.
- **If the gate reads the current factor anyway, why keep memory?** The gate compares one version number, which is cheap. The memory holds the verified procedure: which data, which period, which calculation. In real reporting that's many steps; here, 4 tool calls become 2.
- **What if the data changes but the rule doesn't, e.g. a meter correction?** Memory stores the verified *calculation*, not the number. The agent re-runs it on current readings, so a corrected meter reading gives the new correct total, not the stored one.
- **Plain RAG memory still got 9 of 10 right. So what?** It reported the stale 66 t when the factor changed, and it was shown another company's memory in every case. That's a leak even when the number is right.
- **What if Atlas Agent Engine adds this?** Agent Engine stores, extracts and versions memory. Its preview docs don't check memory against the current business rule, revoke every version, or verify answers. MemoryDelta's rule-version check can sit in front of it; we're a complement, not a competitor.
- **Does it scale?** The company filter runs inside the vector index. The re-read is one `_id` lookup per candidate, and the gate is O(k).

## Limitations and open questions (say these honestly if asked)

- **Rules must be versioned.** The check works when the rule a memory depends on is an explicit, versioned record. Emission factor tables are, which is why the use case fits. Rules that change implicitly (schema drift, a definition nobody versioned) are the open problem STALE measures.
- **Request-context matching isn't built.** Today the gate compares a memory with the company's *current* factor version. Reproducing a past report needs it to compare with the factor for the *requested* site, framework and year.
- **Prototype scope.** One task, three companies, 10 hard cases, one run each, one model (GLM-4.6). That's enough to show the failure modes, not to make a statistical claim.
- **Expired case.** If the expired memory is already gone from search in a live run, Atlas TTL most likely removed it. A deterministic test covers that gate path.
- **The Agent Engine comparison** is based on its public-preview documentation as of Nov 14, 2026.

## Sources

- Clean Energy Regulator, NGER location-based Scope 2 factors (NSW 0.66 for 2024-25, 0.64 for 2025-26): https://cer.gov.au/schemes/national-greenhouse-and-energy-reporting-scheme/report-emissions-and-energy/amendments-to-national-greenhouse-and-energy-reporting-legislation
- Chao et al., “STALE: Can LLM Agents Know When Their Memories Are No Longer Valid?”, arXiv 2605.06527 (May 2026). The best evaluated model scored 55.2%: https://arxiv.org/abs/2605.06527
- Hu, Wang, McAuley, “Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions” (MemoryAgentBench), arXiv 2507.05257: https://arxiv.org/abs/2507.05257
- Rasmussen et al., “Zep: A Temporal Knowledge Graph Architecture for Agent Memory”, arXiv 2501.13956: https://arxiv.org/abs/2501.13956
- MongoDB Atlas Agent Engine docs (public preview): https://www.mongodb.com/docs/agentengine/add-features/memory-types/

# MemoryDelta: pitch, demo and video scripts

**One line:** Agent memory you can trust, governed by MongoDB Atlas.
**Hook:** Same data, different rule, different number. A memory must know which rule it was built for.
**Origin (say it as motivation, not as prior work):** my master’s research on live video, where a stream can look slow even when the connection is fine, depending on what you measure.

## Submission description (Cerebral Valley)

> MemoryDelta is a governed memory layer for AI agents built on MongoDB Atlas. Atlas Vector Search proposes memories, and a validation gate checks tenant, user, status, expiry, evidence, and whether the business rule the memory was built on is still the live one, before anything reaches the model. Memories can be corrected (versioned, verified, delta recorded) and revoked across every version, answers must match the query the agent actually ran, and every decision is audited in Atlas. In 10 live hard cases on Atlas, MemoryDelta answered 10/10 with zero foreign or ineligible memories in the prompt; plain RAG memory exposed another tenant’s memory in all 10 cases, and a tenant-scoped store without checks exposed ineligible memories 4 times and gave one stale answer.

## 3-minute live pitch

| Time | Show | Say |
|---|---|---|
| 0:00–0:20 | Slide 1, cover | “Hi, I’m Sepehr. In my master’s research on live video, I learned that a stream can look slow even when the connection is fine. It depends on what you measure: same data, different rule, different number. AI agents with memory make the same mistake. They save how they calculated something, and keep reusing it after the rule changes.” |
| 0:20–0:35 | Slide 2, problem | “Stale rules are the big failure. Memory can also leak another customer’s data, and nobody can prove which memory shaped an answer. Vector search finds what is similar. It doesn’t know which rule a memory was built for. MemoryDelta does, on MongoDB Atlas.” |
| 0:35–1:00 | Slide 3, 120 vs 93 | “Here it is live on Atlas. Finance changed what ‘active customer’ means. The old saved calculation says 120 active customers. Under today’s rule, it’s 93. Plain memory replays the old calculation: 120, wrong. MemoryDelta checks which rule the saved calculation was built for, sees v1 while the live rule is v2, refuses it, and answers 93. Both found the same memory. Only one knew not to trust it.” |
| 1:00–1:25 | Slide 4, flow | “Identity comes from the session, never the prompt. Atlas Vector Search with Voyage embeddings proposes candidates for that tenant. We re-read each from the collection, because indexes lag. Then six checks in code. The answer must match the query the agent ran, and every decision is logged in Atlas.” |
| 1:25–2:15 | Flow viewer (localhost:8765), scenes 1 → 3 → 4 → 5 | Scene 1: “Three companies, same question: each gets only its own memory; the unscoped baseline leaks.” Scene 3: “Change the rule: red 120 without the gate, green 93 with it.” Scenes 4–5: “Correct it: only v2 reaches the model. Revoke it: nothing does, and the agent still answers.” |
| 2:15–2:35 | Slide 8, scorecard | “Ten hard cases, live, three strategies. Plain RAG memory got the numbers right but showed the model another company’s memory every time. Tenant-scoped memory with no checks showed it stale or private memories four times and gave one wrong answer. MemoryDelta: ten for ten, zero bad memories in the prompt, 41% fewer tokens than plain RAG memory.” |
| 2:35–2:50 | Slide 10, Agent Engine | “MongoDB’s new Agent Engine remembers and versions memory. Its docs don’t check memory against today’s business rules, revoke every version, or verify answers. MemoryDelta does, and can sit in front of it.” |
| 2:50–3:00 | Slide 13, close | “Next: Change Streams to flag memories the moment a rule changes. MemoryDelta: compress the past, track the changes, trust the memory. Thank you.” |

**Fallback if the network fails:** the flow viewer replays the last saved live results (`results/comparison.json`), so it works offline. Don’t click *Run live* on stage; a full run takes about 2 minutes.

## 1-minute video (record it, then trim)

| Time | Screen | Voice-over |
|---|---|---|
| 0–8 s | Slide 1 → slide 2 | “Same data, different rule, different number. AI agents with memory keep reusing an old calculation after the rule changes.” |
| 8–20 s | Viewer, scene 1 | “MemoryDelta scopes every memory to the logged-in tenant. Here the unscoped baseline leaks another company’s memory; MemoryDelta sends only the right one.” |
| 20–35 s | Viewer, scene 3 | “The rule changes. The old saved calculation says 120; today’s rule gives 93. Without the gate, the agent reuses the old one. MemoryDelta checks which rule it was built for, refuses it, and answers 93.” |
| 35–47 s | Viewer, scenes 4 and 5 | “Correct a memory and only the new version is used. Revoke it and no version is.” |
| 47–55 s | Slide 8, scorecard | “Ten live hard cases: ten for ten, and zero memories the model shouldn’t have seen.” |
| 55–60 s | Slide 13 | “MemoryDelta, on MongoDB Atlas. Trust the memory.” |

Check before uploading: audio plays, the link opens in a logged-out window, the video is under 60 seconds.

## Likely questions

- **Isn’t this just RAG with filters?** Retrieval only proposes. The gate decides from tenant, user, status, expiry, evidence and the policy version the memory depends on, and the agent’s answer is verified against the query it ran.
- **Why not ask the LLM if a memory is still valid?** Then unauthorized text has already reached the model. The check is deterministic code, before the prompt is built.
- **What if Atlas Agent Engine adds this?** Then the policy-version check plugs into it. We’re a pattern on Atlas primitives, not a competitor.
- **How do you know memory changed behavior?** Same task, model and data with memory on and off: 4 tool calls vs 2, same answer. Expected outcomes are written down before each run.
- **Does it scale?** The tenant filter runs inside the vector index; the re-read is one `_id` lookup per candidate; the gate is O(k).
- **What if the policy change isn’t versioned?** Out of scope today. Next step: Change Streams on policy collections mark dependent memories for revalidation.
- **Plain RAG memory also got 10/10. So what?** The model re-checked and got lucky, but it was shown another company’s memory in every case. That’s a data leak even when the number is right. Accuracy alone hides the risk.
- **What about the expired case?** Atlas TTL had already deleted that memory before its run, so the live case didn’t exercise the gate; `test_expired_memory_blocked_before_ttl` covers it deterministically.
- **Is the naive baseline a strawman?** We report two baselines: plain RAG memory (no scoping) and a tenant-scoped store with no validity checks, the way most memory stores scope today. Same model and data for all three.

"""Agent loop: governed memory selection -> model with bounded tools -> structured decision."""
import json
import time
import uuid
from dataclasses import dataclass, field

from .compress import estimate_tokens, render_compact
from .config import RequestContext, now
from .model import RecordingModel, _filter_from_memory
from .tools import TOOL_SPECS, Tools

SYSTEM = """You are an analytics agent for a retail company.
Your tools act only on the current tenant's data; you never choose the tenant.
Approved memories, if any, appear inside <approved_memories> in the user message. The platform has \
already checked each one: it belongs to this tenant, is active, unexpired, and matches the current \
policy version. If an approved memory covers the task, reuse its verified filter directly with \
run_count_query instead of re-reading the policy and schema. If no approved memory covers the task, \
call get_policy and inspect_collection before counting.
Finish by calling submit_answer exactly once."""


@dataclass
class RunResult:
    run_id: str
    tenant_id: str
    memory_mode: str
    included: list = field(default_factory=list)   # [{_id, memory_id, version}] actually sent
    rejected: list = field(default_factory=list)   # [{_id, memory_id, version, reason}]
    candidates: int = 0
    tool_calls: list = field(default_factory=list)  # [{name, input, ok}] actually executed
    decision: dict | None = None
    decision_evidence: dict | None = None   # what the answer was verified against (trusted run state)
    first_input_tokens: int | None = None
    total_input_tokens: int | None = None
    token_source: str = "estimate"
    model: str = ""
    payloads: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    stop_reason: str | None = None

    @property
    def first_action(self):
        return self.tool_calls[0]["name"] if self.tool_calls else None


def build_user_message(task, rendered_memories):
    block = json.dumps(rendered_memories, indent=1, sort_keys=True)
    note = "" if rendered_memories else "\nNo approved memory is available for this task."
    return f"Task: {task}\n<approved_memories>\n{block}\n</approved_memories>{note}"


def run_agent(store, model, ctx: RequestContext, task, memory_mode="governed", scenario="",
              rendered_override=None, max_steps=8, log_run=True):
    """rendered_override: pass pre-rendered memories (used only by the compression comparison,
    with evidence from this tenant's own authorized run)."""
    rec = model if isinstance(model, RecordingModel) else RecordingModel(model)
    run_id = f"run_{uuid.uuid4().hex[:10]}"
    res = RunResult(run_id=run_id, tenant_id=ctx.tenant_id, memory_mode=memory_mode, model=rec.name)

    if rendered_override is not None:
        res.memory_mode = "provided"   # handed in directly, no retrieval (compression comparison only)
        rendered = rendered_override
        evidence = [dict(zip(("filter", "policy_version"), _filter_from_memory(m)), memory_id=m["memory_id"])
                    for m in rendered]
        res.included = [{"_id": f"{m['memory_id']}@v{m['version']}", "memory_id": m["memory_id"],
                         "version": m["version"]} for m in rendered]
    else:
        sel = store.select(ctx, task, mode=memory_mode)
        rendered = [render_compact(d) for d in sel.included]
        evidence = [{"memory_id": d["memory_id"], "policy_version": d["content"]["dependencies"]["policy_version"],
                     "filter": json.loads(d["content"]["critical_evidence"]["verified_filter_json"])}
                    for d in sel.included]
        res.included = [{"_id": d["_id"], "memory_id": d["memory_id"], "version": d["version"]}
                        for d in sel.included]
        res.rejected, res.candidates = sel.rejected, len(sel.candidates)

    start = len(rec.payloads)
    tools = Tools(store.db, ctx, memory_evidence=evidence)
    messages = [{"role": "user", "content": build_user_message(task, rendered)}]
    total, first, used_api = 0, None, False
    t0 = time.time()
    for _ in range(max_steps):
        n_before = len(rec.payloads)
        resp = rec.create(system=SYSTEM, tools=TOOL_SPECS, messages=messages)
        usage = getattr(resp, "usage", None)
        if usage is not None and getattr(usage, "input_tokens", None) is not None:
            used_api = True
            n = usage.input_tokens + (getattr(usage, "cache_read_input_tokens", 0) or 0) + \
                (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        else:
            n = estimate_tokens(rec.payloads[n_before])
        first = n if first is None else first
        total += n
        res.stop_reason = resp.stop_reason
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if not uses:
            break
        results, done = [], False
        for b in uses:
            out = tools.call(b.name, b.input)
            if b.name == "submit_answer" and tools.calls[-1]["ok"]:
                res.decision, done = dict(b.input), True
                res.decision_evidence = out["verified_against"]
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, default=str),
                            **({} if tools.calls[-1]["ok"] else {"is_error": True})})
        messages.append({"role": "user", "content": results})
        if done:
            break

    res.trace = tools.calls
    res.tool_calls = [{"name": c["name"], "input": c["input"], "ok": c["ok"]} for c in tools.calls]
    res.first_input_tokens, res.total_input_tokens = first, total
    res.token_source = "api_usage" if used_api else "estimate"
    res.payloads = rec.payloads[start:]   # only this run's requests
    if log_run:
        store.db.runs.insert_one({
            "_id": run_id, "kind": "agent_run", "scenario": scenario, "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id, "memory_mode": memory_mode, "model": rec.name,
            "included": res.included, "rejected": res.rejected, "candidates": res.candidates,
            "tool_calls": res.trace, "decision": res.decision,
            "decision_evidence": res.decision_evidence, "stop_reason": res.stop_reason,
            "first_input_tokens": first, "total_input_tokens": total, "token_source": res.token_source,
            "latency_ms": int((time.time() - t0) * 1000), "created_at": now()})
    return res

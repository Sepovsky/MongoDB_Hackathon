"""Model clients. AnthropicModel is the live client; ScriptedModel is a deterministic test double."""
import copy
import itertools
import json
import os
import re
from types import SimpleNamespace

from .config import MODEL

MEM_RE = re.compile(r"<approved_memories>\s*(.*?)\s*</approved_memories>", re.S)


def memories_in_payload(payload):
    """Parse the memory block out of the first user message of a captured payload."""
    first = payload["messages"][0]["content"]
    text = first if isinstance(first, str) else " ".join(
        b.get("text", "") for b in first if isinstance(b, dict))
    m = MEM_RE.search(text)
    return json.loads(m.group(1)) if m else []


def _plain(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(exclude_none=True)
    if isinstance(obj, SimpleNamespace):
        return {k: _plain(v) for k, v in vars(obj).items()}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


class AnthropicModel:
    live = True

    def __init__(self, model=MODEL, effort="low"):
        import anthropic
        # Explicit base_url so a stray ANTHROPIC_BASE_URL in a dev shell doesn't redirect calls.
        self.client = anthropic.Anthropic(
            base_url=os.getenv("MEMORYDELTA_ANTHROPIC_BASE_URL", "https://api.anthropic.com"))
        self.name, self.effort = model, effort

    def create(self, **payload):
        # Fixed settings across every arm: same model, effort, tools and system prompt.
        # Server-side fallbacks re-run a declined request on another model inside the same call.
        return self.client.beta.messages.create(
            model=self.name, max_tokens=4096, output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **payload)


class ScriptedModel:
    """Deterministic stand-in so the harness runs without an API key. Uses a memory's verified filter
    when one is provided, otherwise discovers the policy first. NOT evidence of model behavior."""
    live = False
    name = "scripted-test-double"
    _ids = itertools.count(1)

    def create(self, **payload):
        msgs = payload["messages"]
        mems = memories_in_payload(payload)
        step = sum(1 for m in msgs if m["role"] == "assistant")
        results = [json.loads(b["content"]) for m in msgs if m["role"] == "user" and isinstance(m["content"], list)
                   for b in m["content"] if b.get("type") == "tool_result"]
        if mems:
            m = mems[0]
            flt, ver = _filter_from_memory(m)
            plan = [("run_count_query", {"filter": flt})]
            final = lambda: {"active_customers": results[-1]["active_customers"], "filter_used": flt,
                             "policy_version": ver, "memory_ids_used": [m["memory_id"]]}
        else:
            plan = [("get_policy", {"metric": "active_customers"}), ("inspect_collection", {"collection": "orders"})]
            if len(results) >= 1:
                plan.append(("run_count_query", {"filter": results[0]["filter"]}))
            final = lambda: {"active_customers": results[-1]["active_customers"],
                             "filter_used": results[0]["filter"], "policy_version": results[0]["version"],
                             "memory_ids_used": []}
        if step < len(plan):
            name, args = plan[step]
        else:
            name, args = "submit_answer", final()
        block = SimpleNamespace(type="tool_use", id=f"toolu_{next(self._ids)}", name=name, input=args)
        return SimpleNamespace(content=[block], stop_reason="tool_use", usage=None, model=self.name)


def _filter_from_memory(m):
    if "verified_filter" in m:
        return m["verified_filter"], m.get("policy_version")
    calls = m["raw_trajectory"]["tool_calls"]
    flt = [c for c in calls if c["name"] == "run_count_query" and c["ok"]][-1]["input"]["filter"]
    pol = [c for c in calls if c["name"] == "get_policy" and c["ok"]]
    return flt, (pol[-1]["output"]["version"] if pol else None)


class RecordingModel:
    """Wraps any model client and keeps a JSON copy of every payload actually sent."""

    def __init__(self, inner):
        self.inner, self.payloads = inner, []
        self.live, self.name = inner.live, inner.name

    def create(self, **payload):
        self.payloads.append(copy.deepcopy(_plain(payload)))
        return self.inner.create(**payload)


class ZaiModel:
    """Z.ai GLM through its OpenAI-compatible endpoint. Translates the agent's Anthropic-shaped
    request/response to chat-completions format so the agent loop, tools and gate stay identical."""
    live = True

    def __init__(self, api_key=None, base_url=None, model=None, client=None):
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=api_key or os.environ["ZAI_API_KEY"],
                            base_url=base_url or os.getenv("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4"))
        self.client = client
        self.name = model or os.getenv("ZAI_MODEL", "glm-4.6")
        temp = os.getenv("ZAI_TEMPERATURE")
        self.extra = {"temperature": float(temp)} if temp else {}
        self.last_wire_payload = None   # the exact chat-completions body of the latest request

    def create(self, system, tools, messages):
        wire = {"model": self.name, "messages": to_openai_messages(system, messages),
                "tools": [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                            "parameters": t["input_schema"]}} for t in tools],
                "tool_choice": "auto", **self.extra}
        self.last_wire_payload = copy.deepcopy(wire)
        resp = self.client.chat.completions.create(**wire)
        return from_openai_response(resp, self.name)


def to_openai_messages(system, messages):
    out = [{"role": "system", "content": system}]
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
        elif m["role"] == "assistant":
            text = "".join(getattr(b, "text", "") or "" for b in c if getattr(b, "type", None) == "text")
            calls = [{"id": b.id, "type": "function",
                      "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                     for b in c if getattr(b, "type", None) == "tool_use"]
            out.append({"role": "assistant", "content": text or None, **({"tool_calls": calls} if calls else {})})
        else:  # user turn carrying tool results
            for b in c:
                if b.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
    return out


def from_openai_response(resp, model_name):
    msg = resp.choices[0].message
    blocks = []
    if msg.content:
        blocks.append(SimpleNamespace(type="text", text=msg.content))
    for tc in msg.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {"_invalid_json": tc.function.arguments}   # tool layer rejects it as an error
        blocks.append(SimpleNamespace(type="tool_use", id=tc.id, name=tc.function.name, input=args))
    usage = getattr(resp, "usage", None)
    return SimpleNamespace(
        content=blocks, model=model_name,
        stop_reason="tool_use" if msg.tool_calls else "end_turn",
        usage=SimpleNamespace(input_tokens=usage.prompt_tokens) if usage and usage.prompt_tokens else None)


def default_model():
    """MEMORYDELTA_PROVIDER = anthropic | zai | scripted. If unset: whichever key is configured."""
    from .config import anthropic_configured
    provider = os.getenv("MEMORYDELTA_PROVIDER", "").lower()
    if not provider:
        provider = "anthropic" if anthropic_configured() else "zai" if os.getenv("ZAI_API_KEY") else "scripted"
    return {"anthropic": AnthropicModel, "zai": ZaiModel, "scripted": ScriptedModel}[provider]()

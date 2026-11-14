"""Evidence-preserving compression: raw agent trace -> compact memory content.
Deterministic. Machine-critical values (filters, IDs, versions) are copied verbatim."""
import json
import re

from .config import METRIC


def compress(trace, policy, run_id):
    """trace: list of executed tool calls. policy: the authoritative policy doc at run time."""
    counts = [c for c in trace if c["name"] == "run_count_query" and c["ok"]]
    if not counts:
        raise ValueError("no successful run_count_query in trace; nothing verified to remember")
    final = counts[-1]  # the query the answer depended on; earlier attempts are dropped
    flt = final["input"]["filter"]
    return {
        "goal": "count_active_customers",
        "plan": [f"read policy {policy['_id']} (v{policy['version']})",
                 "count distinct customer_id in orders for 2026-10 using verified_filter"],
        "critical_evidence": {
            "collection": "orders",
            "fields": sorted({"customer_id", "order_date", *flt.keys()}),
            "verified_filter_json": json.dumps(flt, sort_keys=True),
            "result": final["output"]["active_customers"],
            "source_ids": [policy["_id"], f"run:{run_id}"],
        },
        "assumptions": ["window = 2026-10-01..2026-10-31 UTC", "count distinct customer_id"],
        "dependencies": {"policy_key": METRIC, "policy_version": policy["version"],
                         "policy_doc_id": policy["_id"]},
    }


def render_compact(doc):
    """What the model sees for an eligible memory."""
    c = doc["content"]
    ev = c["critical_evidence"]
    return {"memory_id": doc["memory_id"], "version": doc["version"], "goal": c["goal"],
            "plan": c["plan"], "verified_filter": json.loads(ev["verified_filter_json"]),
            "verified_result": ev.get("result"), "policy_version": c["dependencies"]["policy_version"],
            "source_ids": ev.get("source_ids", []), "assumptions": c.get("assumptions", [])}


def render_raw(memory_id, version, trace, narration):
    """Uncompressed baseline: the full trajectory, including chatter and every tool output."""
    return {"memory_id": memory_id, "version": version, "raw_trajectory": {
        "narration": narration, "tool_calls": trace}}


def evidence_pointers(rendered):
    """Source IDs a representation cites, so we can check they resolve in Atlas."""
    if "source_ids" in rendered:
        return list(rendered["source_ids"])
    text = json.dumps(rendered, default=str)
    return sorted(set(re.findall(r'"policy_id": "([^"]+)"', text)))


def estimate_tokens(obj):
    """Rough estimate (~4 chars/token). Labelled as an estimate wherever shown."""
    return max(1, len(json.dumps(obj, default=str)) // 4)

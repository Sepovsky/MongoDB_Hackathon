"""Evidence-preserving compression: raw agent trace -> compact memory content.
Deterministic. Machine-critical values (filters, IDs, versions) are copied verbatim."""
import json
import re

from .config import METRIC, PERIOD
from .tools import CALC


def compress(trace, policy, run_id):
    """trace: list of executed tool calls. policy: the authoritative policy doc at run time."""
    calcs = [c for c in trace if c["name"] == CALC and c["ok"]]
    if not calcs:
        raise ValueError(f"no successful {CALC} in trace; nothing verified to remember")
    final = calcs[-1]  # the calculation the answer depended on; earlier attempts are dropped
    flt = dict(final["input"])  # calculation parameters, e.g. {"factor": 0.66}
    return {
        "goal": "scope2_electricity_emissions",
        "plan": [f"read emission factor {policy['_id']} (v{policy['version']}, {policy['framework']} "
                 f"{policy['region']} {policy['period']})",
                 f"sum site kWh for {PERIOD} and multiply by the verified factor"],
        "critical_evidence": {
            "collection": "usage",
            "fields": ["kwh", "month", "site_id"],
            "verified_filter_json": json.dumps(flt, sort_keys=True),
            "result": final["output"]["emissions_t"],
            "kwh": final["output"]["kwh"],
            "source_ids": [policy["_id"], f"run:{run_id}"],
        },
        "assumptions": [f"reporting period {PERIOD} = 1 Jul 2025 to 30 Jun 2026",
                        "location-based Scope 2, kg CO2e/kWh, result in tonnes"],
        "dependencies": {"policy_key": METRIC, "policy_version": policy["version"],
                         "policy_doc_id": policy["_id"]},
    }


def render_compact(doc):
    """What the model sees for an eligible memory."""
    c = doc["content"]
    ev = c["critical_evidence"]
    return {"memory_id": doc["memory_id"], "version": doc["version"], "goal": c["goal"],
            "plan": c["plan"], "verified_params": json.loads(ev["verified_filter_json"]),
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

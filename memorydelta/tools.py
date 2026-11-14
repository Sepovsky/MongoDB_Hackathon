"""Bounded tools. The tenant always comes from the trusted RequestContext, never from tool args."""
import json

from .config import METRIC, WINDOW_END, WINDOW_START, RequestContext

ALLOWED_FIELDS = {"status": str, "refunded": bool, "total": (int, float)}
ALLOWED_OPS = {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in"}


class ToolError(ValueError):
    pass


def canonical_filter(f):
    """Order-independent form for comparing filters."""
    return json.dumps(f, sort_keys=True)


def validate_filter(f):
    if not isinstance(f, dict):
        raise ToolError("filter must be an object")
    for field, cond in f.items():
        if field not in ALLOWED_FIELDS:
            raise ToolError(f"field {field!r} not allowed; allowed: {sorted(ALLOWED_FIELDS)}")
        if isinstance(cond, dict):
            for op in cond:
                if op not in ALLOWED_OPS:
                    raise ToolError(f"operator {op!r} not allowed")
    return f


TOOL_SPECS = [
    {
        "name": "get_policy",
        "description": "Read the current authoritative definition of a business metric for this tenant, "
                       "including its version and the order filter that implements it.",
        "input_schema": {"type": "object", "properties": {
            "metric": {"type": "string", "enum": [METRIC]}}, "required": ["metric"]},
    },
    {
        "name": "inspect_collection",
        "description": "Show the fields, types and two sample documents of the orders collection.",
        "input_schema": {"type": "object", "properties": {
            "collection": {"type": "string", "enum": ["orders"]}}, "required": ["collection"]},
    },
    {
        "name": "run_count_query",
        "description": "Count distinct customer_id in this tenant's orders for October 2026 that match "
                       "`filter`. Allowed fields: status (str), refunded (bool), total (number). "
                       "Allowed operators: $eq $ne $gt $gte $lt $lte $in. Use {} for no extra filter.",
        "input_schema": {"type": "object", "properties": {
            "filter": {"type": "object"}}, "required": ["filter"]},
    },
    {
        "name": "submit_answer",
        "description": "Submit the final structured answer. Call exactly once, at the end.",
        "input_schema": {"type": "object", "properties": {
            "active_customers": {"type": "integer"},
            "filter_used": {"type": "object"},
            "policy_version": {"type": ["integer", "null"]},
            "memory_ids_used": {"type": "array", "items": {"type": "string"}},
        }, "required": ["active_customers", "filter_used", "policy_version", "memory_ids_used"]},
    },
]


class Tools:
    def __init__(self, db, ctx: RequestContext, memory_evidence=None):
        self.db, self.ctx = db, ctx
        self.calls = []  # actual executed calls: name, input, output
        # Trusted, set by the application: memories actually placed in this run's request,
        # each as {memory_id, policy_version, filter}. Used to validate answer metadata.
        self.memory_evidence = memory_evidence or []

    def current_policy(self):
        return self.db.policies.find_one(
            {"tenant_id": self.ctx.tenant_id, "metric": METRIC, "active": True})

    def call(self, name, args):
        try:
            fn = getattr(self, f"_{name}", None)
            if fn is None:
                raise ToolError(f"unknown tool {name!r}")
            out = fn(**(args or {}))
            ok = True
        except (ToolError, TypeError) as e:
            out, ok = {"error": str(e)}, False
        self.calls.append({"name": name, "input": args, "output": out, "ok": ok})
        return out

    def _get_policy(self, metric=METRIC):
        p = self.current_policy()
        if not p:
            raise ToolError(f"no active policy for {metric}")
        return {"policy_id": p["_id"], "metric": p["metric"], "version": p["version"],
                "definition": p["definition"], "filter": json.loads(p["filter_json"])}

    def _inspect_collection(self, collection="orders"):
        q = {"tenant_id": self.ctx.tenant_id}
        sample = list(self.db.orders.find(q, {"_id": 0, "tenant_id": 0}).limit(2))
        for s in sample:
            s["order_date"] = s["order_date"].isoformat()
        return {"collection": "orders",
                "fields": {"order_id": "str", "customer_id": "str", "order_date": "datetime",
                           "status": "str: paid|pending", "refunded": "bool", "total": "number"},
                "sample": sample,
                "documents": self.db.orders.count_documents(q)}

    def _run_count_query(self, filter):
        validate_filter(filter)
        q = dict(filter)
        q["tenant_id"] = self.ctx.tenant_id          # injected server-side, after validation
        q["order_date"] = {"$gte": WINDOW_START, "$lt": WINDOW_END}
        n = len(self.db.orders.distinct("customer_id", q))
        return {"active_customers": n, "filter": filter, "window": "2026-10"}

    def _submit_answer(self, active_customers, filter_used, policy_version=None, memory_ids_used=None):
        """The answer must match a successful count executed in this same run, and its metadata must
        match trusted run state: memory IDs that were actually provided, and a policy version that a
        get_policy result or a cited memory in this run ties to the submitted filter."""
        counts = [c for c in self.calls if c["name"] == "run_count_query" and c["ok"]]
        if not counts:
            raise ToolError("submit_answer requires a successful run_count_query earlier in this run")
        last = counts[-1]
        if canonical_filter(filter_used) != canonical_filter(last["input"]["filter"]):
            raise ToolError(f"filter_used {filter_used} does not match the last executed query filter "
                            f"{last['input']['filter']}")
        if active_customers != last["output"]["active_customers"]:
            raise ToolError(f"active_customers {active_customers} does not match the last executed count "
                            f"{last['output']['active_customers']}")

        memory_ids_used = list(memory_ids_used or [])
        provided = {m["memory_id"]: m for m in self.memory_evidence}
        unknown = [m for m in memory_ids_used if m not in provided]
        if unknown:
            raise ToolError(f"memory_ids_used contains IDs not provided in this run: {unknown}; "
                            f"provided: {sorted(provided)}")

        # Evidence that ties a policy version to a filter: get_policy results, and cited memories.
        evidence = [(c["output"]["version"], c["output"]["filter"], "get_policy")
                    for c in self.calls if c["name"] == "get_policy" and c["ok"]]
        evidence += [(provided[m]["policy_version"], provided[m]["filter"], f"memory:{m}") for m in memory_ids_used]
        if not evidence:
            if policy_version is not None:
                raise ToolError("policy_version must be null when no get_policy result or cited memory "
                                "establishes it in this run")
            source = None
        else:
            match = [src for ver, flt, src in evidence
                     if ver == policy_version and canonical_filter(flt) == canonical_filter(filter_used)]
            if not match:
                seen = sorted({f"v{ver} {canonical_filter(flt)} ({src})" for ver, flt, src in evidence})
                raise ToolError(f"policy_version {policy_version} with filter {filter_used} is not supported by "
                                f"this run's evidence: {seen}")
            source = match[0]
        return {"accepted": True, "verified_against": {
            "filter": last["input"]["filter"], "active_customers": active_customers,
            "policy_version": policy_version, "policy_version_source": source,
            "memory_ids_used": memory_ids_used}}

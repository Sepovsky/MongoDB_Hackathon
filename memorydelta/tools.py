"""Bounded tools for a Scope 2 electricity-emissions analyst. The company (tenant) always comes from the
trusted RequestContext, never from tool args. A calculation's parameters are {"factor": <kg CO2e/kWh>}."""
import json

from .config import METRIC, PERIOD, WINDOW_END, WINDOW_START, RequestContext

CALC = "calculate_emissions"


class ToolError(ValueError):
    pass


def canonical_filter(f):
    """Order-independent form for comparing calculation parameters, e.g. {"factor": 0.64}."""
    return json.dumps(f, sort_keys=True)


def validate_params(p):
    if not isinstance(p, dict) or set(p) != {"factor"}:
        raise ToolError('parameters must be exactly {"factor": <number>}')
    f = p["factor"]
    if isinstance(f, bool) or not isinstance(f, (int, float)) or not 0 < f < 5:
        raise ToolError("factor must be a number of kg CO2e/kWh between 0 and 5")
    return p


TOOL_SPECS = [
    {
        "name": "get_emission_factor",
        "description": "Read this company's current authoritative emission factor for purchased electricity "
                       "(location-based Scope 2): framework, region, reporting period, version and factor "
                       "in kg CO2e/kWh.",
        "input_schema": {"type": "object", "properties": {
            "metric": {"type": "string", "enum": [METRIC]}}, "required": ["metric"]},
    },
    {
        "name": "inspect_usage",
        "description": "Show this company's site, the fields of its monthly electricity readings and two samples.",
        "input_schema": {"type": "object", "properties": {
            "collection": {"type": "string", "enum": ["usage"]}}, "required": ["collection"]},
    },
    {
        "name": CALC,
        "description": f"Sum this company's site electricity use (kWh) for {PERIOD} (1 Jul 2025 to 30 Jun 2026) "
                       "and multiply by `factor` (kg CO2e/kWh). Returns kWh and tonnes CO2e.",
        "input_schema": {"type": "object", "properties": {
            "factor": {"type": "number"}}, "required": ["factor"]},
    },
    {
        "name": "submit_answer",
        "description": "Submit the final structured answer. Call exactly once, at the end.",
        "input_schema": {"type": "object", "properties": {
            "emissions_t": {"type": "number"},
            "factor_used": {"type": "number"},
            "policy_version": {"type": ["integer", "null"]},
            "memory_ids_used": {"type": "array", "items": {"type": "string"}},
        }, "required": ["emissions_t", "factor_used", "policy_version", "memory_ids_used"]},
    },
]


class Tools:
    def __init__(self, db, ctx: RequestContext, memory_evidence=None):
        self.db, self.ctx = db, ctx
        self.calls = []  # actual executed calls: name, input, output
        # Trusted, set by the application: memories actually placed in this run's request,
        # each as {memory_id, policy_version, filter} where filter = the calculation parameters.
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

    def _get_emission_factor(self, metric=METRIC):
        p = self.current_policy()
        if not p:
            raise ToolError(f"no active emission factor for {metric}")
        return {"policy_id": p["_id"], "metric": p["metric"], "version": p["version"],
                "framework": p["framework"], "region": p["region"], "period": p["period"],
                "factor": json.loads(p["filter_json"])["factor"], "definition": p["definition"]}

    def _inspect_usage(self, collection="usage"):
        q = {"tenant_id": self.ctx.tenant_id}
        sample = list(self.db.usage.find(q, {"_id": 0, "tenant_id": 0}).limit(2))
        for s in sample:
            s["month"] = s["month"].isoformat()[:7]
        site = sample[0]["site_name"] if sample else None
        return {"collection": "usage", "site": site,
                "fields": {"site_id": "str", "site_name": "str", "region": "str", "month": "date",
                           "kwh": "number (electricity purchased that month)"},
                "sample": sample, "readings": self.db.usage.count_documents(q)}

    def _calculate_emissions(self, factor):
        validate_params({"factor": factor})
        q = {"tenant_id": self.ctx.tenant_id,          # injected server-side
             "month": {"$gte": WINDOW_START, "$lt": WINDOW_END}}
        kwh = sum(d["kwh"] for d in self.db.usage.find(q, {"kwh": 1}))
        return {"emissions_t": round(kwh * factor / 1000, 3), "kwh": kwh, "factor": factor, "period": PERIOD}

    def _submit_answer(self, emissions_t, factor_used, policy_version=None, memory_ids_used=None):
        """The answer must match a successful calculation executed in this same run, and its metadata must
        match trusted run state: memory IDs that were actually provided, and a policy version that a
        get_emission_factor result or a cited memory in this run ties to the submitted factor."""
        calcs = [c for c in self.calls if c["name"] == CALC and c["ok"]]
        if not calcs:
            raise ToolError(f"submit_answer requires a successful {CALC} earlier in this run")
        last = calcs[-1]
        used = {"factor": factor_used}
        if canonical_filter(used) != canonical_filter(last["input"]):
            raise ToolError(f"factor_used {factor_used} does not match the factor of the last executed "
                            f"calculation {last['input'].get('factor')}")
        if not isinstance(emissions_t, (int, float)) or abs(emissions_t - last["output"]["emissions_t"]) > 0.001:
            raise ToolError(f"emissions_t {emissions_t} does not match the last executed calculation "
                            f"{last['output']['emissions_t']}")

        memory_ids_used = list(memory_ids_used or [])
        provided = {}                     # memory_id -> every provided version's evidence
        for m in self.memory_evidence:
            provided.setdefault(m["memory_id"], []).append(m)
        unknown = [m for m in memory_ids_used if m not in provided]
        if unknown:
            raise ToolError(f"memory_ids_used contains IDs not provided in this run: {unknown}; "
                            f"provided: {sorted(provided)}")

        # Evidence that ties a policy version to a factor: get_emission_factor results, and cited memories.
        evidence = [(c["output"]["version"], {"factor": c["output"]["factor"]}, "get_emission_factor")
                    for c in self.calls if c["name"] == "get_emission_factor" and c["ok"]]
        evidence += [(e["policy_version"], e["filter"], f"memory:{m}") for m in memory_ids_used for e in provided[m]]
        if not evidence:
            if policy_version is not None:
                raise ToolError("policy_version must be null when no get_emission_factor result or cited "
                                "memory establishes it in this run")
            source = None
        else:
            match = [src for ver, flt, src in evidence
                     if ver == policy_version and canonical_filter(flt) == canonical_filter(used)]
            if not match:
                seen = sorted({f"v{ver} {canonical_filter(flt)} ({src})" for ver, flt, src in evidence})
                raise ToolError(f"policy_version {policy_version} with factor {factor_used} is not supported "
                                f"by this run's evidence: {seen}")
            source = match[0]
        return {"accepted": True, "verified_against": {
            "factor": factor_used, "emissions_t": last["output"]["emissions_t"],
            "policy_version": policy_version, "policy_version_source": source,
            "memory_ids_used": memory_ids_used}}

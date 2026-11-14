"""Synthetic data only. Seeds the dedicated MemoryDelta database and nothing else."""
import json
from datetime import datetime

from .config import COLLECTIONS, METRIC, TASK, RequestContext, now
from .db import ensure_schema
from .tools import Tools

ACME = RequestContext("acme", "ana")
GLOBEX = RequestContext("globex", "gus")
INITECH = RequestContext("initech", "ivy")

# Versioned rule = the emission factor table each company reports with (NGER, location-based Scope 2, NSW).
# 0.66 (2024-25) and 0.64 (2025-26) kg CO2e/kWh: Clean Energy Regulator published factors.
NSW_2024_25 = ("NGER", "NSW", "2024-25", 0.66)
NSW_2025_26 = ("NGER", "NSW", "2025-26", 0.64)
POLICIES = [
    ("acme", 1, True, NSW_2024_25),
    ("acme", 2, False, NSW_2025_26),     # activated in the demo: the new factor table is published
    ("globex", 1, True, NSW_2024_25),
    ("initech", 1, True, NSW_2024_25),
]
SITES = {"acme": ("ACME-SYD-01", "Acme Sydney plant", 1.0),
         "globex": ("GLX-NEW-01", "Globex Newcastle warehouse", 2.5),
         "initech": ("INI-PAR-01", "Initech Parramatta lab", 0.4)}
# FY2025-26 monthly kWh for a scale-1.0 site: sums to exactly 100,000 kWh.
MONTHLY = [8200, 7900, 8100, 8400, 8600, 8800, 8900, 8700, 8500, 8300, 7900, 7700]
# Fixture targets (asserted in tests): acme 66 t (v1) / 64 t (v2), globex 165 t, initech 26.4 t.


def _usage():
    out, n = [], 0
    for tenant, (site_id, site_name, scale) in SITES.items():
        for year_start, noise in ((2024, 1.03), (2025, 1.0)):    # FY2024-25 is outside the window
            for i, kwh in enumerate(MONTHLY):
                n += 1
                month = datetime(year_start + (1 if i >= 6 else 0), (6 + i) % 12 + 1, 1)
                out.append({"_id": f"u{n:05d}", "tenant_id": tenant, "site_id": site_id, "site_name": site_name,
                            "region": "NSW", "month": month, "kwh": round(kwh * scale * noise)})
    return out


def bootstrap_memory(store, ctx, memory_id, approved_by):
    """Run the verified discovery path with the real tools, compress the trace, store it approved."""
    tools = Tools(store.db, ctx)
    pol = tools.call("get_emission_factor", {"metric": METRIC})
    tools.call("inspect_usage", {"collection": "usage"})
    tools.call("calculate_emissions", {"factor": pol["factor"]})
    run_id = f"seed_{ctx.tenant_id}"
    store.db.runs.insert_one({"_id": run_id, "kind": "seed_bootstrap", "tenant_id": ctx.tenant_id,
                              "tool_calls": tools.calls, "created_at": now()})
    return store.remember_run(ctx, memory_id, tools.calls, run_id,
                              summary=f"Saved calculation: {TASK} Sum the site's electricity use for the "
                                      f"reporting year and multiply by the company's emission factor.",
                              approved_by=approved_by)


def seed(store, log=print):
    db = store.db
    ensure_schema(db, store.mode, log=log)
    for name in COLLECTIONS:          # delete_many (not drop) keeps the vector + TTL indexes
        db[name].delete_many({})
    db.usage.insert_many(_usage())
    db.policies.insert_many([{
        "_id": f"pol_{t}_{METRIC}_v{v}", "tenant_id": t, "metric": METRIC, "version": v, "active": a,
        "framework": fw, "region": rg, "period": per,
        "definition": f"{fw} location-based Scope 2 factor for {rg}, {per}: {fac} kg CO2e/kWh",
        "filter_json": json.dumps({"factor": fac}, sort_keys=True), "created_at": now()}
        for t, v, a, (fw, rg, per, fac) in POLICIES])
    a = bootstrap_memory(store, ACME, "mem_acme_scope2", approved_by="acme_lead")
    g = bootstrap_memory(store, GLOBEX, "mem_globex_scope2", approved_by="globex_lead")
    for ctx, doc in ((ACME, a), (GLOBEX, g)):
        store.wait_searchable(ctx, TASK, doc["_id"])
    log(f"seeded {db.usage.count_documents({})} electricity readings, {db.policies.count_documents({})} policies, "
        f"{db.memories.count_documents({})} memories into database {db.name!r}")
    return a, g


def activate_policy(db, tenant_id, version, actor="policy_admin"):
    """Authorized business change. Never performed by the agent."""
    db.policies.update_many({"tenant_id": tenant_id, "metric": METRIC}, {"$set": {"active": False}})
    db.policies.update_one({"tenant_id": tenant_id, "metric": METRIC, "version": version},
                           {"$set": {"active": True, "activated_by": actor, "activated_at": now()}})


if __name__ == "__main__":
    from .db import connect
    from .embeddings import default_embedder
    from .store import MemoryStore
    _db, _mode = connect()
    print(f"seeding {_mode} database {_db.name!r} (only MemoryDelta collections are touched)")
    seed(MemoryStore(_db, _mode, default_embedder()))

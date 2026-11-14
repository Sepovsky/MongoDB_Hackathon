"""Synthetic data only. Seeds the dedicated MemoryDelta database and nothing else."""
import json
from datetime import datetime

from .config import COLLECTIONS, METRIC, TASK, RequestContext, now
from .db import ensure_schema
from .tools import Tools

ACME = RequestContext("acme", "ana")
GLOBEX = RequestContext("globex", "gus")
INITECH = RequestContext("initech", "ivy")

# Fixture targets (asserted in tests): acme v1=120, acme v2=93, globex v1=41, initech v1=25.
POLICIES = [
    ("acme", 1, True, "Active = placed any order in the month.", {}),
    ("acme", 2, False, "Active = placed a paid, non-refunded order in the month.",
     {"status": "paid", "refunded": False}),
    ("globex", 1, True, "Active = placed an order with total >= 100 in the month.", {"total": {"$gte": 100}}),
    ("initech", 1, True, "Active = placed any order in the month.", {}),
]


def _orders():
    out, n = [], 0

    def add(tenant, cust, day, status, refunded, total, month=10):
        nonlocal n
        n += 1
        out.append({"_id": f"o{n:05d}", "order_id": f"{tenant[:2].upper()}-{n:05d}", "tenant_id": tenant,
                    "customer_id": cust, "order_date": datetime(2026, month, day),
                    "status": status, "refunded": refunded, "total": total})

    for i in range(1, 121):                       # acme: 120 customers ordered in October
        c = f"A{i:03d}"
        if i <= 93:
            add("acme", c, 1 + i % 28, "paid", False, 40 + i % 90)
            if i % 4 == 0:
                add("acme", c, 2 + i % 27, "pending", False, 25)
        elif i <= 105:
            add("acme", c, 1 + i % 28, "pending", False, 60)
        else:
            add("acme", c, 1 + i % 28, "paid", True, 75)
    for i in range(121, 131):                     # September noise, outside the window
        add("acme", f"A{i:03d}", 15, "paid", False, 50, month=9)
    for i in range(1, 81):                        # globex: 80 ordered, 41 with total >= 100
        add("globex", f"B{i:03d}", 1 + i % 28, "paid", False, 150 if i <= 41 else 40)
    for i in range(1, 26):                        # initech: 25 ordered, no memories at all
        add("initech", f"C{i:03d}", 1 + i % 28, "paid", False, 30)
    return out


def bootstrap_memory(store, ctx, memory_id, approved_by):
    """Run the verified discovery path with the real tools, compress the trace, store it approved."""
    tools = Tools(store.db, ctx)
    pol = tools.call("get_policy", {"metric": METRIC})
    tools.call("inspect_collection", {"collection": "orders"})
    tools.call("run_count_query", {"filter": pol["filter"]})
    run_id = f"seed_{ctx.tenant_id}"
    store.db.runs.insert_one({"_id": run_id, "kind": "seed_bootstrap", "tenant_id": ctx.tenant_id,
                              "tool_calls": tools.calls, "created_at": now()})
    return store.remember_run(ctx, memory_id, tools.calls, run_id,
                              summary=f"Verified procedure: {TASK} Count distinct active customers in "
                                      f"orders for October 2026 using the tenant's metric policy.",
                              approved_by=approved_by)


def seed(store, log=print):
    db = store.db
    ensure_schema(db, store.mode, log=log)
    for name in COLLECTIONS:          # delete_many (not drop) keeps the vector + TTL indexes
        db[name].delete_many({})
    db.orders.insert_many(_orders())
    db.policies.insert_many([{
        "_id": f"pol_{t}_{METRIC}_v{v}", "tenant_id": t, "metric": METRIC, "version": v, "active": a,
        "definition": d, "filter_json": json.dumps(f, sort_keys=True), "created_at": now()}
        for t, v, a, d, f in POLICIES])
    a = bootstrap_memory(store, ACME, "mem_acme_active_customers", approved_by="acme_lead")
    g = bootstrap_memory(store, GLOBEX, "mem_globex_active_customers", approved_by="globex_lead")
    for ctx, doc in ((ACME, a), (GLOBEX, g)):
        store.wait_searchable(ctx, TASK, doc["_id"])
    log(f"seeded {db.orders.count_documents({})} orders, {db.policies.count_documents({})} policies, "
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

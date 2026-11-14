"""Hard cases for the retail reporting demo, using the local database and scripted model."""
from datetime import datetime

from memorydelta.agent import run_agent
from memorydelta.config import TASK, authenticate
from memorydelta.model import memories_in_payload
from memorydelta.seed import activate_policy


ACME_MEMORY = "mem_acme_active_customers"
GLOBEX_MEMORY = "mem_globex_active_customers"
PAID_FILTER = {"status": "paid", "refunded": False}


def test_late_refund_recounts_with_the_same_approved_memory(store, model):
    ctx = authenticate("sess_acme_ana")
    activate_policy(store.db, "acme", 2)
    approved = store.correct_memory(ACME_MEMORY, PAID_FILTER, "acme_lead", "policy v2")
    original = run_agent(store, model, ctx, TASK)
    assert original.decision["active_customers"] == 93

    refund = store.db.orders.update_one(
        {"tenant_id": "acme", "customer_id": "A001", "status": "paid"},
        {"$set": {"refunded": True}},
    )
    assert refund.modified_count == 1
    warm = run_agent(store, model, ctx, TASK)
    cold = run_agent(store, model, ctx, TASK, memory_mode="none")
    assert memories_in_payload(warm.payloads[0])[0]["verified_result"] == 93
    assert warm.included[0]["_id"] == approved["_id"]
    assert warm.decision["active_customers"] == cold.decision["active_customers"] == 92
    assert [len(warm.tool_calls), len(cold.tool_calls)] == [2, 4]


def test_higher_ranked_foreign_and_revoked_versions_lose_to_valid_memory(store, model, monkeypatch):
    ctx = authenticate("sess_acme_ana")
    activate_policy(store.db, "acme", 2)
    approved = store.correct_memory(ACME_MEMORY, PAID_FILTER, "acme_lead", "policy v2")
    revoked = store.create_memory(ctx, "old_report", approved["content"], TASK, "acme_lead")
    store.revoke_memory("old_report", "acme_lead", "report withdrawn")
    ranked = [f"{GLOBEX_MEMORY}@v1", revoked["_id"], f"{ACME_MEMORY}@v1", approved["_id"]]
    monkeypatch.setattr(store, "search", lambda *args, **kwargs: [
        {"_id": memory_id, "score": 1 - rank / 10} for rank, memory_id in enumerate(ranked)
    ])

    result = run_agent(store, model, ctx, TASK)
    assert [f"{m['memory_id']}@v{m['version']}" for m in memories_in_payload(result.payloads[0])] == [
        approved["_id"]
    ]
    assert [(r["_id"], r["reason"]) for r in result.rejected] == list(zip(
        ranked, ["tenant_mismatch", "status_revoked", "status_superseded"]
    ))
    assert result.decision["active_customers"] == 93


def test_restricted_memory_forces_fresh_query_with_month_end_and_duplicate_orders(store, model):
    ctx = authenticate("sess_acme_ana")
    activate_policy(store.db, "acme", 2)
    approved = store.correct_memory(ACME_MEMORY, PAID_FILTER, "acme_lead", "policy v2")
    store.db.memories.update_one({"_id": approved["_id"]}, {"$set": {"allowed_users": ["acme_lead"]}})
    base = {"tenant_id": "acme", "status": "paid", "refunded": False, "total": 75}
    store.db.orders.insert_many([
        {**base, "order_id": "edge-duplicate", "customer_id": "A001", "order_date": datetime(2026, 10, 31, 23, 59)},
        {**base, "order_id": "edge-october", "customer_id": "A999", "order_date": datetime(2026, 10, 31, 23, 59)},
        {**base, "order_id": "edge-november", "customer_id": "A998", "order_date": datetime(2026, 11, 1)},
    ])

    result = run_agent(store, model, ctx, TASK)
    assert memories_in_payload(result.payloads[0]) == []
    assert any(r["_id"] == approved["_id"] and r["reason"] == "user_not_allowed" for r in result.rejected)
    assert result.first_action == "get_policy"
    assert result.decision["active_customers"] == 94

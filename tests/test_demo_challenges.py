"""Hard cases for the Scope 2 emissions demo, using the local database and scripted model."""
from datetime import datetime

from memorydelta.agent import run_agent
from memorydelta.config import TASK, authenticate
from memorydelta.model import memories_in_payload
from memorydelta.seed import activate_policy


ACME_MEMORY = "mem_acme_scope2"
GLOBEX_MEMORY = "mem_globex_scope2"
V2 = {"factor": 0.64}


def test_late_meter_correction_recalculates_with_the_same_approved_memory(store, model):
    ctx = authenticate("sess_acme_ana")
    activate_policy(store.db, "acme", 2)
    approved = store.correct_memory(ACME_MEMORY, V2, "acme_lead", "factor table 2025-26")
    original = run_agent(store, model, ctx, TASK)
    assert original.decision["emissions_t"] == 64.0

    fix = store.db.usage.update_one(
        {"tenant_id": "acme", "month": datetime(2026, 6, 1)}, {"$inc": {"kwh": 2500}})
    assert fix.modified_count == 1
    warm = run_agent(store, model, ctx, TASK)
    cold = run_agent(store, model, ctx, TASK, memory_mode="none")
    assert memories_in_payload(warm.payloads[0])[0]["verified_result"] == 64.0
    assert warm.included[0]["_id"] == approved["_id"]
    assert warm.decision["emissions_t"] == cold.decision["emissions_t"] == 65.6
    assert [len(warm.tool_calls), len(cold.tool_calls)] == [2, 4]


def test_higher_ranked_foreign_and_revoked_versions_lose_to_valid_memory(store, model, monkeypatch):
    ctx = authenticate("sess_acme_ana")
    activate_policy(store.db, "acme", 2)
    approved = store.correct_memory(ACME_MEMORY, V2, "acme_lead", "factor table 2025-26")
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
    assert result.decision["emissions_t"] == 64.0


def test_restricted_memory_forces_fresh_calculation_with_period_boundaries(store, model):
    ctx = authenticate("sess_acme_ana")
    activate_policy(store.db, "acme", 2)
    approved = store.correct_memory(ACME_MEMORY, V2, "acme_lead", "factor table 2025-26")
    store.db.memories.update_one({"_id": approved["_id"]}, {"$set": {"allowed_users": ["acme_lead"]}})
    base = {"tenant_id": "acme", "site_id": "ACME-SYD-01", "site_name": "Acme Sydney plant", "region": "NSW"}
    store.db.usage.insert_many([
        {**base, "_id": "edge-last-day", "month": datetime(2026, 6, 30, 23, 59), "kwh": 1000},
        {**base, "_id": "edge-next-year", "month": datetime(2026, 7, 1), "kwh": 5000},
        {**base, "_id": "edge-prev-year", "month": datetime(2025, 6, 30, 23, 59), "kwh": 5000},
    ])

    result = run_agent(store, model, ctx, TASK)
    assert memories_in_payload(result.payloads[0]) == []
    assert any(r["_id"] == approved["_id"] and r["reason"] == "user_not_allowed" for r in result.rejected)
    assert result.first_action == "get_emission_factor"
    assert result.decision["emissions_t"] == 64.64     # 101,000 kWh x 0.64; out-of-period readings excluded

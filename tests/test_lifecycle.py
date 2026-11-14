"""Correction, revocation, expiry and stale-factor rejection, checked on the model payload and trace."""
import json
from datetime import timedelta

from memorydelta.agent import run_agent
from memorydelta.config import TASK, authenticate, now
from memorydelta.model import memories_in_payload
from memorydelta.seed import activate_policy

MEM = "mem_acme_scope2"
V1 = {"factor": 0.66}
V2 = {"factor": 0.64}
ALLOWED_TRACE_KEYS = {"_id", "memory_id", "version", "reason"}


def _sent(r):
    return [(m["memory_id"], m["version"]) for m in memories_in_payload(r.payloads[0])]


def test_stale_factor_rejected_and_agent_recalculates(store, model):
    activate_policy(store.db, "acme", 2)
    r = run_agent(store, model, authenticate("sess_acme_ana"), TASK)
    assert _sent(r) == []
    assert r.rejected[0]["reason"].startswith("policy_version_mismatch(memory=v1, current=v2)")
    assert r.decision["emissions_t"] == 64.0
    ran = [c["input"] for c in r.tool_calls if c["name"] == "calculate_emissions"]
    assert V1 not in ran                      # the stale 2024-25 factor was never executed

    stale = run_agent(store, model, authenticate("sess_acme_ana"), TASK, memory_mode="ungated")
    assert stale.decision["emissions_t"] == 66.0   # ungated baseline gives the stale answer


def test_correction_sends_only_replacement(store, model):
    activate_policy(store.db, "acme", 2)
    new = store.correct_memory(MEM, V2, actor="acme_lead", reason="factor table 2025-26")
    assert new["version"] == 2 and new["content"]["critical_evidence"]["result"] == 64.0
    r = run_agent(store, model, authenticate("sess_acme_ana"), TASK)
    assert _sent(r) == [(MEM, 2)]
    assert r.included == [{"_id": f"{MEM}@v2", "memory_id": MEM, "version": 2}]
    assert {"memory_id": MEM, "version": 1, "reason": "status_superseded"}.items() <= r.rejected[0].items()
    assert r.decision["emissions_t"] == 64.0 and r.decision["factor_used"] == 0.64
    assert store.db.memory_deltas.count_documents({"memory_id": MEM, "from_version": 1, "to_version": 2}) == 1


def test_revocation_excludes_every_version(store, model):
    activate_policy(store.db, "acme", 2)
    store.correct_memory(MEM, V2, actor="acme_lead", reason="factor table 2025-26")
    assert store.revoke_memory(MEM, actor="acme_lead", reason="customer request") == 2
    r = run_agent(store, model, authenticate("sess_acme_ana"), TASK)
    assert _sent(r) == [] and r.included == []
    assert r.decision["emissions_t"] == 64.0          # still answers via a fresh calculation
    for d in store.db.memories.find({"memory_id": MEM}):
        assert d["status"] == "revoked"
        assert not {"content", "embedding", "summary"} & d.keys()


def test_revoked_doc_returned_by_lagging_index_is_still_blocked(store, model, monkeypatch):
    """Simulates Atlas index lag: search still returns the revoked id; the _id re-read blocks it."""
    monkeypatch.setattr(store, "search", lambda *a, **k: [{"_id": f"{MEM}@v1", "score": 0.99}])
    store.revoke_memory(MEM, actor="acme_lead", reason="wrong")
    r = run_agent(store, model, authenticate("sess_acme_ana"), TASK)
    assert _sent(r) == []
    assert r.rejected == [{"_id": f"{MEM}@v1", "memory_id": MEM, "version": 1, "reason": "status_revoked"}]


def test_expired_memory_blocked_before_ttl(store, model):
    store.db.memories.update_one({"_id": f"{MEM}@v1"}, {"$set": {"expires_at": now() - timedelta(seconds=1)}})
    r = run_agent(store, model, authenticate("sess_acme_ana"), TASK)
    assert _sent(r) == [] and r.rejected[0]["reason"] == "expired"


def test_trace_never_contains_rejected_content(store, model):
    activate_policy(store.db, "acme", 2)
    r = run_agent(store, model, authenticate("sess_acme_ana"), TASK)
    run = store.db.runs.find_one({"_id": r.run_id})
    assert run["rejected"] and all(set(x) <= ALLOWED_TRACE_KEYS for x in run["rejected"])
    assert "verified_params" not in json.dumps(run["rejected"], default=str)


def test_incomplete_evidence_rejected(store, model):
    store.db.memories.update_one({"_id": f"{MEM}@v1"}, {"$set": {"content.critical_evidence.source_ids": []}})
    r = run_agent(store, model, authenticate("sess_acme_ana"), TASK)
    assert r.rejected[0]["reason"] == "evidence_incomplete"

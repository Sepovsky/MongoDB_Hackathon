"""Harness checks with the scripted double. These prove the plumbing, not live model behavior."""
from memorydelta.agent import run_agent
from memorydelta.compress import compress, estimate_tokens, evidence_pointers, render_compact, render_raw
from memorydelta.config import TASK, authenticate


def test_only_memory_differs_between_arms(store, model):
    ctx = authenticate("sess_acme_ana")
    off = run_agent(store, model, ctx, TASK, memory_mode="none")
    on = run_agent(store, model, ctx, TASK, memory_mode="governed")
    p_off, p_on = off.payloads[0], on.payloads[0]
    assert p_off["system"] == p_on["system"] and p_off["tools"] == p_on["tools"]
    assert off.first_action == "get_emission_factor" and on.first_action == "calculate_emissions"
    assert off.decision["emissions_t"] == on.decision["emissions_t"] == 66.0


def test_compact_memory_preserves_evidence_and_is_smaller(store, model):
    ctx = authenticate("sess_acme_ana")
    cold = run_agent(store, model, ctx, TASK, memory_mode="none")
    policy = store.db.policies.find_one({"tenant_id": "acme", "active": True})
    content = compress(cold.trace, policy, cold.run_id)
    compact = render_compact({"memory_id": "m", "version": 1, "content": content})
    raw = render_raw("m", 1, cold.trace, "narration")
    assert estimate_tokens(compact) < estimate_tokens(raw)
    for pid in evidence_pointers(compact):
        coll, key = (store.db.runs, pid[4:]) if pid.startswith("run:") else (store.db.policies, pid)
        assert coll.find_one({"_id": key}) is not None
    assert compact["verified_params"] == {"factor": 0.66} and compact["verified_result"] == 66.0

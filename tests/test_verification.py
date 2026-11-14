"""Regression tests: corrections must match the active policy; answers must match the executed query."""
from types import SimpleNamespace as NS

import pytest

from memorydelta.agent import run_agent
from memorydelta.config import TASK, authenticate
from memorydelta.seed import activate_policy
from memorydelta.tools import Tools

MEM = "mem_acme_active_customers"
V2_FILTER = {"status": "paid", "refunded": False}


# ---------- 1. correction verified against the active policy ----------

def test_correction_with_stale_filter_is_rejected_before_any_write(store):
    activate_policy(store.db, "acme", 2)
    before = {c: store.db[c].count_documents({}) for c in ("memories", "memory_deltas", "runs")}
    with pytest.raises(ValueError, match="does not match active policy"):
        store.correct_memory(MEM, {}, actor="acme_lead", reason="stale filter")
    assert {c: store.db[c].count_documents({}) for c in before} == before
    assert store.db.memories.find_one({"_id": f"{MEM}@v1"})["status"] == "active"
    assert store.db.memories.find_one({"_id": f"{MEM}@v2"}) is None


def test_correction_with_valid_v2_filter_saves_93(store):
    activate_policy(store.db, "acme", 2)
    new = store.correct_memory(MEM, {"refunded": False, "status": "paid"}, actor="acme_lead", reason="v2")
    ev, deps = new["content"]["critical_evidence"], new["content"]["dependencies"]
    assert (new["version"], ev["result"], deps["policy_version"]) == (2, 93, 2)
    assert store.db.memories.find_one({"_id": f"{MEM}@v1"})["status"] == "superseded"


# ---------- 2. submit_answer verified against the executed query ----------

def _acme_tools(store):
    return Tools(store.db, authenticate("sess_acme_ana"))


def test_submit_without_count_is_rejected(store):
    t = _acme_tools(store)
    out = t.call("submit_answer", {"active_customers": 999, "filter_used": {}, "policy_version": 1,
                                   "memory_ids_used": []})
    assert "error" in out and not t.calls[-1]["ok"]


def test_submit_with_mismatched_count_is_rejected(store):
    t = _acme_tools(store)
    t.call("run_count_query", {"filter": {}})
    out = t.call("submit_answer", {"active_customers": 999, "filter_used": {}, "policy_version": 1,
                                   "memory_ids_used": []})
    assert "does not match the last executed count" in out["error"]


def test_submit_with_mismatched_filter_is_rejected(store):
    t = _acme_tools(store)
    t.call("run_count_query", {"filter": {}})
    out = t.call("submit_answer", {"active_customers": 120, "filter_used": V2_FILTER, "policy_version": 2,
                                   "memory_ids_used": []})
    assert "does not match the last executed query filter" in out["error"]


def test_valid_submission_is_accepted(store):
    activate_policy(store.db, "acme", 2)
    t = _acme_tools(store)
    t.call("get_policy", {"metric": "active_customers"})
    t.call("run_count_query", {"filter": V2_FILTER})
    out = t.call("submit_answer", {"active_customers": 93, "filter_used": {"refunded": False, "status": "paid"},
                                   "policy_version": 2, "memory_ids_used": []})
    assert out["accepted"] is True and t.calls[-1]["ok"]
    assert out["verified_against"]["policy_version_source"] == "get_policy"


class _FabricatingModel:
    """Submits 999 without ever counting."""
    live, name = False, "fabricator"

    def create(self, **payload):
        call = NS(type="tool_use", id="t1", name="submit_answer",
                  input={"active_customers": 999, "filter_used": {}, "policy_version": 1, "memory_ids_used": []})
        return NS(content=[call], stop_reason="tool_use", usage=None)


def test_agent_does_not_record_unverified_answer(store):
    r = run_agent(store, _FabricatingModel(), authenticate("sess_acme_ana"), TASK, max_steps=2)
    assert r.decision is None
    assert all(c["name"] == "submit_answer" and not c["ok"] for c in r.tool_calls)


def test_ungated_baseline_still_returns_stale_120(store, model):
    activate_policy(store.db, "acme", 2)
    r = run_agent(store, model, authenticate("sess_acme_ana"), TASK, memory_mode="ungated")
    assert r.decision["active_customers"] == 120 and r.decision["filter_used"] == {}


# ---------- 3. saving a new memory uses the same policy check ----------

def _trace(store, flt):
    t = _acme_tools(store)
    t.call("run_count_query", {"filter": flt})
    return t.calls


def test_save_with_stale_filter_is_rejected_before_any_write(store):
    activate_policy(store.db, "acme", 2)
    before = store.db.memories.count_documents({})
    with pytest.raises(ValueError, match="save rejected: .* does not match active policy"):
        store.remember_run(authenticate("sess_acme_ana"), "mem_acme_new", _trace(store, {}), "seed_acme",
                           summary="active customers", approved_by="acme_lead")
    assert store.db.memories.count_documents({}) == before
    assert store.db.memories.find_one({"memory_id": "mem_acme_new"}) is None


def test_save_with_valid_filter_stores_93_under_v2(store):
    activate_policy(store.db, "acme", 2)
    doc = store.remember_run(authenticate("sess_acme_ana"), "mem_acme_new", _trace(store, V2_FILTER),
                             "seed_acme", summary="active customers", approved_by="acme_lead")
    assert doc["content"]["critical_evidence"]["result"] == 93
    assert doc["content"]["dependencies"]["policy_version"] == 2


# ---------- 4. answer metadata checked against trusted run state ----------

ACME_MEM_EVIDENCE = [{"memory_id": MEM, "policy_version": 1, "filter": {}}]


def _submit(t, **over):
    args = {"active_customers": 120, "filter_used": {}, "policy_version": 1, "memory_ids_used": [MEM]}
    args.update(over)
    return t.call("submit_answer", args)


def test_submit_with_fabricated_policy_version_is_rejected(store):
    t = Tools(store.db, authenticate("sess_acme_ana"), memory_evidence=ACME_MEM_EVIDENCE)
    t.call("run_count_query", {"filter": {}})
    out = _submit(t, policy_version=999)
    assert "not supported by this run's evidence" in out["error"]


def test_submit_with_unknown_memory_id_is_rejected(store):
    t = Tools(store.db, authenticate("sess_acme_ana"), memory_evidence=ACME_MEM_EVIDENCE)
    t.call("run_count_query", {"filter": {}})
    out = _submit(t, memory_ids_used=[MEM, "mem_globex_active_customers"])
    assert "not provided in this run" in out["error"]


def test_submit_with_policy_version_not_tied_to_filter_is_rejected(store):
    """Read policy v2, ran the old {} filter, then claimed v2: version and filter disagree."""
    activate_policy(store.db, "acme", 2)
    t = _acme_tools(store)
    t.call("get_policy", {"metric": "active_customers"})
    t.call("run_count_query", {"filter": {}})
    out = _submit(t, policy_version=2, memory_ids_used=[])
    assert "not supported by this run's evidence" in out["error"]


def test_submit_with_version_but_no_evidence_is_rejected(store):
    t = _acme_tools(store)
    t.call("run_count_query", {"filter": {}})
    out = _submit(t, policy_version=1, memory_ids_used=[])
    assert "must be null" in out["error"]


def test_submit_citing_provided_memory_is_accepted(store):
    t = Tools(store.db, authenticate("sess_acme_ana"), memory_evidence=ACME_MEM_EVIDENCE)
    t.call("run_count_query", {"filter": {}})
    out = _submit(t)
    assert out["verified_against"]["policy_version_source"] == f"memory:{MEM}"


def test_run_record_stores_verified_metadata(store, model):
    r = run_agent(store, model, authenticate("sess_acme_ana"), TASK)
    run = store.db.runs.find_one({"_id": r.run_id})
    assert run["decision_evidence"]["policy_version_source"] == f"memory:{MEM}"
    assert run["decision_evidence"]["memory_ids_used"] == [MEM]


def test_two_versions_of_same_memory_both_count_as_evidence(store):
    """Ungated baselines can see v1 and v2 of one memory; citing it with v1's filter must verify."""
    ev = [{"memory_id": MEM, "policy_version": 1, "filter": {}},
          {"memory_id": MEM, "policy_version": 2, "filter": V2_FILTER}]
    t = Tools(store.db, authenticate("sess_acme_ana"), memory_evidence=ev)
    t.call("run_count_query", {"filter": {}})
    assert _submit(t)["verified_against"]["policy_version"] == 1

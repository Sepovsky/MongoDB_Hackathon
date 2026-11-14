"""Atlas memory store: retrieval, eligibility, and the correct / revoke lifecycle."""
import json
import time
from dataclasses import dataclass, field
from datetime import timedelta

from . import gate
from .compress import compress
from .config import METRIC, VECTOR_INDEX, RequestContext, now
from .embeddings import cosine
from .tools import CALC, Tools, canonical_filter


def require_policy_match(policy, flt, tenant_id, action):
    """Shared by every path that writes a memory: the filter must implement the active policy.
    Raises before any write."""
    if policy is None:
        raise ValueError(f"{action} rejected: no active policy for tenant {tenant_id!r}")
    if canonical_filter(flt) != canonical_filter(json.loads(policy["filter_json"])):
        raise ValueError(f"{action} rejected: filter {flt} does not match active policy "
                         f"{policy['_id']} (v{policy['version']}) filter {policy['filter_json']}")


@dataclass
class Selection:
    candidates: list = field(default_factory=list)   # [{_id, score}] as returned by search
    included: list = field(default_factory=list)     # full docs that will go to the model
    rejected: list = field(default_factory=list)     # [{_id, memory_id, version, reason}] - no content


class MemoryStore:
    def __init__(self, db, mode, embedder):
        self.db, self.mode, self.embedder = db, mode, embedder

    # ---------- retrieval ----------
    def search(self, ctx: RequestContext, text, k=5, scoped=True):
        """Vector Search proposes candidates. scoped=True prefilters on the trusted tenant."""
        qv = self.embedder.embed([text], input_type="query")[0]
        if self.mode == "atlas":
            stage = {"index": VECTOR_INDEX, "path": "embedding", "queryVector": qv,
                     "numCandidates": max(100, k * 20), "limit": k}
            if scoped:
                stage["filter"] = {"tenant_id": ctx.tenant_id}
            pipe = [{"$vectorSearch": stage},
                    {"$project": {"_id": 1, "score": {"$meta": "vectorSearchScore"}}}]
            return list(self.db.memories.aggregate(pipe))
        q = {"embedding": {"$exists": True}}
        if scoped:
            q["tenant_id"] = ctx.tenant_id
        hits = [{"_id": d["_id"], "score": cosine(qv, d["embedding"])}
                for d in self.db.memories.find(q, {"embedding": 1})]
        return sorted(hits, key=lambda h: -h["score"])[:k]

    def select(self, ctx: RequestContext, text, mode="governed", k=5, max_included=2):
        """mode: governed (prefilter + gate), ungated (prefilter, no gate),
        unscoped (no prefilter, no gate - only to measure leakage), none (no memory)."""
        sel = Selection()
        if mode == "none":
            return sel
        sel.candidates = self.search(ctx, text, k=k, scoped=(mode != "unscoped"))
        policy = self.db.policies.find_one({"tenant_id": ctx.tenant_id, "metric": METRIC, "active": True})
        for hit in sel.candidates:
            doc = self.db.memories.find_one({"_id": hit["_id"]})  # authoritative re-read, not index copy
            if mode == "governed":
                ok, reason = gate.evaluate(doc, ctx, policy)
            else:
                ok, reason = (doc is not None and doc.get("content") is not None), "ungated"
            if ok and len(sel.included) < max_included:
                sel.included.append(doc)
            elif not ok:
                sel.rejected.append({"_id": hit["_id"], "memory_id": (doc or {}).get("memory_id"),
                                     "version": (doc or {}).get("version"), "reason": reason})
        return sel

    def wait_searchable(self, ctx, text, _id, present=True, timeout=60):
        """Atlas Vector Search indexes are eventually consistent; poll after writes."""
        if self.mode != "atlas":
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            ids = {h["_id"] for h in self.search(ctx, text, k=10)}
            if (_id in ids) == present:
                return True
            time.sleep(1.5)
        return False

    # ---------- lifecycle ----------
    def create_memory(self, ctx, memory_id, content, summary, approved_by, version=1,
                      parent_version=None, ttl_days=30, allowed_users=None):
        doc = {
            "_id": f"{memory_id}@v{version}", "memory_id": memory_id, "version": version,
            "parent_version": parent_version, "tenant_id": ctx.tenant_id,
            "owner_user_id": ctx.user_id, "allowed_users": allowed_users or [],
            "status": "active", "approved_by": approved_by, "created_at": now(),
            "expires_at": now() + timedelta(days=ttl_days),
            "summary": summary, "content": content,
            "embedding": self.embedder.embed([summary])[0],
        }
        self.db.memories.insert_one(doc)
        return doc

    def remember_run(self, ctx, memory_id, trace, run_id, summary, approved_by):
        """Compress a verified run and store it as an approved memory."""
        policy = self.db.policies.find_one({"tenant_id": ctx.tenant_id, "metric": METRIC, "active": True})
        calcs = [c for c in trace if c["name"] == CALC and c["ok"]]
        if not calcs:
            raise ValueError(f"save rejected: no successful {CALC} in the run")
        require_policy_match(policy, calcs[-1]["input"], ctx.tenant_id, "save")
        return self.create_memory(ctx, memory_id, compress(trace, policy, run_id), summary, approved_by)

    def correct_memory(self, memory_id, new_filter, actor, reason):
        """New version, re-verified against current data and policy; old version superseded."""
        old = self.db.memories.find_one({"memory_id": memory_id, "status": "active"})
        if old is None:
            raise LookupError(f"no active version of {memory_id}")
        owner = RequestContext(old["tenant_id"], old["owner_user_id"])
        tools = Tools(self.db, owner)
        policy = tools.current_policy()
        require_policy_match(policy, new_filter, owner.tenant_id, "correction")   # before any write
        tools.call(CALC, dict(new_filter))                              # verification run
        if not tools.calls[-1]["ok"]:
            raise ValueError(f"correction failed verification: {tools.calls[-1]['output']}")
        run_id = f"correction:{memory_id}:v{old['version'] + 1}"
        self.db.runs.insert_one({"_id": run_id, "tenant_id": owner.tenant_id, "kind": "correction",
                                 "actor": actor, "tool_calls": tools.calls, "created_at": now()})
        content = compress(tools.calls, policy, run_id)
        new = self.create_memory(owner, memory_id, content, old["summary"], approved_by=actor,
                                 version=old["version"] + 1, parent_version=old["version"],
                                 allowed_users=old.get("allowed_users"))
        self.db.memories.update_one({"_id": old["_id"]}, {"$set": {
            "status": "superseded", "superseded_by": new["_id"], "superseded_at": now()}})
        self.db.memory_deltas.insert_one({
            "memory_id": memory_id, "from_version": old["version"], "to_version": new["version"],
            "changed": {"critical_evidence.verified_filter_json": {
                "old": old["content"]["critical_evidence"]["verified_filter_json"],
                "new": content["critical_evidence"]["verified_filter_json"]},
                "dependencies.policy_version": {
                "old": old["content"]["dependencies"]["policy_version"],
                "new": content["dependencies"]["policy_version"]}},
            "actor": actor, "reason": reason, "created_at": now()})
        return new

    def revoke_memory(self, memory_id, actor, reason):
        """Excludes every version from future runs and scrubs the embedding and memory content.
        Metadata (id, version, status, who/why) stays for audit. Not a hard document delete."""
        res = self.db.memories.update_many({"memory_id": memory_id}, {
            "$set": {"status": "revoked", "revoked_at": now(), "revoked_by": actor, "revoke_reason": reason},
            "$unset": {"embedding": "", "content": "", "summary": ""}})
        self.db.memory_deltas.update_many({"memory_id": memory_id}, {
            "$set": {"revoked": True}, "$unset": {"changed": ""}})
        return res.modified_count

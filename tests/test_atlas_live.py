"""Live Atlas checks. Skipped unless MONGODB_URI is set. Uses only the dedicated demo database."""
import pytest

from memorydelta.config import TASK, authenticate, mongodb_uri, now

pytestmark = pytest.mark.skipif(not mongodb_uri(), reason="MONGODB_URI not set; live Atlas checks skipped")


@pytest.fixture(scope="module")
def atlas_store():
    from memorydelta.db import connect
    from memorydelta.embeddings import default_embedder
    from memorydelta.seed import seed
    from memorydelta.store import MemoryStore
    db, mode = connect()
    assert mode == "atlas"
    s = MemoryStore(db, mode, default_embedder())
    seed(s, log=lambda *_: None)
    return s


def test_vector_search_prefilter_returns_only_own_tenant(atlas_store):
    for sess, tenant in (("sess_acme_ana", "acme"), ("sess_globex_gus", "globex")):
        ctx = authenticate(sess)
        hits = atlas_store.search(ctx, TASK, k=10)
        assert hits, "vector search returned nothing (index not READY?)"
        tenants = {atlas_store.db.memories.find_one({"_id": h["_id"]})["tenant_id"] for h in hits}
        assert tenants == {tenant}


def test_unscoped_search_sees_both_tenants(atlas_store):
    hits = atlas_store.search(authenticate("sess_acme_ana"), TASK, k=10, scoped=False)
    ids = {h["_id"] for h in hits}
    assert any(i.startswith("mem_acme") for i in ids) and any(i.startswith("mem_globex") for i in ids)


def test_schema_validator_rejects_memory_without_evidence(atlas_store):
    from pymongo.errors import WriteError
    with pytest.raises(WriteError):
        atlas_store.db.memories.insert_one({
            "_id": "bad@v1", "memory_id": "bad", "version": 1, "tenant_id": "acme", "status": "active",
            "created_at": now(), "content": {"critical_evidence": {"verified_filter_json": "{}"},
                                             "dependencies": {"policy_key": "x", "policy_version": 1}}})

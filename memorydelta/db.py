"""Connection and schema setup. Only touches the dedicated MemoryDelta database."""
import time

from .config import COLLECTIONS, DB_NAME, EMBED_DIM, VECTOR_INDEX, mongodb_uri

# Structure check at the database layer. Content correctness is checked in code and tests.
MEMORY_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["memory_id", "version", "tenant_id", "status", "created_at"],
        "properties": {
            "memory_id": {"bsonType": "string"},
            "version": {"bsonType": "int", "minimum": 1},
            "tenant_id": {"bsonType": "string"},
            "status": {"enum": ["active", "superseded", "revoked"]},
            "content": {
                "bsonType": "object",
                "required": ["critical_evidence", "dependencies"],
                "properties": {
                    "critical_evidence": {
                        "bsonType": "object",
                        "required": ["verified_filter_json", "source_ids"],
                    },
                    "dependencies": {
                        "bsonType": "object",
                        "required": ["policy_key", "policy_version"],
                    },
                },
            },
        },
    }
}

VECTOR_DEFINITION = {
    "fields": [
        {"type": "vector", "path": "embedding", "numDimensions": EMBED_DIM, "similarity": "cosine"},
        {"type": "filter", "path": "tenant_id"},
        {"type": "filter", "path": "status"},
    ]
}


def connect(uri=None):
    """Returns (db, mode). mode is 'atlas' with MONGODB_URI, else 'local' (in-memory mongomock)."""
    uri = uri or mongodb_uri()
    if uri:
        import certifi
        from pymongo import MongoClient
        # certifi's CA bundle: python.org macOS builds ship without system CA certs.
        client = MongoClient(uri, serverSelectionTimeoutMS=15000, appname="memorydelta",
                             tlsCAFile=certifi.where())
        client.admin.command("ping")
        return client[DB_NAME], "atlas"
    import mongomock
    return mongomock.MongoClient()[DB_NAME], "local"


def ensure_schema(db, mode, log=print):
    if db.name != DB_NAME:
        raise RuntimeError(f"refusing to manage database {db.name!r}; expected {DB_NAME!r}")
    existing = set(db.list_collection_names())
    for name in COLLECTIONS:
        if name not in existing:
            if name == "memories" and mode == "atlas":
                db.create_collection(name, validator=MEMORY_VALIDATOR, validationLevel="strict")
            else:
                db.create_collection(name)
        elif name == "memories" and mode == "atlas":
            db.command("collMod", name, validator=MEMORY_VALIDATOR, validationLevel="strict")
    db.memories.create_index([("memory_id", 1), ("version", 1)], unique=True)
    db.memories.create_index([("tenant_id", 1), ("status", 1), ("expires_at", 1)])
    db.policies.create_index([("tenant_id", 1), ("metric", 1), ("version", 1)], unique=True)
    db.usage.create_index([("tenant_id", 1), ("month", 1)])
    db.runs.create_index([("tenant_id", 1), ("created_at", -1)])
    if mode == "atlas":
        # Physical cleanup only. The gate checks expiry on every read because TTL runs ~once a minute.
        db.memories.create_index("expires_at", expireAfterSeconds=0, name="ttl_expires_at")
        ensure_vector_index(db, log)


def ensure_vector_index(db, log=print, timeout=180):
    from pymongo.operations import SearchIndexModel
    names = {ix["name"] for ix in db.memories.list_search_indexes()}
    if VECTOR_INDEX not in names:
        log(f"creating Atlas Vector Search index {VECTOR_INDEX!r} ...")
        db.memories.create_search_index(
            SearchIndexModel(definition=VECTOR_DEFINITION, name=VECTOR_INDEX, type="vectorSearch"))
    deadline = time.time() + timeout
    while time.time() < deadline:
        ix = next(iter(db.memories.list_search_indexes(VECTOR_INDEX)), None)
        if ix and ix.get("queryable"):
            return ix
        time.sleep(3)
    raise TimeoutError(f"vector index {VECTOR_INDEX!r} not queryable after {timeout}s")

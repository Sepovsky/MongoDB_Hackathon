"""Check the Atlas connection and list what MemoryDelta owns. Read-only."""
from memorydelta.config import DB_NAME, mongodb_uri
from memorydelta.db import connect

if not mongodb_uri():
    raise SystemExit("MONGODB_URI is not set. Copy .env.example to .env and fill it in.")
db, mode = connect()
print(f"pinged Atlas · database {DB_NAME!r} collections: {sorted(db.list_collection_names())}")

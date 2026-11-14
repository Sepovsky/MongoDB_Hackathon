import os
from dataclasses import dataclass
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

DB_NAME = os.getenv("MEMORYDELTA_DB", "memorydelta_demo")
VECTOR_INDEX = "memory_vec"
EMBED_DIM = 1024
MODEL = os.getenv("MEMORYDELTA_MODEL", "claude-opus-5-5")
METRIC = "scope2_electricity"
WINDOW_START = datetime(2025, 7, 1)   # Australian reporting year FY2025-26
WINDOW_END = datetime(2026, 7, 1)
PERIOD = "FY2025-26"
TASK = "What are our Scope 2 emissions from purchased electricity for FY2025-26?"

# Collections this project owns. Seeding only ever touches these, inside DB_NAME.
COLLECTIONS = ("usage", "policies", "memories", "memory_deltas", "runs")


def mongodb_uri():
    return os.getenv("MONGODB_URI") or None


def voyage_key():
    return os.getenv("VOYAGE_API_KEY") or None


def anthropic_configured():
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def now():
    """Naive UTC, matching what pymongo returns by default."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class RequestContext:
    """Trusted identity. Built by the application from a session, never by the model."""
    tenant_id: str
    user_id: str


# Demo session store: the application resolves a session token to an identity.
# The model and the user's text never get to choose the tenant.
SESSIONS = {
    "sess_acme_ana": RequestContext("acme", "ana"),
    "sess_acme_bob": RequestContext("acme", "bob"),
    "sess_globex_gus": RequestContext("globex", "gus"),
    "sess_initech_ivy": RequestContext("initech", "ivy"),
}


def authenticate(session_token: str) -> RequestContext:
    try:
        return SESSIONS[session_token]
    except KeyError:
        raise PermissionError(f"unknown session {session_token!r}") from None

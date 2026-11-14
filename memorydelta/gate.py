"""The validation gate: the only path for a memory to reach the model."""
from .config import RequestContext, now

REQUIRED_EVIDENCE = ("verified_filter_json", "source_ids", "result")


def evidence_complete(content):
    if not isinstance(content, dict):
        return False
    ev = content.get("critical_evidence") or {}
    deps = content.get("dependencies") or {}
    return all(ev.get(k) not in (None, "", []) for k in REQUIRED_EVIDENCE) and \
        deps.get("policy_key") is not None and deps.get("policy_version") is not None


def evaluate(doc, ctx: RequestContext, current_policy, at=None):
    """Returns (eligible, reason). Reads only metadata, so rejected content never leaves here."""
    at = at or now()
    if doc is None:
        return False, "not_found"
    if doc.get("tenant_id") != ctx.tenant_id:
        return False, "tenant_mismatch"
    allowed = doc.get("allowed_users") or []
    if allowed and ctx.user_id not in allowed:
        return False, "user_not_allowed"
    if doc.get("status") != "active":
        return False, f"status_{doc.get('status')}"
    if doc.get("expires_at") is not None and doc["expires_at"] <= at:
        return False, "expired"
    content = doc.get("content")
    if not evidence_complete(content):
        return False, "evidence_incomplete"
    deps = content["dependencies"]
    if current_policy is None:
        return False, "no_current_policy"
    if deps["policy_key"] != current_policy["metric"] or deps["policy_version"] != current_policy["version"]:
        return False, (f"policy_version_mismatch(memory=v{deps['policy_version']},"
                       f" current=v{current_policy['version']})")
    return True, "eligible"

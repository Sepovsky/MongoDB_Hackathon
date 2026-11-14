"""One repeatable demo: python -m memorydelta.demo
Reseeds the dedicated demo DB, then runs isolation, behavior change, policy change, correction,
revocation and the compression comparison. Prints what memory was sent, what was blocked, and
what the agent decided. Writes results/comparison.json."""
import argparse
import json
import pathlib
import sys

from rich.console import Console
from rich.table import Table

from .agent import run_agent
from .compress import compress, estimate_tokens, evidence_pointers, render_compact, render_raw
from .config import DB_NAME, TASK, authenticate
from .db import connect
from .embeddings import default_embedder
from .model import ScriptedModel, default_model
from .seed import activate_policy, seed
from .store import MemoryStore

con = Console()
MEM = "mem_acme_active_customers"
V2_FILTER = {"status": "paid", "refunded": False}


def fmt_sent(r):
    return ", ".join(f"{m['memory_id']}@v{m['version']}" for m in r.included) or "—"


def fmt_blocked(r):
    return ", ".join(f"{x['memory_id']}@v{x['version']}: {x['reason']}" for x in r.rejected) or "—"


def fmt_calls(r):
    return " → ".join(c["name"] + ("" if c["ok"] else "(err)") for c in r.tool_calls)


def answer(r):
    return r.decision["active_customers"] if r.decision else f"no answer ({r.stop_reason})"


def run_table(title, rows):
    t = Table(title=title, show_lines=True, title_justify="left")
    for col in ("who / arm", "memory sent to model", "blocked (reason)", "tool calls executed", "answer", "check"):
        t.add_column(col, overflow="fold")
    for label, r, ok in rows:
        t.add_row(label, fmt_sent(r), fmt_blocked(r), fmt_calls(r), str(answer(r)),
                  "[green]✓[/]" if ok else "[red]✗[/]")
    con.print(t)


def summary(r):
    out = {k: getattr(r, k) for k in ("run_id", "tenant_id", "memory_mode", "included", "rejected", "candidates",
                                      "decision", "decision_evidence", "first_input_tokens",
                                      "total_input_tokens", "token_source", "model", "stop_reason")}
    # Executed calls with compact outputs (counts and policy versions; schema samples dropped).
    out["tool_calls"] = [{"name": c["name"], "input": c["input"], "ok": c["ok"],
                          "output": {k: v for k, v in (c["output"] or {}).items()
                                     if k in ("active_customers", "version", "policy_id", "error")}}
                         for c in r.trace]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripted", action="store_true", help="force the scripted test double")
    args = ap.parse_args(argv)

    db, mode = connect()
    store = MemoryStore(db, mode, default_embedder())
    model = ScriptedModel() if args.scripted else default_model()
    con.rule("[bold]MemoryDelta demo")
    con.print(f"database: [bold]{'Atlas' if mode == 'atlas' else 'LOCAL in-memory (MONGODB_URI not set)'}[/] "
              f"· db={DB_NAME} · embedder={store.embedder.name}")
    con.print(f"model: [bold]{model.name}[/] ({'LIVE' if model.live else 'scripted test double - not evidence of model behavior'})")
    seed(store, log=lambda m: con.print(f"[dim]{m}[/]"))
    out = {"database_mode": mode, "embedder": store.embedder.name, "model": model.name,
           "model_live": model.live, "scenes": {}}

    ana, gus, ivy = (authenticate(s) for s in ("sess_acme_ana", "sess_globex_gus", "sess_initech_ivy"))

    # 1. Tenant isolation ------------------------------------------------------------------------
    con.rule("1 · Tenant isolation (tenant comes from the session, not the prompt)")
    a = run_agent(store, model, ana, TASK, scenario="isolation")
    g = run_agent(store, model, gus, TASK, scenario="isolation")
    i = run_agent(store, model, ivy, TASK, scenario="isolation")
    leak = run_agent(store, model, ana, TASK, memory_mode="unscoped", scenario="isolation-baseline")
    leaked = [m for m in leak.included if not m["memory_id"].startswith("mem_acme")]
    run_table("governed retrieval vs unscoped baseline", [
        ("acme/ana · governed", a, [m["memory_id"] for m in a.included] == [MEM]),
        ("globex/gus · governed", g, [m["memory_id"] for m in g.included] == ["mem_globex_active_customers"]),
        ("initech/ivy · governed (no memories)", i, not i.included and i.decision is not None),
        ("acme/ana · UNSCOPED baseline", leak, False if leaked else True),
    ])
    con.print(f"unscoped baseline sent {len(leaked)} foreign memor{'y' if len(leaked) == 1 else 'ies'} "
              f"to the model; governed runs sent 0.")
    out["scenes"]["isolation"] = {"acme": summary(a), "globex": summary(g), "initech": summary(i),
                                  "unscoped_baseline": summary(leak), "foreign_in_baseline": len(leaked)}

    # 2. Behavior change -------------------------------------------------------------------------
    con.rule("2 · Does approved memory change what the agent does?")
    expected = {"none": "discovers first (get_policy / inspect_collection) before counting",
                "governed": "first action is run_count_query with the memory's verified filter {}"}
    con.print("[bold]expected before running[/] (same task, model, settings and data; only memory differs):")
    for k, v in expected.items():
        con.print(f"  {k:9s} → {v}")
    off = run_agent(store, model, ana, TASK, memory_mode="none", scenario="behavior")
    on = run_agent(store, model, ana, TASK, memory_mode="governed", scenario="behavior")
    off_ok = off.first_action in ("get_policy", "inspect_collection")
    on_ok = bool(on.tool_calls) and on.tool_calls[0]["name"] == "run_count_query" and \
        on.tool_calls[0]["input"].get("filter") == {}
    run_table("observed", [("acme · no memory", off, off_ok), ("acme · approved memory", on, on_ok)])
    con.print(f"tool calls: no memory={len(off.tool_calls)} vs memory={len(on.tool_calls)} · "
              f"same answer: {answer(off) == answer(on)}")
    out["scenes"]["behavior"] = {"expected": expected, "no_memory": summary(off), "memory": summary(on),
                                 "matched_expected": {"no_memory": off_ok, "memory": on_ok}}

    # 3. Policy change ---------------------------------------------------------------------------
    con.rule("3 · Policy changes v1 → v2: the most similar memory is now stale")
    activate_policy(db, "acme", 2)
    stale = run_agent(store, model, ana, TASK, memory_mode="ungated", scenario="policy-change")
    gov = run_agent(store, model, ana, TASK, memory_mode="governed", scenario="policy-change")
    run_table("same question, after the authorized policy update (correct answer: 93)", [
        ("acme · UNGATED baseline", stale, answer(stale) == 93),
        ("acme · MemoryDelta gate", gov, answer(gov) == 93),
    ])
    out["scenes"]["policy_change"] = {"ungated": summary(stale), "governed": summary(gov)}

    # 4. Correction ------------------------------------------------------------------------------
    con.rule("4 · Correct the memory (new verified version; old one superseded)")
    new = store.correct_memory(MEM, V2_FILTER, actor="acme_lead", reason="metric policy v2")
    store.wait_searchable(ana, TASK, new["_id"])
    corr = run_agent(store, model, ana, TASK, scenario="correction")
    run_table("next run after correction", [
        ("acme · governed", corr, [(m["memory_id"], m["version"]) for m in corr.included] == [(MEM, 2)])])
    out["scenes"]["correction"] = summary(corr)

    # 5. Revocation ------------------------------------------------------------------------------
    con.rule("5 · Revoke the memory (excluded from future runs; embedding + content scrubbed)")
    n = store.revoke_memory(MEM, actor="acme_lead", reason="customer request")
    rev = run_agent(store, model, ana, TASK, scenario="revocation")
    run_table(f"next run after revoking {n} version(s)", [
        ("acme · governed", rev, not rev.included and rev.decision is not None)])
    con.print("[dim]if Vector Search still returns a revoked id (index lag), the _id re-read blocks it "
              "with status_revoked[/]")
    out["scenes"]["revocation"] = summary(rev)

    # 6. Compression -----------------------------------------------------------------------------
    con.rule("6 · Compression: raw trajectory vs evidence-preserving memory (same evidence)")
    policy = db.policies.find_one({"tenant_id": "acme", "active": True})
    content = compress(gov.trace, policy, gov.run_id)   # gov = acme's own authorized v2 run
    compact = render_compact({"memory_id": MEM + ".compact", "version": 1, "content": content})
    raw = render_raw(MEM + ".raw", 1, gov.trace,
                     "Agent read the policy, inspected the orders schema and samples, then counted.")
    t = Table(title="same model, task, data and governance; only the memory representation differs",
              title_justify="left")
    for col in ("representation", "memory tokens (est.)", "request input tokens", "expected action?",
                "answer", "evidence pointers resolve?"):
        t.add_column(col)
    comp = {}
    for label, rep in (("raw trajectory", raw), ("compact", compact)):
        r = run_agent(store, model, ana, TASK, rendered_override=[rep], scenario=f"compression-{label}")
        first = r.tool_calls[0] if r.tool_calls else {}
        exp = first.get("name") == "run_count_query" and first.get("input", {}).get("filter") == V2_FILTER \
            and answer(r) == 93
        ptrs = evidence_pointers(rep)
        resolved = all((db.runs if p.startswith("run:") else db.policies).find_one(
            {"_id": p[4:] if p.startswith("run:") else p}) for p in ptrs) and bool(ptrs)
        tok = f"{r.first_input_tokens} ({'actual' if r.token_source == 'api_usage' else 'estimate'})"
        t.add_row(label, str(estimate_tokens(rep)), tok, "✓" if exp else "✗", str(answer(r)),
                  f"{'✓' if resolved else '✗'} {ptrs}")
        comp[label] = {**summary(r), "memory_tokens_estimate": estimate_tokens(rep),
                       "expected_action": exp, "pointers": ptrs, "pointers_resolve": resolved}
    con.print(t)
    out["scenes"]["compression"] = comp

    pathlib.Path("results").mkdir(exist_ok=True)
    pathlib.Path("results/comparison.json").write_text(json.dumps(out, indent=2, default=str))
    con.rule("done · results/comparison.json written · every run is also in the `runs` collection")


if __name__ == "__main__":
    sys.exit(main())

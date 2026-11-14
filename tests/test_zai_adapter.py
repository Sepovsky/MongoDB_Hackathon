"""GLM adapter: request/response translation with a stub OpenAI client (no network)."""
import json
from types import SimpleNamespace as NS

from memorydelta.agent import run_agent
from memorydelta.config import TASK, authenticate
from memorydelta.model import MEM_RE, ZaiModel


class StubChat:
    """Behaves like a model that reuses the approved memory's verified parameters."""

    def __init__(self):
        self.requests = []

    def create(self, **body):
        self.requests.append(json.loads(json.dumps(body)))
        tool_msgs = [m for m in body["messages"] if m["role"] == "tool"]
        if not tool_msgs:
            mem = json.loads(MEM_RE.search(body["messages"][1]["content"]).group(1))[0]
            call = ("calculate_emissions", mem["verified_params"])
        else:
            n = json.loads(tool_msgs[-1]["content"])["emissions_t"]
            call = ("submit_answer", {"emissions_t": n, "factor_used": 0.66, "policy_version": 1,
                                      "memory_ids_used": ["mem_acme_scope2"]})
        tc = NS(id=f"call_{len(self.requests)}", function=NS(name=call[0], arguments=json.dumps(call[1])))
        return NS(choices=[NS(message=NS(content=None, tool_calls=[tc]))], usage=NS(prompt_tokens=321))


def test_glm_adapter_round_trip(store):
    stub = StubChat()
    model = ZaiModel(client=NS(chat=NS(completions=stub)), model="glm-test")
    r = run_agent(store, model, authenticate("sess_acme_ana"), TASK)
    first = stub.requests[0]
    assert first["messages"][0]["role"] == "system"
    assert "mem_acme_scope2" in first["messages"][1]["content"]
    assert "mem_globex_scope2" not in json.dumps(first)
    assert {t["function"]["name"] for t in first["tools"]} == {
        "get_emission_factor", "inspect_usage", "calculate_emissions", "submit_answer"}
    second = stub.requests[1]["messages"]
    assert second[2]["tool_calls"][0]["function"]["name"] == "calculate_emissions"
    assert second[3]["role"] == "tool" and second[3]["tool_call_id"] == second[2]["tool_calls"][0]["id"]
    assert [c["name"] for c in r.tool_calls] == ["calculate_emissions", "submit_answer"]
    assert r.decision["emissions_t"] == 66.0
    assert r.token_source == "api_usage" and r.first_input_tokens == 321

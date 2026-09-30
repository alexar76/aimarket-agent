"""The A2A client puts each payment rail on the wire the way the hub reads it.

These run against a scripted transport, so they pin the CLIENT's half of the contract:
headers, parts and metadata. The hub's suite (aimarket-hub/tests/test_a2a_invoke.py) drives
this same client against the real app for the other half.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from aimarket_agent import A2AClient, A2AError, AgentKey, Mandate, issue_mandate
from aimarket_agent.a2a import payment_required, task_result, task_state
from aimarket_agent.mandates import public_key_of, request_message

HUB = "https://hub.test"


def _task(state: str, **extra) -> dict:
    return {"id": "a2at_" + "0" * 24, "contextId": "ctx", "status": {"state": state}, **extra}


class Hub:
    """Records every request and answers from a script."""

    def __init__(self, *answers: dict):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else {"result": {}}
        body = {"jsonrpc": "2.0", "id": json.loads(request.content)["id"], **answer}
        return httpx.Response(200, json=body)

    def rpc(self, n: int = -1) -> dict:
        return json.loads(self.requests[n].content)


def _client(hub: Hub, **kw) -> A2AClient:
    client = A2AClient(HUB, verify_receipts=False, **kw)
    client.session = httpx.Client(transport=httpx.MockTransport(hub))
    return client


def _proof_verifies(mandate: Mandate, headers: httpx.Headers, *, path: str, body: bytes) -> bool:
    fields = dict(part.split("=", 1) for part in headers["X-AIMarket-Mandate-Proof"].split(";"))
    message = request_message(hub_origin=HUB, method="POST", path=path, digest=mandate.digest,
                              t=int(fields["t"]), nonce=fields["n"], body=body)
    try:
        signature = base64.urlsafe_b64decode(fields["s"] + "=" * (-len(fields["s"]) % 4))
        public_key_of(mandate.key.did).verify(signature, message)
    except Exception:
        return False
    return True


def test_every_call_speaks_a2a_1_0_json_rpc():
    hub = Hub({"result": {"message": {"parts": [{"text": "found"}, {"data": {"matches": [{"capability_id": "x"}]}}]}}})
    found = _client(hub).search("weather", limit=3, budget=0.01)
    assert found == {"matches": [{"capability_id": "x"}]}
    request = hub.requests[0]
    assert str(request.url) == f"{HUB}/a2a"
    assert request.headers["A2A-Version"] == "1.0"
    rpc = hub.rpc()
    assert rpc["jsonrpc"] == "2.0" and rpc["method"] == "SendMessage"
    assert rpc["params"]["message"]["parts"] == [{"data": {"intent": "weather", "limit": 3, "budget": 0.01},
                                                   "mediaType": "application/json"}]


def test_credits_ride_as_a_header_and_the_invoke_as_a_data_part():
    hub = Hub({"result": {"task": _task("TASK_STATE_COMPLETED", artifacts=[
        {"artifactId": "result", "name": "result", "parts": [{"data": {"ok": True}}]}])}})
    task = _client(hub, api_key="amk_test").invoke("p-1", "p.cap@v1", {"q": 1}, max_price_usd=0.02,
                                                   message_id="m-1", context_id="c-1")
    assert task_state(task) == "TASK_STATE_COMPLETED" and task_result(task) == {"ok": True}
    assert hub.requests[0].headers["X-API-Key"] == "amk_test"
    message = hub.rpc()["params"]["message"]
    assert message["messageId"] == "m-1" and message["contextId"] == "c-1"
    assert message["parts"][1] == {"data": {"invoke": {
        "product_id": "p-1", "capability_id": "p.cap@v1", "source_hub": "local", "input": {"q": 1},
        "max_price_usd": 0.02}}, "mediaType": "application/json"}


def test_a_mandate_signs_the_exact_bytes_it_sends_as_a_raw_part():
    owner, agent = AgentKey.generate(), AgentKey.generate()
    doc = issue_mandate(owner, agent.did, audience=[HUB], scope=["*"], per_call_usd=0.01, per_day_usd=0.1)
    mandate = Mandate(document=doc, key=agent, hub_origin=HUB)
    hub = Hub({"result": {"task": _task("TASK_STATE_COMPLETED")}})
    _client(hub, mandate=mandate).invoke("p-1", "p.cap@v1", {"q": 1.5})
    request = hub.requests[0]
    raw_part = hub.rpc()["params"]["message"]["parts"][1]
    assert raw_part["mediaType"] == "application/json" and "data" not in raw_part
    raw = base64.b64decode(raw_part["raw"])
    assert json.loads(raw)["input"] == {"q": 1.5}   # a float: JCS could not have signed this
    assert request.headers["X-AIMarket-Mandate"] == mandate.digest
    assert "X-API-Key" not in request.headers
    assert _proof_verifies(mandate, request.headers, path="/ai-market/v2/invoke", body=raw)


def test_a_mandate_reads_its_tasks_with_a_proof_over_the_rpc_body():
    owner, agent = AgentKey.generate(), AgentKey.generate()
    doc = issue_mandate(owner, agent.did, audience=[HUB], scope=["*"], per_call_usd=0.01, per_day_usd=0.1)
    mandate = Mandate(document=doc, key=agent, hub_origin=HUB)
    hub = Hub({"result": _task("TASK_STATE_COMPLETED")}, {"result": {"tasks": [], "nextPageToken": ""}})
    client = _client(hub, mandate=mandate)
    client.get_task("a2at_" + "0" * 24)
    client.list_tasks()
    for request in hub.requests:
        assert _proof_verifies(mandate, request.headers, path="/a2a", body=request.content)
    first, second = (r.headers["X-AIMarket-Mandate-Proof"] for r in hub.requests)
    assert first != second, "each proof is single-use"


def test_an_x402_payment_is_a_follow_up_on_the_same_task():
    terms = {"x402Version": 2, "accepts": [{"scheme": "exact", "amount": "4000", "payTo": "0x" + "cd" * 20}]}
    waiting = _task("TASK_STATE_INPUT_REQUIRED", status={"state": "TASK_STATE_INPUT_REQUIRED", "message": {
        "role": "ROLE_AGENT", "parts": [{"text": "pay"}],
        "metadata": {"x402.payment.status": "payment-required", "x402.payment.required": terms}}})
    hub = Hub({"result": {"task": _task("TASK_STATE_COMPLETED")}})
    client = _client(hub)
    assert payment_required(waiting) == terms
    payload = {"x402Version": 2, "scheme": "exact", "txHash": "0x" + "11" * 32}
    client.pay_x402(waiting, payload, message_id="pay-1")
    message = hub.rpc()["params"]["message"]
    assert message["taskId"] == waiting["id"] and message["contextId"] == "ctx"
    assert message["messageId"] == "pay-1" and message["parts"]
    assert message["metadata"] == {"x402.payment.status": "payment-submitted", "x402.payment.payload": payload}


def test_declining_a_price_is_the_standard_rejection():
    hub = Hub({"result": {"task": _task("TASK_STATE_CANCELED")}})
    assert task_state(_client(hub).reject_payment(_task("TASK_STATE_INPUT_REQUIRED"))) == "TASK_STATE_CANCELED"
    assert hub.rpc()["params"]["message"]["metadata"] == {"x402.payment.status": "payment-rejected"}


def test_resuming_with_the_complete_input_resends_the_same_capability():
    hub = Hub({"result": {"task": _task("TASK_STATE_INPUT_REQUIRED")}}, {"result": {"task": _task("TASK_STATE_COMPLETED")}})
    client = _client(hub, api_key="amk_test")
    task = client.invoke("p-1", "p.cap@v1", {})
    client.resume(task, input_payload={"text": "hi"})
    message = hub.rpc()["params"]["message"]
    assert message["taskId"] == task["id"]
    assert message["parts"][-1]["data"]["invoke"] == {"product_id": "p-1", "capability_id": "p.cap@v1",
                                                       "source_hub": "local", "input": {"text": "hi"}}


def test_errors_are_raised_with_their_a2a_reason():
    hub = Hub({"error": {"code": -32001, "message": "Task not found",
                         "data": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "TASK_NOT_FOUND"}]}})
    with pytest.raises(A2AError) as raised:
        _client(hub).get_task("a2at_" + "1" * 24)
    assert raised.value.code == -32001 and raised.value.reason == "TASK_NOT_FOUND"


def test_the_receipt_is_checked_against_the_signers_origin():
    receipt = {"nonce": "rcpt_1", "signature": {"algorithm": "ed25519", "value": "AA=="}}
    hub = Hub({"result": {"task": _task("TASK_STATE_COMPLETED", artifacts=[
        {"artifactId": "aimarket-receipt", "name": "aimarket-receipt", "parts": [{"data": receipt}],
         "metadata": {"sourceHub": "https://peer.test"}}])}})
    client = _client(hub)
    client.verify_receipts = True
    asked: list[str] = []

    class Verifiers:
        def verify(self, got, *, source_hub=""):
            asked.append(source_hub)
            assert got == receipt

            class Verdict:
                reason = "invalid-signature"

                def __bool__(self):
                    return False
            return Verdict()

    client._verifiers = Verifiers()
    task = client.invoke("p-1", "p.cap@v1", {}, source_hub="https://peer.test")
    assert asked == ["https://peer.test"]
    assert task["receipt_verified"] is False and task["receipt_verify_reason"] == "invalid-signature"


"""Pay-on-Verified tests — verify block threading, envelope surfacing, polling.

These prove the SDK sends the opt-in `verify` block exactly as given (and omits
it entirely when absent, so older hubs see an unchanged body), surfaces the
hub's `verification` envelope on every result, and polls the verdict lookup
with exponential backoff and no overall deadline by default — mirroring the
hub's own no-deadline settlement policy. Hub HTTP is mocked with pytest-httpx.
"""

import json
import re
import sys
from pathlib import Path

import httpx

# Make the SDK importable from the monorepo checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aimarket_agent.agent as agent_module
from aimarket_agent.agent import AIMarketAgent

_HUB = "http://hub.test"


def _agent(**over):
    kwargs = {"base_url": _HUB, "verify_receipts": False, **over}
    return AIMarketAgent(**kwargs)


def _mock_no_channel(httpx_mock):
    """Hub without the channels plugin: open returns 404, close is skipped."""
    httpx_mock.add_response(method="POST", url=f"{_HUB}/ai-market/v2/channel/open", status_code=404)


def _mock_run_stack(httpx_mock, invoke_json):
    """Register the full run() request sequence for a one-step plan."""
    httpx_mock.add_response(method="GET", url=f"{_HUB}/.well-known/ai-market.json", json={})
    httpx_mock.add_response(
        method="GET",
        url=re.compile(rf"{_HUB}/ai-market/v2/search\?.*"),
        json={"matches": [{
            "product_id": "prod-x", "capability_id": "cap.y@v2",
            "source_hub": "local", "price_per_call_usd": 0.4,
        }]},
    )
    _mock_no_channel(httpx_mock)
    httpx_mock.add_response(method="POST", url=f"{_HUB}/ai-market/v2/invoke", json=invoke_json)


def _invoke_body(httpx_mock):
    """The JSON body the SDK sent to /ai-market/v2/invoke."""
    req = [r for r in httpx_mock.get_requests() if r.url.path == "/ai-market/v2/invoke"][0]
    return json.loads(req.content)


class _FakeTime:
    """Deterministic stand-in for agent.time — records sleeps, advances a clock."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def time(self):  # _open_channel uses time.time() for the tx hash
        return self.now


def _fake_time(monkeypatch):
    fake = _FakeTime()
    monkeypatch.setattr(agent_module, "time", fake)
    return fake


# ── verify block serialization ─────────────────────────────────


def test_invoke_single_sends_verify_block(httpx_mock):
    _mock_no_channel(httpx_mock)
    httpx_mock.add_response(
        method="POST", url=f"{_HUB}/ai-market/v2/invoke",
        json={"success": True, "result": {}, "price_usd": 0.4},
    )
    block = {"requested": True, "intent": "translate to French", "mode": "auto"}
    with _agent() as agent:
        agent.invoke_single("prod-x", "cap.y@v2", {"text": "hi"}, verify=block)
    assert _invoke_body(httpx_mock)["verify"] == block


def test_invoke_single_omits_verify_by_default(httpx_mock):
    _mock_no_channel(httpx_mock)
    httpx_mock.add_response(
        method="POST", url=f"{_HUB}/ai-market/v2/invoke",
        json={"success": True, "result": {}, "price_usd": 0.4},
    )
    with _agent() as agent:
        agent.invoke_single("prod-x", "cap.y@v2", {"text": "hi"})
    assert "verify" not in _invoke_body(httpx_mock)


def test_run_threads_verify_and_defaults_intent_to_task(httpx_mock):
    _mock_run_stack(httpx_mock, {"success": True, "result": {}, "price_usd": 0.4})
    with _agent() as agent:
        agent.run("translate spec", verify={"requested": True, "wait": False})
    sent = _invoke_body(httpx_mock)["verify"]
    assert sent == {"requested": True, "wait": False, "intent": "translate spec"}


def test_run_keeps_explicit_intent_and_callers_dict_unmutated(httpx_mock):
    _mock_run_stack(httpx_mock, {"success": True, "result": {}, "price_usd": 0.4})
    block = {"requested": True, "intent": "judge the legal review"}
    with _agent() as agent:
        agent.run("translate spec", verify=block)
    assert _invoke_body(httpx_mock)["verify"]["intent"] == "judge the legal review"
    assert block == {"requested": True, "intent": "judge the legal review"}


def test_run_omits_verify_by_default(httpx_mock):
    _mock_run_stack(httpx_mock, {"success": True, "result": {}, "price_usd": 0.4})
    with _agent() as agent:
        agent.run("translate spec")
    assert "verify" not in _invoke_body(httpx_mock)


# ── verification envelope surfacing ─────────────────────────────


def test_invoke_single_passes_verification_envelope_through(httpx_mock):
    _mock_no_channel(httpx_mock)
    envelope = {"requested": True, "status": "pending", "performed": False, "verified": None}
    httpx_mock.add_response(
        method="POST", url=f"{_HUB}/ai-market/v2/invoke",
        json={"success": True, "result": {}, "price_usd": 0.4, "verification": envelope},
    )
    with _agent() as agent:
        result = agent.invoke_single("prod-x", "cap.y@v2", {}, verify={"requested": True})
    assert result["verification"] == envelope


def test_invoke_single_lifts_envelope_from_receipt(httpx_mock):
    _mock_no_channel(httpx_mock)
    envelope = {"status": "settled", "verified": True, "verify_score": 0.93}
    httpx_mock.add_response(
        method="POST", url=f"{_HUB}/ai-market/v2/invoke",
        json={"success": True, "result": {}, "price_usd": 0.4,
              "receipt": {"nonce": "rcpt_1", "verification": envelope}},
    )
    with _agent() as agent:
        result = agent.invoke_single("prod-x", "cap.y@v2", {})
    assert result["verification"] == envelope


def test_run_surfaces_envelope_in_results_and_bom(httpx_mock):
    envelope = {"requested": True, "status": "pending"}
    _mock_run_stack(httpx_mock, {
        "success": True, "result": {}, "price_usd": 0.4,
        "receipt": {"nonce": "rcpt_1", "verification": envelope},
        "verification": envelope,
    })
    with _agent() as agent:
        result = agent.run("translate spec", verify={"requested": True})
    step = result["bill_of_materials"]["results"][0]
    assert step["verification"] == envelope


# ── verdict lookup + polling helper ─────────────────────────────


def _lookup(json_body, status_code=200):
    return {"method": "GET", "url": f"{_HUB}/ai-market/v2/verification/rcpt_1",
            "json": json_body, "status_code": status_code}


_PENDING = {"success": True, "verification": {"status": "pending"}, "protocol_version": "v2"}
_SETTLED = {"success": True, "verification": {"status": "settled", "verified": True},
            "protocol_version": "v2"}


def test_get_verification_returns_hub_body(httpx_mock):
    httpx_mock.add_response(**_lookup(_SETTLED))
    with _agent() as agent:
        assert agent.get_verification("rcpt_1") == _SETTLED


def test_get_verification_never_raises_on_transport_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    with _agent() as agent:
        body = agent.get_verification("rcpt_1")
    assert body["success"] is False
    assert "verification lookup failed" in body["error"]


def test_wait_for_verification_backs_off_exponentially_to_cap(httpx_mock, monkeypatch):
    fake = _fake_time(monkeypatch)
    for _ in range(4):
        httpx_mock.add_response(**_lookup(_PENDING))
    httpx_mock.add_response(**_lookup(_SETTLED))
    with _agent() as agent:
        body = agent.wait_for_verification("rcpt_1", initial_backoff_s=2.0, max_backoff_s=5.0)
    assert body["verification"]["status"] == "settled"
    assert fake.sleeps == [2.0, 4.0, 5.0, 5.0]


def test_wait_for_verification_returns_refunded_verdict(httpx_mock, monkeypatch):
    fake = _fake_time(monkeypatch)
    refunded = {"success": True,
                "verification": {"status": "refunded", "verified": False},
                "rejection_receipt": {"type": "verification_rejection"}}
    httpx_mock.add_response(**_lookup(refunded))
    with _agent() as agent:
        body = agent.wait_for_verification("rcpt_1")
    assert body["verification"]["status"] == "refunded"
    assert body["rejection_receipt"]["type"] == "verification_rejection"
    assert fake.sleeps == []


def test_wait_for_verification_unknown_nonce_is_definitive(httpx_mock, monkeypatch):
    fake = _fake_time(monkeypatch)
    not_found = {"success": False, "error": "verification_not_found"}
    httpx_mock.add_response(**_lookup(not_found, status_code=404))
    with _agent() as agent:
        assert agent.wait_for_verification("rcpt_1") == not_found
    assert fake.sleeps == []


def test_wait_for_verification_retries_transport_errors(httpx_mock, monkeypatch):
    fake = _fake_time(monkeypatch)
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    httpx_mock.add_response(**_lookup(_SETTLED))
    with _agent() as agent:
        body = agent.wait_for_verification("rcpt_1", initial_backoff_s=2.0)
    assert body["verification"]["status"] == "settled"
    assert fake.sleeps == [2.0]


def test_wait_for_verification_honors_max_wait_deadline(httpx_mock, monkeypatch):
    fake = _fake_time(monkeypatch)
    for _ in range(3):
        httpx_mock.add_response(**_lookup(_PENDING))
    with _agent() as agent:
        body = agent.wait_for_verification("rcpt_1", initial_backoff_s=2.0, max_wait_s=5.0)
    # Polls at t=0/2/5; the last sleep is clamped to the remaining 3s window.
    assert body["verification"]["status"] == "pending"
    assert fake.sleeps == [2.0, 3.0]

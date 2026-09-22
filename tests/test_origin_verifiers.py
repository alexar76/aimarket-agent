"""Receipts must be checked against the key of whoever signed them.

The bug this closes, measured against modelmarket.dev on 2026-07-29: the SDK held ONE
verifier, built from the hub's well-known document, and used it for every receipt. A hub is
a broker, so a federated capability's receipt carries the PROVIDER's signature — different
key, and the check failed. 42 of the 47 live capabilities are federated, so the SDK reported
`invalid-signature` for 89% of the catalogue, on receipts that were valid.

A false alarm on the one guarantee the protocol is sold on is worse than no check, because it
teaches the reader that the signal means nothing. These tests use two REAL signers with two
real key pairs, so a regression cannot pass by comparing a stub against itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aimarket_agent.receipts import OriginVerifiers, VerifyResult  # noqa: E402

HUB = "https://hub.test"
ORIGIN = "https://oracles.test/family"


def _signer(tmp_path, name):
    """A real hub Signer, so the signatures under test are genuine."""
    repo = Path(__file__).resolve().parents[2]
    hub = repo / "aimarket-hub"
    if hub.is_dir() and str(hub) not in sys.path:
        sys.path.insert(0, str(hub))
    try:
        from aimarket_hub.signing import Signer
    except Exception:
        pytest.skip("aimarket-hub not importable in this environment")
    return Signer(str(tmp_path / name))


def _payload(**over):
    base = {
        "nonce": "rcpt_1", "product_id": "prod-platon", "capability_id": "platon.random@v1",
        "price_usd": 0.004, "timestamp": "2026-07-29T00:00:00Z", "success": True,
        "latency_ms": 12,
    }
    base.update(over)
    return base


def _sign(signer, **over):
    payload = _payload(**over)
    return {**payload, "signature": signer.sign_receipt(payload)}


def _session(keys: dict[str, str], *, fail: set[str] | None = None) -> httpx.Client:
    """A network where each origin publishes its own well-known document.

    `keys` maps a well-known URL to the key it serves. Anything else 404s, which is what an
    origin that publishes nothing looks like.
    """
    fail = fail or set()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in fail:
            return httpx.Response(500, json={"error": "boom"})
        if url in keys:
            return httpx.Response(200, json={"signer_public_key": keys[url]})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestTheBugItself:
    def test_a_federated_receipt_verifies_against_its_origin(self, tmp_path):
        """The exact case 2.1.x got wrong."""
        hub_signer = _signer(tmp_path, "hub")
        oracle = _signer(tmp_path, "oracle")
        assert hub_signer.public_key_b64 != oracle.public_key_b64, "two distinct keys needed"

        receipt = _sign(oracle)
        verifiers = OriginVerifiers(HUB, _session({
            f"{HUB}/.well-known/ai-market.json": hub_signer.public_key_b64,
            f"{ORIGIN}/.well-known/ai-market.json": oracle.public_key_b64,
        }))
        result = verifiers.verify(receipt, source_hub=ORIGIN)
        assert bool(result) is True, result.reason

    def test_the_old_behaviour_would_have_failed_it(self, tmp_path):
        """Pins the regression: the hub's key must NOT verify an oracle's receipt.

        If this ever passes, the two signers are sharing a key and every other test here is
        vacuous.
        """
        from aimarket_agent.receipts import verify_receipt

        hub_signer = _signer(tmp_path, "hub")
        oracle = _signer(tmp_path, "oracle")
        result = verify_receipt(_sign(oracle), hub_signer.public_key_b64)
        assert bool(result) is False
        assert result.reason == "invalid-signature"

    def test_a_local_receipt_still_verifies_against_the_hub(self, tmp_path):
        """The case 2.1.x got right must keep working."""
        hub_signer = _signer(tmp_path, "hub")
        verifiers = OriginVerifiers(HUB, _session({
            f"{HUB}/.well-known/ai-market.json": hub_signer.public_key_b64,
        }))
        for origin in ("", "local"):
            assert bool(verifiers.verify(_sign(hub_signer), source_hub=origin)) is True

    def test_tampering_is_still_caught_at_the_right_origin(self, tmp_path):
        """Resolving the correct key must not become "accept anything"."""
        oracle = _signer(tmp_path, "oracle")
        receipt = _sign(oracle)
        receipt["price_usd"] = 99.0
        verifiers = OriginVerifiers(HUB, _session({
            f"{ORIGIN}/.well-known/ai-market.json": oracle.public_key_b64,
        }))
        result = verifiers.verify(receipt, source_hub=ORIGIN)
        assert bool(result) is False and result.reason == "invalid-signature"


class TestKeyResolution:
    def test_a_path_scoped_origin_is_tried_before_the_root(self, tmp_path):
        """The oracle family publishes at …/family/.well-known, not at the domain root.

        Trying only the root would find the wrong document — or none — for every federated
        provider mounted under a path.
        """
        oracle = _signer(tmp_path, "oracle")
        wrong = _signer(tmp_path, "wrong")
        verifiers = OriginVerifiers(HUB, _session({
            f"{ORIGIN}/.well-known/ai-market.json": oracle.public_key_b64,
            "https://oracles.test/.well-known/ai-market.json": wrong.public_key_b64,
        }))
        assert verifiers.verifier_for(ORIGIN).public_key_b64 == oracle.public_key_b64

    def test_the_root_is_the_fallback(self, tmp_path):
        oracle = _signer(tmp_path, "oracle")
        verifiers = OriginVerifiers(HUB, _session({
            "https://oracles.test/.well-known/ai-market.json": oracle.public_key_b64,
        }))
        assert verifiers.verifier_for(ORIGIN).public_key_b64 == oracle.public_key_b64

    def test_each_origin_is_fetched_once(self, tmp_path):
        """A per-invoke well-known fetch would double the request count of every call."""
        oracle = _signer(tmp_path, "oracle")
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json={"signer_public_key": oracle.public_key_b64})

        verifiers = OriginVerifiers(HUB, httpx.Client(transport=httpx.MockTransport(handler)))
        for _ in range(5):
            verifiers.verify(_sign(oracle), source_hub=ORIGIN)
        assert len(calls) == 1, calls

    def test_a_failed_lookup_is_also_cached(self, tmp_path):
        """Otherwise every call to a keyless origin re-pays two failed HTTP round trips."""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(404)

        verifiers = OriginVerifiers(HUB, httpx.Client(transport=httpx.MockTransport(handler)))
        for _ in range(4):
            verifiers.verify(_sign(_signer(tmp_path, "x")), source_hub=ORIGIN)
        assert len(calls) == 2, f"two candidate URLs, tried once: {calls}"

    def test_two_origins_do_not_share_a_key(self, tmp_path):
        a, b = _signer(tmp_path, "a"), _signer(tmp_path, "b")
        other = "https://other.test/hub"
        verifiers = OriginVerifiers(HUB, _session({
            f"{ORIGIN}/.well-known/ai-market.json": a.public_key_b64,
            f"{other}/.well-known/ai-market.json": b.public_key_b64,
        }))
        assert bool(verifiers.verify(_sign(a), source_hub=ORIGIN)) is True
        assert bool(verifiers.verify(_sign(b), source_hub=other)) is True
        assert bool(verifiers.verify(_sign(a), source_hub=other)) is False, (
            "a receipt must not verify against a different origin's key"
        )


class TestHonestFailures:
    def test_no_published_key_is_not_reported_as_a_bad_signature(self, tmp_path):
        """"Could not look" and "the signature is wrong" demand opposite reactions.

        Conflating them is precisely how the old false alarm stayed invisible for so long.
        """
        verifiers = OriginVerifiers(HUB, _session({}))
        result = verifiers.verify(_sign(_signer(tmp_path, "o")), source_hub=ORIGIN)
        assert bool(result) is False
        assert result.reason.startswith(OriginVerifiers.NO_KEY)
        assert ORIGIN in result.reason, "an operator needs to know WHICH origin"
        assert "invalid-signature" not in result.reason

    def test_an_unreachable_origin_says_so(self, tmp_path):
        verifiers = OriginVerifiers(HUB, _session(
            {}, fail={f"{ORIGIN}/.well-known/ai-market.json"}))
        result = verifiers.verify(_sign(_signer(tmp_path, "o")), source_hub=ORIGIN)
        assert result.reason.startswith(OriginVerifiers.NO_KEY)

    def test_a_document_without_a_key_counts_as_no_key(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"name": "a hub with no signer_public_key"})

        verifiers = OriginVerifiers(HUB, httpx.Client(transport=httpx.MockTransport(handler)))
        assert verifiers.verifier_for(ORIGIN) is None

    def test_no_receipt_is_its_own_reason(self):
        verifiers = OriginVerifiers(HUB, _session({}))
        assert verifiers.verify(None, source_hub=ORIGIN).reason == "no-receipt"

    def test_keys_are_inspectable(self, tmp_path):
        """An operator debugging a verification failure needs to see which key was used."""
        oracle = _signer(tmp_path, "oracle")
        verifiers = OriginVerifiers(HUB, _session({
            f"{ORIGIN}/.well-known/ai-market.json": oracle.public_key_b64,
        }))
        verifiers.verify(_sign(oracle), source_hub=ORIGIN)
        assert verifiers.keys() == {ORIGIN: oracle.public_key_b64}


class TestTheAgentUsesIt:
    def test_verify_receipt_still_accepts_one_argument(self, tmp_path):
        """Backward compatibility: 2.1.x callers passed only the receipt."""
        import inspect

        from aimarket_agent.agent import AIMarketAgent

        signature = inspect.signature(AIMarketAgent.verify_receipt)
        assert signature.parameters["source_hub"].default == "", (
            "source_hub must be optional or every existing call breaks"
        )

    def test_invoke_single_forwards_the_origin(self):
        """The fix is worthless if the call site does not pass the origin along."""
        import inspect

        from aimarket_agent.agent import AIMarketAgent

        source = inspect.getsource(AIMarketAgent.invoke_single)
        assert "source_hub=source_hub" in source

    def test_run_verifies_each_step_against_its_own_origin(self):
        import inspect

        from aimarket_agent.agent import AIMarketAgent

        source = inspect.getsource(AIMarketAgent.run)
        assert "source_hub=source_hub" in source
        assert "self._verifier " not in source, "the single hub-wide verifier must be gone"

    def test_the_version_says_it_changed(self):
        """A behaviour change that reports differently must not ship as a patch."""
        import aimarket_agent

        assert aimarket_agent.__version__.startswith("2.2"), aimarket_agent.__version__

    def test_replacing_the_agents_session_is_honoured(self, tmp_path):
        """`.session` is the only injection point the agent offers, and binding the client
        into OriginVerifiers at construction meant a swap left key lookups on the discarded
        client — every receipt came back unverified with `no-origin-key`, while the invokes
        themselves used the new session. Fails closed, so nothing was wrongly trusted; it is
        this release's own false alarm arriving by another route."""
        from aimarket_agent.agent import AIMarketAgent

        signer = _signer(tmp_path, "hub")
        agent = AIMarketAgent(base_url=HUB, budget=1.0)
        # The client the agent built itself serves nothing, so this must fail first...
        assert agent._verifiers.verifier_for(HUB) is None

        # ...then the caller swaps in a client that does serve the key.
        agent.session = _session({f"{HUB}/.well-known/ai-market.json": signer.public_key_b64})
        agent._verifiers._misses.clear()  # the miss above is cached; a real caller would swap first
        verifier = agent._verifiers.verifier_for(HUB)
        assert verifier is not None and verifier.available, (
            "the replaced session must be used — otherwise verification silently dies"
        )
        assert verifier.verify(_sign(signer)).verified is True

    def test_a_key_lookup_does_not_inherit_the_invoke_timeout(self):
        """No per-call timeout meant a key fetch inherited the agent's 120 s client default and
        tried two URLs, so one blocked origin could stall 240 s inside the sequential run loop —
        and MISS_TTL_S was 60 s, shorter than that worst case, so the next receipt from the same
        origin paid the stall again instead of reusing the cached miss."""
        from aimarket_agent.receipts import OriginVerifiers

        assert OriginVerifiers.KEY_FETCH_TIMEOUT_S <= 10.0
        worst_case = OriginVerifiers.KEY_FETCH_TIMEOUT_S * 2  # two candidate URLs per origin
        assert OriginVerifiers.MISS_TTL_S > worst_case, (
            "a miss TTL shorter than the failure it caches makes the cache useless"
        )

    def test_the_timeout_reaches_the_request(self, tmp_path):
        """A constant nobody passes is decoration."""
        seen: list[float | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.extensions.get("timeout", {}).get("connect"))
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        OriginVerifiers(HUB, client).verifier_for(HUB)
        assert seen, "no request was made"
        assert all(t == OriginVerifiers.KEY_FETCH_TIMEOUT_S for t in seen), seen

    def test_the_package_and_pyproject_agree(self):
        """2.1.2 on PyPI shipped with __version__ saying 2.1.1."""
        import re

        import aimarket_agent

        text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
        declared = re.search(r'^version = "([^"]+)"', text, re.M).group(1)
        assert declared == aimarket_agent.__version__, (
            f"pyproject says {declared}, package says {aimarket_agent.__version__}"
        )

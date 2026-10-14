"""The SDK's mandate support agrees with the protocol byte for byte.

The strongest check is the first class: from the vectors' seeds the SDK rebuilds the SAME
documents, digests and signatures that the AWR reference implementation produced — so a
mandate issued with this package verifies at any conformant hub, and a hub can never see a
document from this SDK that the reference would have canonicalized differently.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import httpx
import pytest

from aimarket_agent import AIMarketAgent, AgentKey, JobContext, Mandate, issue_mandate, mandate_digest
from aimarket_agent.mandates import canonicalize, request_proof, sign_document, verify_document

VECTORS = Path(__file__).resolve().parents[2] / "aimarket-protocol" / "test-vectors" / "mandate-signed.json"


@pytest.fixture(scope="module")
def vectors():
    if not VECTORS.exists():
        pytest.skip("the normative vectors live in the monorepo's aimarket-protocol/")
    return json.loads(VECTORS.read_text())


class TestNormativeVectors:
    def test_keys_resolve_to_the_same_dids(self, vectors):
        for name in ("owner", "agent", "delegate"):
            k = vectors["keys"][name]
            assert AgentKey.from_seed_hex(k["seed_hex"]).did == k["did"]

    def test_the_sdk_signs_the_reference_documents_identically(self, vectors):
        for name, issuer in (("root", "owner"), ("child", "agent")):
            expected = vectors[name]["document"]
            unsigned = {k: v for k, v in expected.items() if k != "proof"}
            key = AgentKey.from_seed_hex(vectors["keys"][issuer]["seed_hex"])
            rebuilt = sign_document(unsigned, key, created=expected["proof"]["created"])
            assert rebuilt == expected
            assert mandate_digest(rebuilt) == vectors[name]["digest"]
            assert verify_document(rebuilt)

    def test_the_owner_change_and_revocation_match(self, vectors):
        from aimarket_agent.mandates import owner_change_authorization, revoke_payload

        owner = AgentKey.from_seed_hex(vectors["keys"]["owner"]["seed_hex"])
        c, r = vectors["owner_change"], vectors["revoke"]
        auth = owner_change_authorization(owner, hub_origin=vectors["hub_origin"], account_id=c["account_id"],
                                          action=c["action"], did=c["did"], require_mandate=bool(c["require_mandate"]),
                                          t=c["t"], nonce=c["n"])
        assert auth == {"by": c["by"], "t": c["t"], "n": c["n"], "s": c["signature"]}
        body = revoke_payload(owner, hub_origin=vectors["hub_origin"], digest=r["digest"], t=r["t"], nonce=r["n"])
        assert body["s"] == r["signature"] and body["by"] == r["by"]

    def test_the_request_proof_matches(self, vectors):
        p = vectors["request_proof"]
        delegate = AgentKey.from_seed_hex(vectors["keys"]["delegate"]["seed_hex"])
        header = request_proof(delegate, hub_origin=vectors["hub_origin"], leaf_digest=p["leaf"],
                               body=p["body"].encode(), t=p["t"], nonce=p["n"])
        assert header == p["header"]


class TestCanonicalizer:
    def test_it_matches_rfc8785_on_awkward_strings(self):
        awr = pytest.importorskip("awr.jcs")
        rng = random.Random(8785)
        alphabet = ['a', 'Z', '"', '\\', '/', '\n', '\t', '\x01', '\x1f', 'é', 'ß', '€', '中', '😀', ' ', '﻿']
        for _ in range(300):
            doc = {
                "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 6))): rng.randint(-10**12, 10**12)
                for _ in range(rng.randint(1, 6))
            }
            doc["nested"] = [{"k": "".join(rng.choice(alphabet) for _ in range(8))}, True, None, 0]
            assert canonicalize(doc) == awr.canonicalize(doc)

    def test_floats_are_refused(self):
        with pytest.raises(ValueError):
            canonicalize({"perCall": 0.5})

    def test_integers_beyond_ijson_are_refused(self):
        # 2^53 + 1 reads back as 2^53 in every double-based parser: one limit, two values.
        assert canonicalize({"n": 2**53 - 1}) == b'{"n":9007199254740991}'
        for bad in (2**53, -(2**53), 10**30):
            with pytest.raises(ValueError, match="I-JSON"):
                canonicalize({"n": bad})


class TestIssuing:
    def test_issue_verify_and_tamper(self):
        owner, agent = AgentKey.generate(), AgentKey.generate()
        doc = issue_mandate(owner, agent.did, audience=["https://modelmarket.dev/"], scope=["gaia.*"],
                            per_call_usd=0.02, per_day_usd=1.0, subcontract_allowance_usd=0.005)
        body = doc["credentialSubject"]["aimarketMandate"]
        assert body["limits"] == {"perCall": 20_000, "perDay": 1_000_000}
        assert body["audience"] == ["https://modelmarket.dev"]
        assert body["subcontract"] == {"perCallAllowance": 5_000, "maxDepth": 1}
        assert verify_document(doc)
        doc["credentialSubject"]["aimarketMandate"]["limits"]["perDay"] = 10**9
        assert not verify_document(doc)

    def test_a_redelegation_names_its_parent_by_digest(self):
        owner, agent, sub = AgentKey.generate(), AgentKey.generate(), AgentKey.generate()
        root = issue_mandate(owner, agent.did, audience=["https://h.test"], scope=["*"],
                             per_call_usd=0.01, per_day_usd=0.1)
        child = issue_mandate(agent, sub.did, audience=["https://h.test"], scope=["gaia.*"],
                              per_call_usd=0.01, per_day_usd=0.05, parent=root)
        assert child["credentialSubject"]["aimarketMandate"]["parent"] == mandate_digest(root)


class TestAgentClient:
    def test_a_mandated_invoke_signs_exactly_the_bytes_it_sends(self):
        owner, agent = AgentKey.generate(), AgentKey.generate()
        doc = issue_mandate(owner, agent.did, audience=["https://h.test"], scope=["*"],
                            per_call_usd=0.01, per_day_usd=0.1)
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            seen["body"] = request.content
            return httpx.Response(200, json={"success": True, "result": {}})

        client = AIMarketAgent("https://h.test", verify_receipts=False,
                               mandate=Mandate(document=doc, key=agent, hub_origin="https://h.test"))
        client.session = httpx.Client(transport=httpx.MockTransport(handler))
        client.invoke_single("p", "c@v1", {"x": 1}, subcontract={"allowance_usd": 0.002})
        h = seen["headers"]
        assert h["x-aimarket-mandate"] == mandate_digest(doc)
        assert "x-payment-channel" not in h and "x-api-key" not in h
        from aimarket_agent.mandates import request_message, _b58decode  # noqa: F401
        import base64, hashlib  # noqa: E401
        t, n, s = (part.split("=", 1)[1] for part in h["x-aimarket-mandate-proof"].split(";"))
        message = request_message(hub_origin="https://h.test", method="POST", path="/ai-market/v2/invoke",
                                  digest=mandate_digest(doc), t=int(t), nonce=n, body=seen["body"])
        from aimarket_agent.mandates import public_key_of
        public_key_of(agent.did).verify(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)), message)
        assert json.loads(seen["body"])["subcontract"] == {"allowance_usd": 0.002}

    def test_a_provider_forwards_the_job_and_pays_from_the_allowance(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.headers))
            return httpx.Response(200, json={"success": True, "result": {}})

        job = JobContext.from_headers({"x-aimarket-job": "tok.sig", "X-AIMarket-Job-Grant": "g"})
        assert job is not None and job.funded
        client = AIMarketAgent("https://h.test", verify_receipts=False, api_key="aimk_own")
        client.session = httpx.Client(transport=httpx.MockTransport(handler))
        client.invoke_single("p", "c@v1", {}, job=job)
        assert seen["x-aimarket-job"] == "tok.sig" and seen["x-aimarket-job-grant"] == "g"
        assert "x-api-key" not in seen   # a funded purchase sends no other payment

    def test_a_mandate_refusal_is_not_reported_as_a_safety_block(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"success": False, "error": "mandate_scope",
                                             "detail": "c@v1 is outside the leaf's scope"})

        client = AIMarketAgent("https://h.test", verify_receipts=False, api_key="aimk_own")
        client.session = httpx.Client(transport=httpx.MockTransport(handler))
        out = client.invoke_single("p", "c@v1", {})
        assert out["refused"] is True and out["error"] == "mandate_scope"
        assert "safety_blocked" not in out

    def test_a_safety_rejection_still_is_one(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"category": "injection", "reason": "prompt injection"})

        client = AIMarketAgent("https://h.test", verify_receipts=False, api_key="aimk_own")
        client.session = httpx.Client(transport=httpx.MockTransport(handler))
        assert client.invoke_single("p", "c@v1", {})["safety_blocked"] is True

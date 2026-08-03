"""The v2 canonical and the origin-URL normalisation — the two halves of 2.2.0 that shipped
with no test in this package at all.

Both were verified against the hub's real Signer during the pre-release audit and both were
correct, but a correct thing with no test has no net under the next refactor. The audit said so
in as many words, so these are its acceptance assertions, ported.

v2 exists because on a REJECTION receipt every v1 field is a constant — price 0, success 0,
latency 0 — so the v1 signature authenticated nothing about why the buyer's money came back.
The hub therefore signs a rejection over the evidence as well, and until this release the SDK
computed only v1 and answered `invalid-signature` for every one of them: exactly the receipts a
buyer most wants to check.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aimarket_agent.receipts import (  # noqa: E402
    RECEIPT_V2_FIELDS,
    OriginVerifiers,
    _fields_digest,
    _normalise_origin,
    canonical_string,
    signed_version,
    verify_receipt,
)


def _hub_signer(tmp_path, name="hub"):
    """The hub's REAL Signer. Nothing here compares a stub against itself."""
    repo = Path(__file__).resolve().parents[2]
    hub = repo / "aimarket-hub"
    if hub.is_dir() and str(hub) not in sys.path:
        sys.path.insert(0, str(hub))
    try:
        from aimarket_hub.signing import Signer
    except Exception:
        pytest.skip("aimarket-hub not importable — real signatures unavailable")
    return Signer(str(tmp_path / name))


INVOKE = {
    "nonce": "n1", "product_id": "prod-x", "capability_id": "x.y@v1", "price_usd": 0.004,
    "timestamp": "2026-07-30T10:00:00Z", "success": True, "latency_ms": 12,
}
REJECTION = {
    **INVOKE, "price_usd": 0.0, "success": False, "latency_ms": 0,
    "reason": "verification score below threshold", "verify_score": 0.31,
    "trace_id": "tr_abc", "refunded": True, "channel_id": "ch_1",
}


class TestTheVersionComesFromTheSignature:
    """Read from the signature block, never derived from the content.

    Deriving it would let a tamperer bolt a v2-only field onto a v1-signed receipt and have the
    verifier compute a canonical that no longer matches — reporting a VALID receipt as forged.
    """

    def test_absent_means_v1(self):
        assert signed_version({"signature": {"algorithm": "ed25519", "value": "x"}}) == 1

    def test_a_v2_block_says_two(self):
        assert signed_version({"signature": {"value": "x", "version": 2}}) == 2

    @pytest.mark.parametrize("bad", ["abc", None, "", [], {}, "2.5", 0, -1])
    def test_an_unreadable_version_is_zero_not_one(self, bad):
        """Zero is the fail-closed sentinel. Falling back to 1 would verify a v2 receipt
        against the wrong canonical and call it forged."""
        assert signed_version({"signature": {"value": "x", "version": bad}}) == 0

    def test_a_v2_only_field_on_a_v1_signed_receipt_does_not_change_the_canonical(self):
        v1 = {**INVOKE, "signature": {"value": "x"}}
        forged = {**v1, "reason": "attacker text"}
        assert canonical_string(forged) == canonical_string(v1), (
            "the version must come from the signature, not from which fields happen to exist"
        )


class TestByteParityWithTheHub:
    def test_v1_canonicals_are_identical(self, tmp_path):
        signer = _hub_signer(tmp_path)
        signed = {**INVOKE, "signature": signer.sign_receipt(INVOKE)}
        assert canonical_string(signed) == signer.receipt_canonical(signed, 1)

    def test_v2_canonicals_are_identical_including_the_digest(self, tmp_path):
        signer = _hub_signer(tmp_path)
        signed = {**REJECTION, "signature": signer.sign_receipt(REJECTION)}
        assert signed["signature"].get("version") == 2, "the hub must pick v2 for a rejection"
        assert canonical_string(signed) == signer.receipt_canonical(signed, 2)

    def test_the_field_list_matches_the_hub(self, tmp_path):
        _hub_signer(tmp_path)  # only for the import side effect / skip
        from aimarket_hub.signing import _RECEIPT_V2_FIELDS

        assert RECEIPT_V2_FIELDS == _RECEIPT_V2_FIELDS, (
            "a field in one list and not the other means one side signs what the other does not"
        )

    def test_a_missing_field_is_bound_as_null(self):
        """Dropping a field must change the digest, or removing `delivery_reasons` from a
        stored envelope would go unnoticed."""
        with_field = _fields_digest({"reason": "r", "refunded": True}, RECEIPT_V2_FIELDS)
        without = _fields_digest({"reason": "r"}, RECEIPT_V2_FIELDS)
        assert with_field != without


class TestRealSignatures:
    def test_a_v2_rejection_verifies(self, tmp_path):
        signer = _hub_signer(tmp_path)
        signed = {**REJECTION, "signature": signer.sign_receipt(REJECTION)}
        result = verify_receipt(signed, signer.public_key_b64)
        assert bool(result) is True, result.reason

    @pytest.mark.parametrize("field,value", [
        ("reason", "something else"), ("verify_score", 0.99), ("refunded", False),
        ("trace_id", "tr_other"), ("channel_id", "ch_other"), ("type", "other"),
    ])
    def test_tampering_with_the_evidence_breaks_it(self, tmp_path, field, value):
        """The whole point of v2: the evidence a rejection is argued from is signed."""
        signer = _hub_signer(tmp_path)
        signed = {**REJECTION, "signature": signer.sign_receipt(REJECTION)}
        assert not bool(verify_receipt({**signed, field: value}, signer.public_key_b64))

    def test_a_v1_receipt_still_verifies_and_carries_no_version_marker(self, tmp_path):
        signer = _hub_signer(tmp_path)
        signed = {**INVOKE, "signature": signer.sign_receipt(INVOKE)}
        assert "version" not in signed["signature"], "v1 must stay byte-stable"
        assert bool(verify_receipt(signed, signer.public_key_b64))

    @pytest.mark.parametrize("version,reason", [
        ("two", "unreadable-signature-version"),
        (0, "unreadable-signature-version"),
        (9, "unsupported-canonical-version:9"),
    ])
    def test_an_unusable_version_fails_closed_with_its_own_reason(self, tmp_path, version, reason):
        """Not "invalid-signature": blaming the receipt for the verifier's age is the mistake
        this module already made once, with the wrong key."""
        signer = _hub_signer(tmp_path)
        signed = {**REJECTION, "signature": signer.sign_receipt(REJECTION)}
        signed["signature"] = {**signed["signature"], "version": version}
        result = verify_receipt(signed, signer.public_key_b64)
        assert bool(result) is False and result.reason == reason


class TestOriginNormalisation:
    @pytest.mark.parametrize("origin,expected", [
        ("https://peer.test/family", "https://peer.test/family"),
        ("https://peer.test/family/", "https://peer.test/family"),
        ("http://h/_cluster/health#", "http://h/_cluster/health"),
        ("http://h/v1/secret?list=true&", "http://h/v1/secret"),
        ("http://h/latest/meta-data/iam#", "http://h/latest/meta-data/iam"),
    ])
    def test_the_fragment_and_query_are_dropped(self, origin, expected):
        """Appending a fixed suffix to a URL carrying either hands the path to whoever wrote it:
        a '#' sent the suffix to the fragment, which is never transmitted, so
        "http://HOST/_cluster/health#" produced a literal GET /_cluster/health."""
        assert _normalise_origin(origin) == expected

    def test_credentials_are_stripped(self):
        """httpx turns userinfo into an Authorization: Basic header, and the value was also kept
        as the cache key and handed out by keys(). A well-known document is public; there is
        nothing here to authenticate to."""
        assert _normalise_origin("http://alice:s3cr3t@peer.test/family") == (
            "http://peer.test/family"
        )
        assert "s3cr3t" not in _normalise_origin("http://alice:s3cr3t@peer.test/family")

    @pytest.mark.parametrize("origin", [
        "file:///etc/passwd", "FILE:///etc/passwd", "gopher://127.0.0.1:6379/_x",
        "ftp://h/y", "data:text/plain,x", "notaurl", "", "   ", "http://",
    ])
    def test_only_http_and_https_are_fetched(self, origin):
        assert _normalise_origin(origin) == ""

    def test_a_rejected_origin_is_not_logged_verbatim(self, caplog):
        with caplog.at_level("WARNING", logger="aimarket_agent.receipts"):
            _normalise_origin("gopher://alice:s3cr3t@evil.test/x")
        assert "s3cr3t" not in caplog.text
        assert "gopher" in caplog.text, "the scheme is the useful part"

    @pytest.mark.parametrize("origin", [
        "http://localhost:9083", "http://127.0.0.1:9083", "http://hub:9083",
        "http://172.17.0.4:9083", "http://192.168.1.10:9083",
    ])
    def test_it_is_not_an_address_filter(self, origin):
        """Refusing loopback and private ranges would refuse the project's own documented
        deployments and silently downgrade every receipt in a self-hosted stack to unchecked."""
        assert _normalise_origin(origin) == origin


class TestTheFailedLookupCacheExpires:
    def _resolver(self, state, calls):
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if state["fail"]:
                return httpx.Response(503)
            return httpx.Response(200, json={"signer_public_key": "AAAA"})

        return OriginVerifiers(
            "https://hub.test", httpx.Client(transport=httpx.MockTransport(handler))
        )

    def test_a_miss_is_cached_within_the_ttl(self):
        calls: list[str] = []
        resolver = self._resolver({"fail": True}, calls)
        for _ in range(4):
            assert resolver.verifier_for("https://peer.test/x") is None
        assert len(calls) == 2, f"two candidate URLs, tried once: {calls}"

    def test_the_miss_expires_so_a_blip_is_not_permanent(self):
        """One 503 used to leave every later receipt from that origin unverified for the life of
        the agent, with no further request ever made."""
        calls: list[str] = []
        state = {"fail": True}
        resolver = self._resolver(state, calls)
        resolver.verifier_for("https://peer.test/x")
        state["fail"] = False
        assert resolver.verifier_for("https://peer.test/x") is None, "still inside the TTL"
        resolver.MISS_TTL_S = 0.0
        found = resolver.verifier_for("https://peer.test/x")
        assert found is not None and found.public_key_b64 == "AAAA"

    def test_a_resolved_key_is_cached_and_clears_the_miss(self):
        calls: list[str] = []
        state = {"fail": True}
        resolver = self._resolver(state, calls)
        resolver.verifier_for("https://peer.test/x")
        state["fail"] = False
        resolver.MISS_TTL_S = 0.0
        resolver.verifier_for("https://peer.test/x")
        before = len(calls)
        resolver.verifier_for("https://peer.test/x")
        assert len(calls) == before, "a resolved key must not be re-fetched"

    def test_a_failed_origin_is_visible_in_keys_not_hidden(self):
        resolver = self._resolver({"fail": True}, [])
        resolver.verifier_for("https://peer.test/x")
        assert resolver.keys() == {"https://peer.test/x": ""}, (
            "an operator must be able to tell 'published nothing' from 'never asked'"
        )


class TestRedirectsAreRefusedExplicitly:
    def test_a_cross_origin_302_yields_no_key(self):
        hops: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hops.append(str(request.url))
            if "peer" in str(request.url):
                return httpx.Response(
                    302, headers={"Location": "http://169.254.169.254/latest/meta-data/iam"}
                )
            return httpx.Response(200, json={"signer_public_key": "LEAKED"})

        # follow_redirects=True on the CALLER's client — the property must not depend on it.
        resolver = OriginVerifiers(
            "https://hub.test",
            httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True),
        )
        assert resolver.verifier_for("https://peer.test/x") is None
        assert not any("169.254" in h for h in hops), hops


class TestUnsignedFieldsAreVisible:
    """A v1-signed receipt with injected v2 fields verifies True — correctly, and the caller
    needs a way to see that the injected values are not covered.

    No re-signing is needed to build one: the signature block still says v1, so the v1 canonical
    is what gets checked and it still matches. `verify_receipt` is right to say ok. But 2.2.0 is
    the release that advertises "v2 binds reason/verify_score/trace_id/refunded/channel_id", and
    a reader can reasonably over-trust that True.
    """

    def test_a_forged_v1_receipt_verifies_but_names_its_uncovered_fields(self, tmp_path):
        from aimarket_agent.receipts import unsigned_receipt_fields

        signer = _hub_signer(tmp_path)
        genuine = {**INVOKE, "signature": signer.sign_receipt(INVOKE)}
        forged = {**genuine, "reason": "attacker text", "refunded": True,
                  "verify_score": 0.99, "trace_id": "tr_forged"}

        assert bool(verify_receipt(forged, signer.public_key_b64)) is True, (
            "the v1 canonical still matches, so this is the correct answer"
        )
        uncovered = unsigned_receipt_fields(forged)
        assert set(uncovered) == {"reason", "refunded", "verify_score", "trace_id"}, uncovered

    def test_a_properly_signed_v2_receipt_has_nothing_uncovered(self, tmp_path):
        from aimarket_agent.receipts import unsigned_receipt_fields

        signer = _hub_signer(tmp_path)
        signed = {**REJECTION, "signature": signer.sign_receipt(REJECTION)}
        assert unsigned_receipt_fields(signed) == ()

    def test_a_plain_invoke_receipt_has_nothing_to_report(self, tmp_path):
        from aimarket_agent.receipts import unsigned_receipt_fields

        signer = _hub_signer(tmp_path)
        signed = {**INVOKE, "signature": signer.sign_receipt(INVOKE)}
        assert unsigned_receipt_fields(signed) == ()

    def test_it_agrees_with_the_hub(self, tmp_path):
        """Two implementations of the same question must not disagree."""
        signer = _hub_signer(tmp_path)
        from aimarket_hub.signing import unsigned_receipt_fields as hub_answer

        from aimarket_agent.receipts import unsigned_receipt_fields as sdk_answer

        for receipt in (
            {**INVOKE, "signature": signer.sign_receipt(INVOKE)},
            {**REJECTION, "signature": signer.sign_receipt(REJECTION)},
            {**INVOKE, "signature": signer.sign_receipt(INVOKE), "reason": "x", "refunded": True},
            {**INVOKE, "signature": {"value": "unreadable"}},
        ):
            assert sdk_answer(receipt) == hub_answer(receipt), receipt


def test_the_real_signature_tests_must_not_be_silently_skipped():
    """A guard that skips itself is not a guard.

    Every test above that proves anything cryptographic needs ``aimarket-hub`` importable for
    its Signer, and skips without it. Running this suite from the sdist — where aimarket-hub is
    not present — gave "26 passed, 17 skipped", and among the skipped were all twelve
    real-signature tests including the one that is the entire point of this release. A
    downstream packager or auditor sees green and has verified nothing.

    So this fails, loudly, and says how to fix it. It is the only test here that must never be
    skipped.
    """
    repo = Path(__file__).resolve().parents[2]
    hub = repo / "aimarket-hub"
    if hub.is_dir() and str(hub) not in sys.path:
        sys.path.insert(0, str(hub))
    try:
        from aimarket_hub.signing import Signer  # noqa: F401
    except Exception as exc:
        pytest.fail(
            "aimarket-hub is not importable, so every real-signature test in this suite "
            f"SKIPPED and this run proves nothing cryptographic ({type(exc).__name__}: {exc}). "
            "Run from a monorepo checkout, or `pip install ./aimarket-hub`, or set PYTHONPATH "
            "to it. This test exists because the sdist's suite reported 26 passed / 17 skipped "
            "and looked green."
        )

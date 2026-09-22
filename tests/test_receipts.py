"""Receipt verification tests — cross-checked against the hub's real Signer.

These prove the SDK verifier accepts genuine hub signatures, rejects tampering,
and degrades gracefully — closing the gap where receipts were trusted blindly.
The hub signs a pipe-delimited canonical with Ed25519 and exposes the public key
(base64) at /.well-known as `signer_public_key`.
"""

import sys
from pathlib import Path

import pytest

# Make the SDK importable from the monorepo checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aimarket_agent.receipts import ReceiptVerifier, VerifyResult, verify_receipt


def _signed(tmp_path, payload):
    """Build a self-contained receipt using the real hub Signer (or skip)."""
    repo = Path(__file__).resolve().parents[2]
    hub = repo / "aimarket-hub"
    if hub.is_dir() and str(hub) not in sys.path:
        sys.path.insert(0, str(hub))
    try:
        from aimarket_hub.signing import Signer
    except Exception:
        pytest.skip("aimarket-hub not importable in this environment")
    signer = Signer(str(tmp_path / "key"))
    receipt = {**payload, "signature": signer.sign_receipt(payload)}
    return receipt, signer.public_key_b64


def _payload(**over):
    base = {
        "nonce": "rcpt_abc", "product_id": "prod-translate",
        "capability_id": "translate.multi@v2", "price_usd": 0.4,
        "timestamp": "2026-05-30T00:00:00Z", "success": True, "latency_ms": 123,
    }
    base.update(over)
    return base


def test_verifies_genuine_hub_receipt(tmp_path):
    receipt, pub = _signed(tmp_path, _payload())
    res = verify_receipt(receipt, pub)
    assert res.verified is True
    assert bool(res) is True


def test_rejects_tampered_receipt(tmp_path):
    receipt, pub = _signed(tmp_path, _payload())
    receipt["price_usd"] = 0.0  # tamper a signed field
    res = verify_receipt(receipt, pub)
    assert res.verified is False
    assert res.reason == "invalid-signature"


def test_rejects_wrong_key(tmp_path):
    receipt, _ = _signed(tmp_path, _payload())
    _, other_pub = _signed(tmp_path / "other", _payload(nonce="n2"))
    assert verify_receipt(receipt, other_pub).verified is False


def test_verifier_from_well_known(tmp_path):
    receipt, pub = _signed(tmp_path, _payload(nonce="n3"))
    v = ReceiptVerifier.from_well_known({"signer_public_key": pub})
    assert v.available is True
    assert v.verify(receipt).verified is True


def test_from_well_known_supports_nested_signing_block(tmp_path):
    receipt, pub = _signed(tmp_path, _payload(nonce="n4"))
    v = ReceiptVerifier.from_well_known({"signing": {"algorithm": "ed25519", "public_key": pub}})
    assert v.verify(receipt).verified is True


def test_missing_signature_is_reported():
    assert verify_receipt({"nonce": "x"}, "AA==").reason == "no-signature"


def test_no_public_key_is_reported():
    assert verify_receipt({"nonce": "x", "signature": {"value": "ab"}}, "").reason == "no-public-key"


def test_unsupported_algorithm():
    v = ReceiptVerifier(public_key_b64="AA==", algorithm="rsa")
    assert v.verify({"signature": {"value": "ab"}}).reason.startswith("unsupported-algorithm")


def test_verify_result_truthiness():
    assert not VerifyResult(False, "x")
    assert VerifyResult(True, "ok")

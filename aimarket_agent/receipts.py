"""Receipt verification for AIMarket Protocol v2.

The hub signs every invoke receipt with Ed25519 and advertises its public key
(base64) at ``/.well-known/ai-market.json`` as ``signer_public_key``.

A self-contained receipt looks like::

    {
      "nonce": "...", "product_id": "...", "capability_id": "...",
      "price_usd": 0.4, "timestamp": "...Z", "success": true, "latency_ms": 123,
      "signature": {"algorithm": "ed25519", "value": "<base64>"}
    }

The signature is over a pipe-delimited canonical string (matching
``aimarket_hub.signing.Signer.sign_receipt``)::

    nonce:{}|product_id:{}|capability_id:{}|price_usd:{}|timestamp:{}|success:{0/1}|latency_ms:{}

This module lets a consumer cryptographically confirm a receipt really came from
the hub it paid — turning "trust the JSON" into "verify the signature".

``cryptography`` is an optional dependency: if it is missing, verification
degrades to ``VerifyResult(False, "cryptography-not-installed")`` rather than
raising, so the SDK still runs.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class VerifyResult:
    """Outcome of verifying a single receipt."""

    verified: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.verified


def canonical_string(receipt: dict[str, Any]) -> str:
    """Pipe-delimited canonical string the hub signs (Signer.sign_receipt parity)."""
    return (
        f"nonce:{receipt.get('nonce', '')}"
        f"|product_id:{receipt.get('product_id', '')}"
        f"|capability_id:{receipt.get('capability_id', '')}"
        f"|price_usd:{receipt.get('price_usd', 0)}"
        f"|timestamp:{receipt.get('timestamp', '')}"
        f"|success:{1 if receipt.get('success') else 0}"
        f"|latency_ms:{receipt.get('latency_ms', 0)}"
    )


def _signature_value(receipt: dict[str, Any]) -> str:
    """Extract the base64 signature from a self-contained receipt.

    Accepts the nested block ``{"signature": {"value": ...}}`` and, defensively,
    a flat ``{"signature": "<b64>"}`` or ``{"value": "<b64>"}``.
    """
    sig = receipt.get("signature")
    if isinstance(sig, dict):
        return sig.get("value", "")
    if isinstance(sig, str):
        return sig
    return receipt.get("value", "")


def verify_receipt(receipt: dict[str, Any], public_key_b64: str) -> VerifyResult:
    """Verify an Ed25519-signed receipt against the hub's base64 public key.

    Never raises on ordinary failure paths — returns a VerifyResult so callers
    can branch without wrapping every invoke in try/except.
    """
    if not isinstance(receipt, dict):
        return VerifyResult(False, "receipt-not-a-dict")
    sig_b64 = _signature_value(receipt)
    if not sig_b64:
        return VerifyResult(False, "no-signature")
    if not public_key_b64:
        return VerifyResult(False, "no-public-key")

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return VerifyResult(False, "cryptography-not-installed")

    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        pub.verify(base64.b64decode(sig_b64), canonical_string(receipt).encode())
        return VerifyResult(True, "ok")
    except InvalidSignature:
        return VerifyResult(False, "invalid-signature")
    except (ValueError, TypeError) as exc:
        return VerifyResult(False, f"malformed: {exc}")


class ReceiptVerifier:
    """Holds a hub public key and verifies receipts against it.

    Construct from a hub's well-known document::

        v = ReceiptVerifier.from_well_known(agent.well_known())
        assert v.verify(response["receipt"])
    """

    def __init__(self, public_key_b64: str = "", algorithm: str = "ed25519"):
        self.public_key_b64 = public_key_b64 or ""
        self.algorithm = algorithm or "ed25519"

    @classmethod
    def from_well_known(cls, well_known: dict[str, Any]) -> "ReceiptVerifier":
        wk = well_known or {}
        # The hub exposes the key as "signer_public_key"; accept a nested
        # "signing" block too for forward compatibility.
        pub = wk.get("signer_public_key", "")
        alg = "ed25519"
        signing = wk.get("signing")
        if isinstance(signing, dict):
            pub = pub or signing.get("public_key", "")
            alg = signing.get("algorithm", alg)
        return cls(public_key_b64=pub, algorithm=alg)

    @property
    def available(self) -> bool:
        """True if a public key is present (verification is possible)."""
        return bool(self.public_key_b64)

    def verify(self, receipt: Optional[dict[str, Any]]) -> VerifyResult:
        if receipt is None:
            return VerifyResult(False, "no-receipt")
        if self.algorithm != "ed25519":
            return VerifyResult(False, f"unsupported-algorithm:{self.algorithm}")
        return verify_receipt(receipt, self.public_key_b64)

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

Receipts issued under Pay-on-Verified settlement additionally carry an UNSIGNED
``verification`` envelope (status/verdict/verify_score/trace_id). It is not
part of the canonical above — the envelope gets its own hub signature — so its
presence never affects receipt verification.

This module lets a consumer cryptographically confirm a receipt really came from
the party that did the work — turning "trust the JSON" into "verify the signature".

**Whose key, though.** A hub is a broker. When it routes an invoke to a federated
provider, what comes back carries the PROVIDER's signature, not the hub's — that is
the point of the design, because it is what lets a buyer check the work without
trusting the middleman. So the key to verify against depends on where the capability
lives, and ``OriginVerifiers`` below resolves it per source.

Until 2.2.0 this module only ever held one key, the hub's, and every receipt was
checked against it. Measured against ``modelmarket.dev`` on 2026-07-29: the hub
publishes ``sVjlCo52…``, the federated oracle family publishes ``YkAOwWNb…``, and 42
of the 47 live capabilities are federated. So the SDK answered
``invalid-signature`` for 89% of the catalogue, on receipts that were perfectly
valid — a false alarm on exactly the guarantee the protocol is sold on, which is
worse than no check at all because it teaches the reader that the signal means
nothing. ``verify_receipt(receipt, key)`` itself was never wrong; the key was.

``cryptography`` is an optional dependency: if it is missing, verification
degrades to ``VerifyResult(False, "cryptography-not-installed")`` rather than
raising, so the SDK still runs.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit

import logging


logger = logging.getLogger(__name__)


@dataclass
class VerifyResult:
    """Outcome of verifying a single receipt."""

    verified: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.verified


#: Fields bound only by the v2 canonical, in the hub's order. Mirrors
#: ``aimarket_hub.signing._RECEIPT_V2_FIELDS`` — these are what a REJECTION receipt is
#: argued from, and on a rejection every v1 field is a constant (price 0, success 0,
#: latency 0), so v1 signed essentially nothing about why the buyer's money came back.
RECEIPT_V2_FIELDS = (
    "type", "channel_id", "category", "plugin", "reason", "verify_score",
    "delivery_reasons", "trace_id", "refunded",
)


def _fields_digest(receipt: dict[str, Any], names: tuple[str, ...]) -> str:
    """Digest over a named subset, byte-identical to the hub's ``_fields_digest``.

    Every argument matters for byte parity: ``sort_keys``, ``ensure_ascii=False``, the
    compact separators, and ``default=str``. A missing key is bound as ``null`` on purpose,
    so DROPPING a field changes the digest — otherwise removing ``delivery_reasons`` from a
    stored envelope would go unnoticed.
    """
    import hashlib
    import json

    payload = {name: receipt.get(name) for name in names}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":"), default=str).encode()
    ).hexdigest()


def signed_version(receipt: dict[str, Any]) -> int:
    """Canonical version the receipt's OWN signature block names. 1 when absent.

    Read from the signature rather than derived from the content, and that distinction is
    the whole point: the hub picks the version from what the receipt carries, but a verifier
    must check against what was actually SIGNED. Deriving it here instead would let a
    tamperer add a v2-only field to a v1-signed receipt and have the verifier compute a
    canonical that no longer matches — a valid receipt reported as forged.
    """
    sig = receipt.get("signature")
    if not isinstance(sig, dict):
        return 1
    raw = sig.get("version", 1)
    try:
        version = int(raw)
    except (TypeError, ValueError):
        return 0  # unreadable: fail closed rather than guessing v1
    return version if version >= 1 else 0


def unsigned_receipt_fields(receipt: dict[str, Any]) -> tuple[str, ...]:
    """v2-only fields this receipt carries that its OWN signature does not cover.

    Mirrors ``aimarket_hub.signing.unsigned_receipt_fields`` so a consumer can ask the same
    question the hub can. Empty means every evidence field present is covered.

    Why a caller needs this, and why ``verify_receipt`` cannot answer it. Take a genuine
    hub-signed v1 invoke receipt and bolt on ``reason``, ``refunded``, ``verify_score``,
    ``trace_id``: no re-signing is needed, because the signature block still says v1, and
    verification correctly reports ``ok`` — the v1 canonical is what was signed and it still
    matches. That is right, and it matches the hub. But the injected fields are unauthenticated
    text with nothing in the result to mark them as such, and 2.2.0 is the release that
    advertises "v2 binds reason/verify_score/trace_id/refunded/channel_id", so a reader may
    reasonably over-trust a ``verified=True``. A dispute has to be able to see which values
    were actually covered rather than guess.

    Non-empty is not by itself suspicious: a receipt signed before v2 existed, or by a peer
    still on v1, legitimately carries uncovered fields. It means "do not argue from these".
    """
    present = tuple(name for name in RECEIPT_V2_FIELDS if name in receipt)
    if not present:
        return ()
    return () if signed_version(receipt) >= 2 else present


def canonical_string(receipt: dict[str, Any], version: int | None = None) -> str:
    """Pipe-delimited canonical string the hub signs (Signer.receipt_canonical parity).

    ``version=None`` reads the version out of the receipt's signature block. Pass one only
    to pin it deliberately.

    v2 exists because a rejection receipt's v1 fields are all constants, so the signature
    authenticated nothing about the reason, the score, the trace or the refund. Until this
    function learned about it, the SDK computed the v1 string for every receipt and answered
    ``invalid-signature`` for every v2 one — i.e. for exactly the receipts a buyer most
    wants to verify, the ones explaining why their money came back. Measured against the
    hub's own Signer on 2026-07-29: hub True, SDK False, same receipt.
    """
    resolved = signed_version(receipt) if version is None else int(version)
    base = (
        f"nonce:{receipt.get('nonce', '')}"
        f"|product_id:{receipt.get('product_id', '')}"
        f"|capability_id:{receipt.get('capability_id', '')}"
        f"|price_usd:{receipt.get('price_usd', 0)}"
        f"|timestamp:{receipt.get('timestamp', '')}"
        f"|success:{1 if receipt.get('success') else 0}"
        f"|latency_ms:{receipt.get('latency_ms', 0)}"
    )
    if resolved < 2:
        return base
    return f"{base}|v:2|fields:{_fields_digest(receipt, RECEIPT_V2_FIELDS)}"


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
    version = signed_version(receipt)
    if version == 0:
        return VerifyResult(False, "unreadable-signature-version")
    if version > 2:
        # A newer canonical this build does not know. Reporting "invalid" would blame the
        # receipt for the verifier's age, which is the mistake this module has already made
        # once with the wrong key.
        return VerifyResult(False, f"unsupported-canonical-version:{version}")

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return VerifyResult(False, "cryptography-not-installed")

    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        pub.verify(base64.b64decode(sig_b64), canonical_string(receipt, version).encode())
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


def _normalise_origin(origin: str) -> str:
    """A ``source_hub`` reduced to scheme://host[:port][/path], or "" if unusable.

    The fragment and query are DROPPED, and that is the security of this function. Appending
    a fixed suffix to a URL that may carry either hands the path to whoever wrote the URL —
    measured on the unfixed code against a raw TCP listener:

        source_hub = http://HOST/_cluster/health#          -> GET /_cluster/health
        source_hub = http://HOST/v1/secret/data/prod?list=true&
                                                           -> GET /v1/secret/data/prod?list=true&/.well-known/…

    The first is exact path control: the ``#`` sends everything after it to the fragment,
    which is never transmitted, so the suffix vanishes. After normalisation both become a
    request under ``/.well-known/ai-market.json`` and nothing else.

    Only http and https are accepted. httpx already refuses file:// and gopher:// (verified:
    zero connections), but relying on a transport's refusal for a security property means the
    property moves when the transport does.

    Deliberately NOT an address filter. Blocking loopback and private ranges would refuse the
    project's own documented deployments — docs/running.md health-checks the hub at
    http://localhost:9083/.well-known/ai-market.json, and core services reach each other as
    http://hub:9083 on a docker bridge inside 172.16/12 — which would silently downgrade every
    receipt in every self-hosted stack from verified to "unchecked". That is the false-signal
    failure this module exists to remove. `source_hub` is also hub-authored: the crawler
    overwrites whatever a peer claims with the URL it actually crawled, and screens that URL
    against its own SSRF guard before indexing.
    """
    raw = (origin or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https"):
        # The scheme, not the whole value: a rejected origin can carry credentials, and this
        # is the one place they would reach a log file.
        logger.warning(
            "ignoring a source_hub with scheme %r: only http and https are fetched for a "
            "signing key", parsed.scheme or "(none)",
        )
        return ""
    if not parsed.netloc:
        return ""
    # Credentials are STRIPPED. A source_hub of "http://alice:s3cr3t@peer/family" was fetched
    # verbatim — httpx turns userinfo into an Authorization: Basic header — then kept as the
    # cache key and handed out by the public keys() diagnostic, and a rejected non-http origin
    # was logged verbatim. A well-known document is public by definition, so there is nothing
    # to authenticate to; the only thing userinfo can do here is travel somewhere it should not.
    host = parsed.netloc.rpartition("@")[2] or parsed.netloc
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{host}{path}"


class OriginVerifiers:
    """Resolves the right signing key per capability origin, and caches it.

    A receipt from a capability the hub serves itself is signed by the hub. A receipt
    from a federated capability is signed by whoever ran it. One registry, keyed by the
    ``source_hub`` the catalogue reported, keeps both cases correct without the caller
    having to know which is which.

    ``session`` is the caller's HTTP client, so the agent's proxies and connection pool
    apply here too rather than this opening its own. It may be passed as a **callable**
    returning the client, which is how :class:`AIMarketAgent` hands it over: the agent
    exposes ``.session`` as a public, replaceable attribute (a caller swapping in an
    instrumented or mock client is the only injection point there is), and capturing the
    object at construction time meant a swap left this holding the ORIGINAL client. The
    replacement was used for every invoke while key lookups still went through the old
    one — so a caller who replaced the session got ``receipt_verified: False`` with
    ``no-origin-key`` on every call. It fails closed, so nothing was wrongly trusted; it
    is the same false alarm this release exists to remove, arriving by another route.
    """

    #: Reason used when no key could be found for an origin. Deliberately NOT
    #: "invalid-signature": "we could not look" and "the signature is wrong" call for
    #: opposite reactions, and conflating them is how the old false alarm hid.
    NO_KEY = "no-origin-key"

    #: How long a FAILED lookup is remembered. A miss is cached so an origin that publishes no
    #: key does not cost two HTTP round trips per receipt — but caching a FAILURE forever meant
    #: one 503 at the wrong moment left every later receipt from that origin reporting
    #: `no-origin-key`, i.e. unverified, for the life of the agent. Measured: serve one 503, let
    #: the origin recover, and the second lookup still answered None having made no further
    #: request. It fails closed, so it is not a security defect — it is a milder form of the
    #: false alarm this release exists to remove.
    #: Raised from 60 s to 300 s, because 60 was shorter than the worst case it was meant to
    #: bound. With no per-call timeout a key lookup inherited the agent's 120 s client default
    #: and tried two URLs, so a blocked origin could stall 240 s — longer than the TTL, so the
    #: next receipt from that origin paid the whole stall again inside the sequential run loop.
    #: A miss TTL must outlive the failure it is caching.
    MISS_TTL_S = 300.0

    #: Per-lookup timeout, independent of the agent's request timeout. Fetching a well-known
    #: document is a fast side errand on the way to verifying a receipt; the 120 s that is
    #: reasonable for an invoke that runs a VDF is not reasonable here, and two URLs are tried.
    KEY_FETCH_TIMEOUT_S = 5.0

    def __init__(self, hub_url: str, session: Any, *, algorithm: str = "ed25519"):
        self.hub_url = (hub_url or "").rstrip("/")
        self._session_ref = session
        self._algorithm = algorithm
        self._by_origin: dict[str, "ReceiptVerifier"] = {}
        self._misses: dict[str, float] = {}

    @property
    def _session(self) -> Any:
        """The client to use right now — resolved per call, never captured.

        Accepts either a client or a zero-argument callable returning one, so a caller who
        replaces the agent's ``.session`` is honoured on the next lookup instead of silently
        losing verification for the rest of the run.
        """
        ref = self._session_ref
        return ref() if callable(ref) and not hasattr(ref, "get") else ref

    # ── key resolution ───────────────────────────────────────────────────────

    def _well_known_urls(self, origin: str) -> list[str]:
        """Where an origin's well-known document might be.

        A federated ``source_hub`` may carry a path — the oracle family publishes at
        ``…/family``, and its document sits at ``…/family/.well-known/ai-market.json``,
        not at the domain root. Path-scoped first because it is the specific answer;
        the root is the fallback.
        """
        base = _normalise_origin(origin)
        if not base:
            return []
        urls = [f"{base}/.well-known/ai-market.json"]
        if "//" in base:
            scheme, _, rest = base.partition("//")
            root = f"{scheme}//{rest.split('/', 1)[0]}"
            if root != base:
                urls.append(f"{root}/.well-known/ai-market.json")
        return urls

    def verifier_for(self, source_hub: str = "") -> Optional["ReceiptVerifier"]:
        """Verifier for an origin, or None when it publishes no usable key."""
        origin = (source_hub or "").strip()
        if not origin or origin == "local":
            origin = self.hub_url
        if origin in self._by_origin:
            return self._by_origin[origin]
        failed_at = self._misses.get(origin)
        if failed_at is not None and time.monotonic() - failed_at < self.MISS_TTL_S:
            return None

        found: Optional[ReceiptVerifier] = None
        for url in self._well_known_urls(origin):
            try:
                # follow_redirects=False EXPLICITLY. It is httpx's default, so the shipped
                # behaviour was already right — but this module's own docstring argues that
                # resting a security property on a transport's behaviour means the property
                # moves when the transport does, and a caller may hand in a session built with
                # follow_redirects=True. A well-known document has no reason to redirect, and
                # following one lets a public origin 302-pivot anywhere.
                response = self._session.get(
                    url, follow_redirects=False, timeout=self.KEY_FETCH_TIMEOUT_S
                )
                response.raise_for_status()
                document = response.json()
            except Exception:
                continue
            candidate = ReceiptVerifier.from_well_known(document)
            if candidate.available:
                found = candidate
                break

        if found is not None:
            self._by_origin[origin] = found
            self._misses.pop(origin, None)
        else:
            self._misses[origin] = time.monotonic()
        return found

    # ── verification ─────────────────────────────────────────────────────────

    def verify(
        self, receipt: Optional[dict[str, Any]], *, source_hub: str = ""
    ) -> VerifyResult:
        """Verify a receipt against the key of the capability's origin."""
        if receipt is None:
            return VerifyResult(False, "no-receipt")
        verifier = self.verifier_for(source_hub)
        if verifier is None:
            origin = (source_hub or "").strip() or "local"
            return VerifyResult(False, f"{self.NO_KEY}:{origin}")
        return verifier.verify(receipt)

    def keys(self) -> dict[str, str]:
        """Origins seen so far and the key each published — for diagnostics.

        Origins whose lookup failed appear with an empty key rather than being hidden, so an
        operator can tell "published nothing" from "never asked".
        """
        resolved = {origin: v.public_key_b64 for origin, v in self._by_origin.items()}
        for origin in self._misses:
            resolved.setdefault(origin, "")
        return resolved

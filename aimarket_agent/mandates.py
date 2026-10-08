"""Mandates and subcontracting from the agent's side (aimarket-protocol/mandates.md).

An owner issues a mandate to an agent key; the agent presents it on every invoke with a
proof that it holds that key; the hub enforces the limits. This module is everything the two
sides need, with no dependency beyond ``cryptography``:

* :class:`AgentKey` — an Ed25519 key and its ``did:key``;
* :func:`issue_mandate` — build and sign a mandate (W3C VC 2.0, ``eddsa-jcs-2022``);
* :func:`mandate_digest` — the digest the hub names it by;
* :func:`request_proof` / :func:`owner_link_payload` / :func:`revoke_payload` — the signed
  messages of §4, §5.1 and §5.5;
* :class:`JobContext` — for a PROVIDER: the job token and grant the hub handed it, to send
  back on anything it buys while serving the call (§6).

Canonicalization. Mandates hold only objects, arrays, strings, integers and booleans, so
this module carries a canonicalizer for exactly that subset and refuses anything else. For
that subset it is byte-identical to RFC 8785; the monorepo's conformance test checks it
against the normative vectors (``aimarket-protocol/test-vectors/mandate-signed.json``)
signed by the AWR reference implementation.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

VC_CONTEXT = "https://www.w3.org/ns/credentials/v2"
MANDATE_HEADER = "X-AIMarket-Mandate"
PROOF_HEADER = "X-AIMarket-Mandate-Proof"
JOB_HEADER = "X-AIMarket-Job"
GRANT_HEADER = "X-AIMarket-Job-Grant"
HUB_HEADER = "X-AIMarket-Hub"
MICRO_PER_USD = 1_000_000

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_ED25519_PUB = b"\xed\x01"


# ── encodings ─────────────────────────────────────────────────────────────

def _b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def _b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        n = n * 58 + _B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(text) - len(text.lstrip("1"))) + body


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _escape(text: str) -> str:
    out = ['"']
    for ch in text:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            raise ValueError("lone surrogate in a mandate string")
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif code < 0x20:
            out.append("\\u%04x" % code)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


MAX_SAFE_INTEGER = 2**53 - 1


def canonicalize(value: Any) -> bytes:
    """RFC 8785 for the integer-only JSON subset mandates use. Floats are refused."""
    return _canon(value).encode("utf-8")


def _canon(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        # I-JSON (RFC 7493) numbers: outside ±(2^53 - 1) another implementation parses a
        # different value, and a limit that means one thing here and another at the hub
        # is worse than a refusal.
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError(f"{value} is outside the I-JSON integer range ±(2^53 - 1)")
        return str(value)
    if isinstance(value, float):
        raise ValueError("mandates carry integers only (µUSD); got a float")
    if isinstance(value, str):
        return _escape(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canon(v) for v in value) + "]"
    if isinstance(value, Mapping):
        keys = sorted(value.keys(), key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(_escape(k) + ":" + _canon(value[k]) for k in keys) + "}"
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def mandate_digest(document: Mapping[str, Any]) -> str:
    """``sha256-<base64>`` over the canonical form of the SECURED document (§3.3)."""
    return "sha256-" + base64.b64encode(_sha256(canonicalize(document))).decode("ascii")


# ── keys ──────────────────────────────────────────────────────────────────

class AgentKey:
    """An Ed25519 signing key and its ``did:key``. Keep the seed secret."""

    def __init__(self, private_key: Ed25519PrivateKey):
        self._key = private_key
        raw = private_key.public_key().public_bytes_raw()
        self.did = "did:key:z" + _b58encode(_ED25519_PUB + raw)
        self.verification_method = f"{self.did}#{self.did[len('did:key:'):]}"

    @classmethod
    def generate(cls) -> "AgentKey":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_seed(cls, seed: bytes) -> "AgentKey":
        return cls(Ed25519PrivateKey.from_private_bytes(seed))

    @classmethod
    def from_seed_hex(cls, seed_hex: str) -> "AgentKey":
        return cls.from_seed(bytes.fromhex(seed_hex.strip()))

    def seed_hex(self) -> str:
        from cryptography.hazmat.primitives import serialization

        return self._key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption(),
        ).hex()

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message)


def public_key_of(did: str) -> Ed25519PublicKey:
    if not did.startswith("did:key:z"):
        raise ValueError("not a did:key")
    raw = _b58decode(did[len("did:key:z"):])
    if raw[:2] != _ED25519_PUB or len(raw) != 34:
        raise ValueError("not an Ed25519 did:key")
    return Ed25519PublicKey.from_public_bytes(raw[2:])


# ── the mandate ───────────────────────────────────────────────────────────

def usd(amount: float) -> int:
    """USD → the integer µUSD a mandate carries."""
    return int(round(float(amount) * MICRO_PER_USD))


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue_mandate(
    issuer: AgentKey,
    subject_did: str,
    *,
    audience: Iterable[str],
    scope: Iterable[str],
    per_call_usd: float,
    per_day_usd: float,
    total_usd: float | None = None,
    per_product_per_day_usd: float | None = None,
    subcontract_allowance_usd: float | None = None,
    subcontract_max_depth: int = 1,
    parent: Mapping[str, Any] | str | None = None,
    valid_days: int = 30,
    valid_from: datetime | None = None,
    mandate_id: str | None = None,
) -> dict[str, Any]:
    """Build and sign a mandate (§3). Amounts are USD here and µUSD in the document.

    ``parent`` is the parent mandate (or its digest) for a re-delegation; the hub then
    requires this mandate to be narrower in every respect (§3.4).
    """
    start = (valid_from or datetime.now(timezone.utc)).replace(microsecond=0)
    limits: dict[str, int] = {"perCall": usd(per_call_usd), "perDay": usd(per_day_usd)}
    if total_usd is not None:
        limits["total"] = usd(total_usd)
    if per_product_per_day_usd is not None:
        limits["perProductPerDay"] = usd(per_product_per_day_usd)
    body: dict[str, Any] = {
        "version": 1,
        "audience": [a.rstrip("/") for a in audience],
        "scope": list(scope),
        "limits": limits,
    }
    if subcontract_allowance_usd is not None:
        body["subcontract"] = {"perCallAllowance": usd(subcontract_allowance_usd),
                               "maxDepth": int(subcontract_max_depth)}
    if parent is not None:
        body["parent"] = parent if isinstance(parent, str) else mandate_digest(parent)
    document: dict[str, Any] = {
        "@context": [VC_CONTEXT],
        "type": ["VerifiableCredential", "AIMarketMandate"],
        "id": mandate_id or f"urn:uuid:{uuid.uuid4()}",
        "issuer": issuer.did,
        "validFrom": _ts(start),
        "validUntil": _ts(start + timedelta(days=valid_days)),
        "credentialSubject": {"id": subject_did, "aimarketMandate": body},
    }
    return sign_document(document, issuer, created=_ts(datetime.now(timezone.utc)))


def sign_document(document: Mapping[str, Any], key: AgentKey, *, created: str) -> dict[str, Any]:
    """Attach an ``eddsa-jcs-2022`` DataIntegrityProof, as the AWR/2 reference does."""
    unsecured = {k: v for k, v in document.items() if k != "proof"}
    options: dict[str, Any] = {
        "type": "DataIntegrityProof",
        "cryptosuite": "eddsa-jcs-2022",
        "created": created,
        "verificationMethod": key.verification_method,
        "proofPurpose": "assertionMethod",
    }
    config = dict(options)
    if "@context" in unsecured:
        config["@context"] = unsecured["@context"]
    signature = key.sign(_sha256(canonicalize(config)) + _sha256(canonicalize(unsecured)))
    proof: dict[str, Any] = {}
    if "@context" in unsecured:
        proof["@context"] = unsecured["@context"]
    proof.update(options)
    proof["proofValue"] = "z" + _b58encode(signature)
    return {**unsecured, "proof": proof}


def verify_document(document: Mapping[str, Any]) -> bool:
    """Check a mandate's proof against its issuer (structure is the hub's job)."""
    proof = document.get("proof")
    if not isinstance(proof, Mapping) or not str(proof.get("proofValue", "")).startswith("z"):
        return False
    unsecured = {k: v for k, v in document.items() if k != "proof"}
    config = {k: v for k, v in proof.items() if k != "proofValue"}
    if "@context" in unsecured:
        config["@context"] = unsecured["@context"]
    try:
        public_key_of(str(document["issuer"])).verify(
            _b58decode(str(proof["proofValue"])[1:]),
            _sha256(canonicalize(config)) + _sha256(canonicalize(unsecured)),
        )
    except Exception:
        return False
    return True


# ── signed messages ───────────────────────────────────────────────────────

def _nonce() -> str:
    return secrets.token_urlsafe(18)


def request_message(*, hub_origin: str, method: str, path: str, digest: str, t: int, nonce: str,
                    body: bytes) -> bytes:
    return "\n".join([
        "aimarket-mandate-request/1", hub_origin.rstrip("/"), f"{method.upper()} {path}", digest,
        str(int(t)), nonce, hashlib.sha256(body or b"").hexdigest(),
    ]).encode("utf-8")


def request_proof(agent: AgentKey, *, hub_origin: str, leaf_digest: str, body: bytes,
                  method: str = "POST", path: str = "/ai-market/v2/invoke",
                  t: int | None = None, nonce: str | None = None) -> str:
    """The ``X-AIMarket-Mandate-Proof`` header value (§5.1)."""
    t = int(time.time()) if t is None else int(t)
    nonce = nonce or _nonce()
    message = request_message(hub_origin=hub_origin, method=method, path=path, digest=leaf_digest,
                              t=t, nonce=nonce, body=body)
    return f"t={t};n={nonce};s={b64url(agent.sign(message))}"


def owner_change_authorization(by: AgentKey, *, hub_origin: str, account_id: str, action: str,
                               did: str, require_mandate: bool = False, t: int | None = None,
                               nonce: str | None = None) -> dict[str, Any]:
    """An existing owner's signature over a change of owners (§4). ``action`` is ``link``
    (another DID), ``unlink`` or ``policy`` (changing require_mandate of a linked DID).
    ``t`` and ``nonce`` default to now and a fresh random value; pass them only to
    reproduce a fixed vector."""
    if action not in ("link", "unlink", "policy"):
        raise ValueError("action must be link, unlink or policy")
    t = int(time.time()) if t is None else int(t)
    nonce = nonce or _nonce()
    message = "\n".join(["aimarket-owner-change/1", hub_origin.rstrip("/"), account_id, action, did,
                         "1" if require_mandate else "0", str(t), nonce]).encode("utf-8")
    return {"by": by.did, "t": t, "n": nonce, "s": b64url(by.sign(message))}


def owner_link_payload(owner: AgentKey, *, hub_origin: str, account_id: str,
                       require_mandate: bool = False, authorized_by: AgentKey | None = None,
                       action: str = "link") -> dict[str, Any]:
    """Body of ``POST /ai-market/v2/mandates/owners`` (§4); send it with the account's X-API-Key.

    The first owner of an account needs only this. Once the account has an owner, pass
    ``authorized_by`` (a key that already is one); ``action="policy"`` changes
    ``require_mandate`` of an already-linked DID.
    """
    t, nonce = int(time.time()), _nonce()
    message = "\n".join(["aimarket-owner-link/1", hub_origin.rstrip("/"), account_id, owner.did,
                         str(t), nonce]).encode("utf-8")
    payload: dict[str, Any] = {"did": owner.did, "require_mandate": require_mandate,
                               "proof": {"t": t, "n": nonce, "s": b64url(owner.sign(message))}}
    if authorized_by is not None:
        payload["authorization"] = owner_change_authorization(
            authorized_by, hub_origin=hub_origin, account_id=account_id, action=action,
            did=owner.did, require_mandate=require_mandate,
        )
    return payload


def owner_unlink_payload(did: str, *, hub_origin: str, account_id: str, authorized_by: AgentKey) -> dict[str, Any]:
    """Body of ``POST /ai-market/v2/mandates/owners/unlink``; an owner may authorize its own removal."""
    return {"did": did, "authorization": owner_change_authorization(
        authorized_by, hub_origin=hub_origin, account_id=account_id, action="unlink", did=did,
    )}


def revoke_payload(by: AgentKey, *, hub_origin: str, digest: str, t: int | None = None,
                   nonce: str | None = None) -> dict[str, Any]:
    """Body of ``POST /ai-market/v2/mandates/revoke`` signed by an issuer in the chain (§5.5)."""
    t = int(time.time()) if t is None else int(t)
    nonce = nonce or _nonce()
    message = "\n".join(["aimarket-mandate-revoke/1", hub_origin.rstrip("/"), digest, str(t), nonce]).encode()
    return {"digest": digest, "by": by.did, "t": t, "n": nonce, "s": b64url(by.sign(message))}


@dataclass
class Mandate:
    """A mandate an agent holds: the document, its key, and the hub it spends at."""

    document: dict[str, Any]
    key: AgentKey
    hub_origin: str

    @property
    def digest(self) -> str:
        return mandate_digest(self.document)

    def headers(self, body: bytes, *, method: str = "POST", path: str = "/ai-market/v2/invoke") -> dict[str, str]:
        return {
            MANDATE_HEADER: self.digest,
            PROOF_HEADER: request_proof(self.key, hub_origin=self.hub_origin, leaf_digest=self.digest,
                                        body=body, method=method, path=path),
        }


# ── the provider side of a job ────────────────────────────────────────────

@dataclass(frozen=True)
class JobContext:
    """What the hub told a provider about the job its call belongs to (§6.1).

    A provider that buys something while serving the call sends :meth:`headers` with the
    purchase. With a grant present (the buyer set aside an allowance), the purchase is paid
    from that allowance; without one it is paid by whatever the provider sends itself, and
    the headers only place it in the job tree.
    """

    token: str
    grant: str = ""
    hub: str = ""

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "JobContext | None":
        lower = {k.lower(): v for k, v in headers.items()}
        token = (lower.get(JOB_HEADER.lower()) or "").strip()
        if not token:
            return None
        return cls(token=token, grant=(lower.get(GRANT_HEADER.lower()) or "").strip(),
                   hub=(lower.get(HUB_HEADER.lower()) or "").strip())

    @property
    def funded(self) -> bool:
        return bool(self.grant)

    def headers(self, *, use_allowance: bool = True) -> dict[str, str]:
        out = {JOB_HEADER: self.token}
        if use_allowance and self.grant:
            out[GRANT_HEADER] = self.grant
        return out

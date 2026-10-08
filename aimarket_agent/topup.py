"""Pay a hub's USDC top-up offer (hub docs/credits-topup.md).

``AIMarketAgent.topup_quote(5)`` returns the hub's 402: pay 5 USDC to ``payTo`` with an EIP-3009
``transferWithAuthorization`` signed over the offer's ``nonce``. This module turns that offer into

  * the EIP-712 typed data to hand to ``eth_signTypedData_v4`` (any wallet), and
  * the calldata to send to the token contract once you have the signature.

It never touches a private key. Sign and send with whatever already holds yours — a browser
wallet, a hardware device, ``eth_account`` in your own process, ``cast`` — and pay the gas
yourself: the hub holds no key and submits nothing. Then ``AIMarketAgent.topup_redeem(nonce,
tx_hash)`` and the credit lands on the account that asked for the offer.

Only an authorization over the offer's nonce is credited. A plain transfer to the same address
carries nothing that says which account it was for, so the hub cannot credit it.
"""
from __future__ import annotations

from typing import Any

#: keccak256("transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,uint8,bytes32,bytes32)")[:4]
TRANSFER_WITH_AUTHORIZATION_SELECTOR = "0xe3ee160e"

EIP712_TYPES: dict[str, list[dict[str, str]]] = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ],
}


class OfferError(ValueError):
    """The 402 cannot be turned into a payment as it stands."""


def _hex(value: Any, digits: int) -> bool:
    text = str(value or "")
    return (len(text) == digits + 2 and text[:2] in ("0x", "0X")
            and all(c in "0123456789abcdefABCDEF" for c in text[2:]))


def _whole(value: Any) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def typed_data(offer: dict[str, Any], *, sender: str, valid_before: int, valid_after: int = 0) -> dict[str, Any]:
    """EIP-712 payload for ``eth_signTypedData_v4`` from a top-up 402 body.

    The domain is read from the offer (``accepts[0].extra``), never guessed from the symbol: a
    wrong name or version yields a signature the token rejects, which looks like the hub
    refusing a good payment. ``valid_before`` is a unix time; keep it close — the authorization
    is spendable by whoever holds it until then.
    """
    if not _hex(sender, 40):
        raise OfferError("sender is not an address (0x followed by 40 hex digits)")
    for name, moment in (("valid_after", valid_after), ("valid_before", valid_before)):
        if isinstance(moment, bool) or not isinstance(moment, int) or moment < 0:
            raise OfferError(f"{name} is not a unix time (whole seconds, not negative)")
    accepts = offer.get("accepts") if isinstance(offer, dict) else None
    if not accepts or not isinstance(accepts, list) or not isinstance(accepts[0], dict):
        raise OfferError("the 402 carries no 'accepts' entry")
    accept = accepts[0]
    extra = accept.get("extra") if isinstance(accept.get("extra"), dict) else {}
    nonce = offer.get("nonce") or extra.get("nonce")
    if not _hex(nonce, 64):
        raise OfferError("the 402 carries no 32-byte payment nonce")
    for field in ("name", "version", "chainId", "verifyingContract"):
        if not extra.get(field):
            raise OfferError(f"the 402's 'extra' is missing the EIP-712 domain field {field!r}")
    chain_id = _whole(extra["chainId"])
    if chain_id is None or not _hex(extra["verifyingContract"], 40) or not _hex(accept.get("payTo"), 40):
        raise OfferError("the 402's chainId, verifyingContract or payTo is malformed")
    amount = _whole(accept.get("maxAmountRequired") or accept.get("amount"))
    if amount is None:
        raise OfferError("the 402 names no amount to pay (a positive whole number of base units)")
    return {
        "types": EIP712_TYPES,
        "primaryType": "TransferWithAuthorization",
        "domain": {"name": extra["name"], "version": str(extra["version"]), "chainId": chain_id,
                   "verifyingContract": extra["verifyingContract"]},
        "message": {"from": sender, "to": accept["payTo"], "value": str(amount),
                    "validAfter": str(valid_after), "validBefore": str(valid_before), "nonce": nonce},
    }


def split_signature(signature: str) -> tuple[int, str, str]:
    """65-byte signature → (v, r, s). Accepts v as 0/1 or 27/28 and refuses anything else:
    r and s go into the calldata as written, and a bad v makes a transaction that reverts."""
    raw = signature[2:] if signature.startswith(("0x", "0X")) else signature
    if len(raw) != 130 or not all(c in "0123456789abcdefABCDEF" for c in raw):
        raise OfferError("signature must be 65 bytes of hex (132 characters with 0x)")
    v = int(raw[128:130], 16)
    v = v + 27 if v in (0, 1) else v
    if v not in (27, 28):
        raise OfferError(f"signature v is {v}: a recovery id is 27 or 28 (or 0 or 1)")
    return v, "0x" + raw[0:64], "0x" + raw[64:128]


def _word(value: int | str) -> str:
    if isinstance(value, str):
        return (value[2:] if value.startswith("0x") else value).rjust(64, "0").lower()
    return format(value, "064x")


def calldata(typed: dict[str, Any], signature: str) -> str:
    """ABI-encoded ``transferWithAuthorization`` for the token contract: the selector and nine
    fixed-size words, no dynamic offsets."""
    message = typed["message"]
    v, r, s = split_signature(signature)
    words = [message["from"], message["to"], int(message["value"]), int(message["validAfter"]),
             int(message["validBefore"]), message["nonce"], v, r, s]
    return TRANSFER_WITH_AUTHORIZATION_SELECTOR + "".join(_word(w) for w in words)

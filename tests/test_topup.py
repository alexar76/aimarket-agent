"""aimarket_agent.topup: a hub's USDC top-up 402 → typed data to sign → calldata to send."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from aimarket_agent.topup import OfferError, calldata, split_signature, typed_data

TOKEN = "0x" + "11" * 20
PAY_TO = "0x" + "22" * 20
SENDER = "0x" + "33" * 20
NONCE = "0x" + "ab" * 32


def offer(**extra_overrides) -> dict:
    extra = {"name": "USD Coin", "version": "2", "chainId": 8453, "verifyingContract": TOKEN, "nonce": NONCE,
             **extra_overrides}
    return {"nonce": NONCE, "accepts": [{"scheme": "exact", "network": "base", "maxAmountRequired": "5000000",
                                         "asset": TOKEN, "payTo": PAY_TO, "extra": extra}]}


def test_the_typed_data_is_what_the_offer_says():
    typed = typed_data(offer(), sender=SENDER, valid_before=1_900_000_000)
    assert typed["primaryType"] == "TransferWithAuthorization"
    assert typed["domain"] == {"name": "USD Coin", "version": "2", "chainId": 8453, "verifyingContract": TOKEN}
    assert typed["message"] == {"from": SENDER, "to": PAY_TO, "value": "5000000", "validAfter": "0",
                                "validBefore": "1900000000", "nonce": NONCE}


@pytest.mark.parametrize("broken, why", [
    (lambda o: o["accepts"][0]["extra"].pop("version"), "version"),
    (lambda o: o["accepts"][0].update(payTo="0xnot"), "payTo"),
    (lambda o: o["accepts"][0].update(maxAmountRequired="-1"), "amount"),
    (lambda o: (o.pop("nonce"), o["accepts"][0]["extra"].pop("nonce")), "nonce"),
    (lambda o: o.update(accepts=[]), "accepts"),
])
def test_an_offer_it_cannot_honour_is_refused_before_anything_is_signed(broken, why):
    o = offer()
    broken(o)
    with pytest.raises(OfferError, match=why):
        typed_data(o, sender=SENDER, valid_before=1_900_000_000)


def test_a_template_sender_is_refused():
    with pytest.raises(OfferError, match="sender"):
        typed_data(offer(), sender="0xYOURADDRESS", valid_before=1)


def test_signature_forms():
    r, s = "12" * 32, "34" * 32
    assert split_signature("0x" + r + s + "1b") == (27, "0x" + r, "0x" + s)
    assert split_signature(r + s + "01") == (28, "0x" + r, "0x" + s)
    with pytest.raises(OfferError):
        split_signature("0x" + r + s + "05")
    with pytest.raises(OfferError):
        split_signature("0x1234")


@pytest.mark.skipif(not shutil.which("cast"), reason="foundry's cast not installed")
def test_the_calldata_is_what_cast_encodes():
    typed = typed_data(offer(), sender=SENDER, valid_before=1_900_000_000)
    r, s = "0x" + "12" * 32, "0x" + "34" * 32
    ours = calldata(typed, r + s[2:] + "1c")
    theirs = subprocess.run(
        ["cast", "calldata",
         "transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,uint8,bytes32,bytes32)",
         SENDER, PAY_TO, "5000000", "0", "1900000000", NONCE, "28", r, s],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert ours == theirs

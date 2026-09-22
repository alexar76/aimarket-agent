"""AIMarket Agent v2.2.0 — Reference consumer for AIMarket Protocol.

MIT Licensed. Lightweight pip-installable agent that any AI (Claude, GPT,
Cursor, LangChain) can use to discover, pay, and invoke capabilities
across the AIMarket federation.

New in 2.1.1: Pay-on-Verified — opt an invoke into verified settlement with a
`verify` block, read the hub's `verification` envelope off the result, and poll
the verdict with `wait_for_verification` (exponential backoff, no deadline by
default).

New in 2.2.0: receipts are verified against the key of the party that SIGNED them.
A hub is a broker — a federated capability's receipt carries the provider's
signature, not the hub's — so 2.1.x reported `invalid-signature` for every
federated capability it called. On modelmarket.dev that was 42 of 47, all valid.
Keys are now resolved per origin and cached; nothing about the call changes.

New in 2.1.0: cryptographic receipt verification — invoke receipts are checked
against an Ed25519 key from /.well-known (enabled by default).

Usage:
    pip install aimarket-agent
    aimarket-agent run "translate spec to 5 languages" --budget 3.00
"""

from aimarket_agent.agent import AIMarketAgent
from aimarket_agent.receipts import (
    OriginVerifiers,
    ReceiptVerifier,
    VerifyResult,
    unsigned_receipt_fields,
    verify_receipt,
)

__all__ = [
    "AIMarketAgent",
    "ReceiptVerifier",
    "OriginVerifiers",
    "VerifyResult",
    "verify_receipt",
    "unsigned_receipt_fields",
    "__version__",
]
__version__ = "2.2.0"

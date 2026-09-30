"""AIMarket Agent v2.5.0 — Reference consumer for AIMarket Protocol.

MIT Licensed. Lightweight pip-installable agent that any AI (Claude, GPT,
Cursor, LangChain) can use to discover, pay, and invoke capabilities
across the AIMarket federation.

New in 2.1.1: Pay-on-Verified — opt an invoke into verified settlement with a
`verify` block, read the hub's `verification` envelope off the result, and poll
the verdict with `wait_for_verification` (exponential backoff, no deadline by
default).

New in 2.5.0: buying credit with USDC on a hub (hub docs/credits-topup.md) — `create_account`,
`topup_quote`, `topup_redeem`, `topup_status`, and `aimarket_agent.topup`, which turns the hub's 402
into EIP-712 typed data to sign and `transferWithAuthorization` calldata to send, without ever
touching a key.

New in 2.4.0: `A2AClient` — the same market over A2A 1.0 JSON-RPC (a hub's `/a2a`): search,
and invoke as a Task paid with credits, a mandate (the exact signed bytes travel as a raw part)
or x402 (`pay_x402`, the payload carrying the settle txHash). A retried invoke with the same
message id returns its task instead of paying twice.

New in 2.3.0: mandates and subcontracting (aimarket-protocol/mandates.md). An owner
issues a signed mandate to an agent key with its own limits; the agent pays with it and
never holds the owner's API key. A provider can buy from other providers inside a job and
spend the buyer's pass-through allowance (`subcontract=`), with a bill of materials back.

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

from aimarket_agent.a2a import A2AClient, A2AError
from aimarket_agent.agent import AIMarketAgent
from aimarket_agent.mandates import (
    AgentKey,
    JobContext,
    Mandate,
    issue_mandate,
    mandate_digest,
    owner_change_authorization,
    owner_link_payload,
    owner_unlink_payload,
    request_proof,
    revoke_payload,
)
from aimarket_agent.receipts import (
    OriginVerifiers,
    ReceiptVerifier,
    VerifyResult,
    unsigned_receipt_fields,
    verify_receipt,
)

__all__ = [
    "A2AClient",
    "A2AError",
    "AIMarketAgent",
    "AgentKey",
    "JobContext",
    "Mandate",
    "issue_mandate",
    "mandate_digest",
    "owner_change_authorization",
    "owner_link_payload",
    "owner_unlink_payload",
    "request_proof",
    "revoke_payload",
    "ReceiptVerifier",
    "OriginVerifiers",
    "VerifyResult",
    "verify_receipt",
    "unsigned_receipt_fields",
    "__version__",
]
__version__ = "2.5.0"

"""AIMarket Agent v2.1.0 — Reference consumer for AIMarket Protocol.

MIT Licensed. Lightweight pip-installable agent that any AI (Claude, GPT,
Cursor, LangChain) can use to discover, pay, and invoke capabilities
across the AIMarket federation.

New in 2.1.0: cryptographic receipt verification — invoke receipts are checked
against the hub's Ed25519 key from /.well-known (enabled by default).

Usage:
    pip install aimarket-agent
    aimarket-agent run "translate spec to 5 languages" --budget 3.00
"""

from aimarket_agent.agent import AIMarketAgent
from aimarket_agent.receipts import ReceiptVerifier, VerifyResult, verify_receipt

__all__ = [
    "AIMarketAgent",
    "ReceiptVerifier",
    "VerifyResult",
    "verify_receipt",
    "__version__",
]
__version__ = "2.1.0"

<!-- aicom-mirror-notice -->
> **📖 Read-only mirror.** `aimarket-agent` is published from the canonical AI-Factory monorepo.
> **Pull requests are not accepted** — any commit pushed here is overwritten by
> `scripts/mirror_satellites.sh` on the next sync.
> 🐞 Found a bug or have a request? Please **[open an issue](https://github.com/alexar76/aimarket-agent/issues)**.

# AIMarket Agent v2.2.0

<!-- aicom-readme-badges -->
<p align="center">
  <a href="https://github.com/alexar76/aimarket-agent/actions/workflows/ci.yml"><img src="https://raw.githubusercontent.com/alexar76/aimarket-agent/main/docs/badges/ci.svg" alt="CI" /></a>
  <a href="https://raw.githubusercontent.com/alexar76/aimarket-agent/main/docs/badges/coverage.svg"><img src="https://raw.githubusercontent.com/alexar76/aimarket-agent/main/docs/badges/coverage.svg" alt="Test coverage" /></a>
  <a href="https://github.com/alexar76/aimarket-agent/blob/main/LICENSE"><img src="https://raw.githubusercontent.com/alexar76/aimarket-agent/main/docs/badges/license.svg" alt="License: Apache-2.0" /></a>
</p>
<!-- /aicom-readme-badges -->










> **Ecosystem:** [AICOM overview & live demos](https://modeldev.modelmarket.dev) · **Community:** [Discord · Pollux](https://discord.gg/aimarket) · [Telegram · Castor](https://t.me/just_for_agents)

**Reference consumer agent for the AIMarket Protocol.**
`pip install aimarket-agent` — any AI (Claude, GPT, Cursor, LangChain) can discover, pay, and invoke capabilities across the AIMarket federation. MIT Licensed.

> **SDK versions:** this package is on the **Python 2.x line**. Dart/TypeScript/Rust SDKs use **0.1.x** — see [`docs/sdk-version-policy.md`](https://github.com/alexar76/aicom/blob/main/docs/sdk-version-policy.md).

## Live Hub

This agent connects to **[modelmarket.dev](https://modelmarket.dev)** — the reference hub, currently serving 47 capabilities — 5 of its own and 42 federated. Federated peers and **[oracles](https://github.com/alexar76/oracles)** (Platon randomness, Chronos VDF, Murmuration consensus, Lumen reputation, …) appear in search when their manifests are pinned on the hub.

## Install

```bash
pip install aimarket-agent
```

## Quick Start

```bash
# Full autonomous cycle
aimarket-agent run "translate spec to 5 languages + legal review" \
  --base-url https://modelmarket.dev \
  --budget 3.00

# Search capabilities
aimarket-agent search "code review" --base-url https://modelmarket.dev

# Invoke a single capability
aimarket-agent invoke prod-translate/translate.multi@v2 \
  --base-url https://modelmarket.dev \
  --input '{"text":"Hello world"}'
```

## Python SDK

```python
from aimarket_agent import AIMarketAgent

agent = AIMarketAgent(
    base_url="https://modelmarket.dev",
    budget=3.00,
    affiliate_id="my_app"
)

# Full cycle: discover → channel → invoke → settle → BOM
result = agent.run("translate spec to 5 languages + legal review")
print(f"Spent: ${result['total_spent_usd']:.2f}")

# Discovery only
capabilities = agent.discover("summarize long documents")
for c in capabilities:
    print(f"  {c['capability_id']} — ${c.get('price_per_call_usd', 0):.2f}")

# Single invoke
result = agent.invoke_single(
    "prod-translate", "translate.multi@v2",
    {"text": "Hello world", "locales": ["ru", "fr", "de"]}
)

# Pay-on-Verified: escrow the debit until Metis verifies the output.
# The result carries a "verification" envelope; poll it to the verdict
# (exponential backoff, no deadline by default — pass max_wait_s to bound).
result = agent.invoke_single(
    "prod-translate", "translate.multi@v2",
    {"text": "Hello world"},
    verify={"requested": True, "intent": "translate to French", "mode": "auto"},
)
final = agent.wait_for_verification(result["receipt"]["nonce"])
print(final["verification"]["status"])  # "settled" (paid) or "refunded"
```

## Full Autonomous Cycle

```
① GET  /.well-known/ai-market.json        → discover hub + its signing key
② GET  /ai-market/v2/search?intent=…      → rank capabilities
③ POST /ai-market/v2/channel/open         → pre-fund channel
④ POST /ai-market/v2/invoke               → invoke (safety-gated, signed receipt)
⑤ POST /ai-market/v2/channel/close        → settle + refund
⑥ GET  {source_hub}/.well-known/…         → the ORIGIN's key, to verify the receipt
⑦ Save bill_of_materials.json             → signed audit trail
```

## Safety Gate

If an invocation is blocked by the safety gate (injection, PII, etc.), the agent receives HTTP 403 with a signed rejection receipt and the channel is auto-refunded.

## Output

```
[search]   47 capabilities · 5 local, 42 federated
[plan]     translate.multi@v2  (est $0.40)
[channel]  opened ch_a8f3 with $3.00 deposit
[call]     translate.multi@v2 ....... $0.40 ✓ 8.1s
[settle]   used $0.40, refund $2.60
[saved]    bill_of_materials.json
```

## Configuration

| CLI flag | Default | Description |
|----------|---------|-------------|
| `--base-url` | `http://127.0.0.1:9083` | Hub URL |
| `--budget` | `3.0` | Max budget in USD |
| `--affiliate` | — | Affiliate ID for revenue share |
| `--json` | false | Output as JSON |

## Demo

- **Live:** https://modelmarket.dev/
- **Docs:** https://github.com/alexar76/aimarket-agent/blob/main/README.md

## Related repos

| Repo | Role |
|------|------|
| [aimarket-hub](https://github.com/alexar76/aimarket-hub) | Reference hub |
| [aimarket-protocol](https://github.com/alexar76/aimarket-protocol) | Normative v2 spec |
| [aimarket-sdks](https://github.com/alexar76/aimarket-sdks) | TS/Rust/Dart SDKs |
| [argus](https://github.com/alexar76/argus) | Personal agent reference client |
| [dioscuri](https://github.com/alexar76/dioscuri) | Twin community agents — MNEMOSYNE Q&A |

## Community

The [DIOSCURI](https://github.com/alexar76/dioscuri) twins answer questions from synced GitHub docs.

| Channel | Twin | Best for |
|---------|------|----------|
| [Discord](https://discord.gg/aimarket) | Pollux | Help, ideas, show-and-tell |
| [Telegram](https://t.me/just_for_agents) | Castor | Releases, digests, quick news |

**Ecosystem map:** [Alien Monitor](https://magic-ai-factory.com/monitor/) · [AICOM](https://magic-ai-factory.com)

## License

MIT · Maintained by AI-Factory · [modelmarket.dev](https://modelmarket.dev) · [Hub API](https://modelmarket.dev/.well-known/ai-market.json)

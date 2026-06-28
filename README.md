<!-- aicom-mirror-notice -->
> **📖 Read-only mirror.** `aimarket-agent` is published from the canonical AI-Factory monorepo.
> **Pull requests are not accepted** — any commit pushed here is overwritten by
> `scripts/mirror_satellites.sh` on the next sync.
> 🐞 Found a bug or have a request? Please **[open an issue](https://github.com/alexar76/aimarket-agent/issues)**.

# AIMarket Agent v2.1.0

<!-- aicom-readme-badges -->
<p align="center">
  <a href="https://github.com/alexar76/aimarket-agent/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/alexar76/aimarket-agent/ci.yml?branch=main&label=CI" alt="CI" /></a>
  <a href="docs/badges/coverage.svg"><img src="docs/badges/coverage.svg" alt="Test coverage" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache--2.0-blue.svg" alt="License: Apache-2.0" /></a>
</p>
<!-- /aicom-readme-badges -->








> **Ecosystem:** [AICOM overview & live demos](https://modeldev.modelmarket.dev)

**Reference consumer agent for the AIMarket Protocol.**
`pip install aimarket-agent` — any AI (Claude, GPT, Cursor, LangChain) can discover, pay, and invoke capabilities across the AIMarket federation. MIT Licensed.

> **SDK versions:** this package is on the **Python 2.x line**. Dart/TypeScript/Rust SDKs use **0.1.x** — see [`docs/sdk-version-policy.md`](https://github.com/alexar76/aicom/blob/main/docs/sdk-version-policy.md).

## Live Hub

This agent connects to **[modelmarket.dev](https://modelmarket.dev)** — the reference hub with 12 capabilities and 15 plugins. Federated peers and **[oracles](https://github.com/alexar76/oracles)** (Platon randomness, Chronos VDF, Murmuration consensus, Lumen reputation, …) appear in search when their manifests are pinned on the hub.

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
```

## Full Autonomous Cycle

```
① GET  /.well-known/ai-market.json     → discover hub
② POST /ai-market/discover              → search capabilities
③ POST /ai-market/channel/open          → pre-fund channel
④ POST /capabilities/{pid}/{cid}/invoke → invoke (safety-gated)
⑤ POST /ai-market/channel/close         → settle + refund
⑥ Save bill_of_materials.json           → signed audit trail
```

## Safety Gate

If an invocation is blocked by the safety gate (injection, PII, etc.), the agent receives HTTP 403 with a signed rejection receipt and the channel is auto-refunded.

## Output

```
[discover] 12 capabilities across 12 products
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

## License

MIT · Maintained by AI-Factory · [modelmarket.dev](https://modelmarket.dev) · [Hub API](https://modelmarket.dev/.well-known/ai-market.json)

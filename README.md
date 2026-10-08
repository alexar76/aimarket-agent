<!-- aicom-mirror-notice -->
> **📖 Read-only mirror.** `aimarket-agent` is published from the canonical AI-Factory monorepo.
> **Pull requests are not accepted** — any commit pushed here is overwritten by
> `scripts/mirror_satellites.sh` on the next sync.
> 🐞 Found a bug or have a request? Please **[open an issue](https://github.com/alexar76/aimarket-agent/issues)**.

# AIMarket Agent v2.2.0

<!-- aicom-readme-badges -->
<p align="center">
  <a href="https://github.com/alexar76/aimarket-agent/actions/workflows/ci.yml"><img src="https://raw.githubusercontent.com/alexar76/aimarket-agent/refs/heads/main/docs/badges/ci.svg" alt="CI" /></a>
  <a href="https://raw.githubusercontent.com/alexar76/aimarket-agent/refs/heads/main/docs/badges/coverage.svg"><img src="https://raw.githubusercontent.com/alexar76/aimarket-agent/refs/heads/main/docs/badges/coverage.svg" alt="Test coverage" /></a>
  <a href="https://github.com/alexar76/aimarket-agent/blob/main/LICENSE"><img src="https://raw.githubusercontent.com/alexar76/aimarket-agent/refs/heads/main/docs/badges/license.svg" alt="License: Apache-2.0" /></a>
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

### Over A2A 1.0 (2.4.0)

The same market through a hub's standard A2A endpoint (`POST /a2a`): an invoke is a Task that
completes with `result`, `aimarket-receipt` and `provenance` artifacts, or waits for a payment
or credentials. Pay with `api_key=`, `mandate=` (the exact signed bytes travel as a raw part),
or x402 — `pay_x402(task, payload)`, the payload carrying the txHash of the transfer you made,
because the hub verifies payments and never settles them. A retried `invoke` with the same
`message_id` returns its task instead of paying twice.

```python
from aimarket_agent import A2AClient
from aimarket_agent.a2a import payment_required, task_result, task_state

client = A2AClient("https://modelmarket.dev", api_key="amk_…")
offer = client.search("weather at a sensor", budget=0.05)["matches"][0]
task = client.invoke(offer["product_id"], offer["capability_id"], {"device_id": "om-wx-01"},
                     source_hub=offer["source_hub"], max_price_usd=0.01)
if task_state(task) == "TASK_STATE_COMPLETED":
    print(task_result(task), task["receipt_verified"])
elif payment_required(task):           # no key: the hub quoted its x402 terms
    task = client.pay_x402(task, {"x402Version": 2, "scheme": "exact", "txHash": "0x…"})
```

Wire contract and task states: `aimarket-hub/docs/a2a.md`.

## Paying with a mandate

An owner can fund an agent **without giving it the API key**. The owner signs a mandate (AMD/1)
for the agent's own Ed25519 key — limits per call, per day and in total, on which capabilities, at
which hubs, until when — and the hub verifies it and enforces the limits itself.

```python
import httpx
from aimarket_agent import AIMarketAgent, AgentKey, Mandate, issue_mandate, owner_link_payload

HUB = "https://modelmarket.dev"               # must equal the hub's AIMARKET_HUB_URL
owner = AgentKey.from_seed_hex(OWNER_SEED_HEX)
agent = AgentKey.generate()                   # the agent keeps agent.seed_hex(), never the API key

# Owner, once: link the owner's DID to the credit account the API key names.
account_id = httpx.get(f"{HUB}/ai-market/v2/mandates/owners",
                       headers={"X-API-Key": API_KEY}).json()["account_id"]
httpx.post(f"{HUB}/ai-market/v2/mandates/owners", headers={"X-API-Key": API_KEY},
           json=owner_link_payload(owner, hub_origin=HUB, account_id=account_id, require_mandate=True))

# Owner: issue and register a mandate for the agent's key (USD here, integer µUSD in the document).
doc = issue_mandate(owner, agent.did, audience=[HUB], scope=["gaia.*"],
                    per_call_usd=0.02, per_day_usd=1.00, total_usd=10.00, valid_days=30)
httpx.post(f"{HUB}/ai-market/v2/mandates", json=doc)

# Agent: every invoke is signed with the agent key over its exact body.
client = AIMarketAgent(HUB, mandate=Mandate(document=doc, key=agent, hub_origin=HUB))
r = client.invoke_single("gaia.gateway", "gaia.weather.read@v1", {"latitude": 60.17, "longitude": 24.94})
```

- `hub_origin` is the hub's base URL, path included for a hub mounted under one; the path a proof
  signs is relative to it (`POST /ai-market/v2/invoke`).
- A 403 refusal of the authority behind a call (`mandate_invalid`, `mandate_scope`, `job_invalid`,
  `job_limit`, `owner_authorization_required`) comes back as
  `{"refused": True, "error": …, "detail": …}`, never as `safety_blocked`. A limit reached is the
  hub's 402 body: `{"error": "mandate_limit", "limit": "perDay", "mandate": "sha256-…", …}`.
- `revoke_payload(owner, hub_origin=HUB, digest=…)` is the body of
  `POST /ai-market/v2/mandates/revoke`. After the first owner, `owner_link_payload(...,
  authorized_by=existing_owner)` (with `action="policy"` to change `require_mandate`) and
  `owner_unlink_payload(did, ..., authorized_by=existing_owner)` carry an existing owner's
  signature. `request_proof(key, hub_origin=HUB, leaf_digest=…, body=b"", method="GET", path=…)`
  signs other requests, such as reading usage at `GET /ai-market/v2/mandates/{digest}`.
- `issue_mandate(agent, sub_agent.did, ..., parent=doc)` re-delegates a narrower part of a mandate
  to another key; `subcontract_allowance_usd=` and `subcontract_max_depth=` let a mandate pay for
  subcontracting allowances.

Operator guide: [aimarket-hub/docs/mandates.md](https://github.com/alexar76/aimarket-hub/blob/main/docs/mandates.md) ·
protocol: [aimarket-protocol/mandates.md](https://github.com/alexar76/aimarket-protocol/blob/main/mandates.md).

## Buying from inside a job (subcontracting)

A **provider** the hub executes receives a job token (`X-AIMarket-Job`, plus `X-AIMarket-Job-Grant`
when the buyer set aside an allowance). Pass the headers of the request you are serving, and
anything you buy with them joins the buyer's job tree:

```python
from aimarket_agent import AIMarketAgent, JobContext

job = JobContext.from_headers(request.headers)      # None when the hub sent no token
buyer = AIMarketAgent(job.hub or HUB, api_key=MY_OWN_KEY)
r = buyer.invoke_single("wx", "wx.read@v1", {"q": 1}, job=job)
r["job"]   # {"job_id", "node", "parent", "depth", "funded_by"}
```

- With a grant (`job.funded`), the purchase is paid from the buyer's allowance and the SDK sends
  no other payment — the hub refuses a grant that comes with one.
- Without a grant, the client pays its own way (`api_key=`, `mandate=` or a channel) and the
  purchase is only linked into the tree. To pay your own way although a grant came, pass
  `JobContext(token=job.token, hub=job.hub)`.

The **buyer** asks for an allowance on the root call and reads the bill of materials back:

```python
r = client.invoke_single("brief", "brief.make@v1", {"topic": "x"},
                         subcontract={"allowance_usd": 0.005, "max_depth": 2})
bill = r["subcontracting"]   # job_id, nodes, allowance_usd, spent_usd, released_usd
```

`spent_usd` and `released_usd` are `null` while the hub has not settled the allowance yet. On a
mandated call the mandate must allow subcontracting (`subcontract_allowance_usd=` in
`issue_mandate`).

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

**Ecosystem map:** [Alien Monitor](https://monitor.modelmarket.dev/) · [AICOM](https://magic-ai-factory.com)

## License

MIT · Maintained by AI-Factory · [modelmarket.dev](https://modelmarket.dev) · [Hub API](https://modelmarket.dev/.well-known/ai-market.json)

# aimarket-agent — Python SDK & CLI guide

The reference **consumer agent** for the [AIMarket Protocol v2](https://github.com/alexar76/aimarket-protocol), written in pure Python. One `pip install` lets any server agent, LangChain tool, or CLI **discover → pay → invoke** capabilities across the AIMarket federation.

> **Live hub:** [modelmarket.dev](https://modelmarket.dev) · **Ecosystem:** [modeldev.modelmarket.dev](https://modeldev.modelmarket.dev) · **Repo:** [alexar76/aimarket-agent](https://github.com/alexar76/aimarket-agent) · **Cross-platform SDKs:** [`../../aimarket-sdks/docs/en.md`](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md)

---

## 1. What it is

`aimarket-agent` is the **Python** member of the AIMarket SDK family. It does one thing: it turns a plain-language task into a discovered, paid-for, invoked capability call and returns a signed bill of materials.

It is intentionally lightweight — only `httpx` + `cryptography`, no FastAPI, no database — so it drops cleanly into a LangChain tool, a background worker, a Lambda, or a one-line CLI invocation. This package is on the **Python 2.1.x** line (the Dart/TypeScript/Rust SDKs are a separate `0.1.x` line — see [§8 Versioning](#8-versioning)).

---

## 2. Install

```bash
pip install aimarket-agent
```

| Requirement | Value |
|-------------|-------|
| Python | **>= 3.11** |
| Runtime deps | `httpx>=0.28`, `cryptography>=44` |
| Console script | `aimarket-agent` |
| License | MIT |

---

## 3. The model — a stateless consumer (no wallet here)

This is the single most important thing to understand, because the Python agent is **architecturally different** from the cross-platform SDKs.

| | Python `aimarket-agent` (this package) | Dart / TypeScript / Rust ([`../../aimarket-sdks/docs/en.md`](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md)) |
|---|---|---|
| State | **Stateless HTTP consumer** | Holds a wallet / signing key |
| Public-API wallet/seed | **None** | `walletKey` (Ed25519 seed), optional `ethereumPrivateKeyHex` |
| Signs the invoke request | **No** `X-Market-Signature` is sent | Yes — `X-Market-Signature: ed25519:<base64>` |
| Where auth lives | **Hub-side** | Client-side |
| Crypto it *does* use | **Verifies** hub receipts against the hub's Ed25519 public key | Signs requests + verifies receipts |

There is **no `walletKey`, no seed, and no signing** anywhere in the Python public API. The agent never produces a signature header. Payment authorization is handled hub-side; the agent simply talks to the hub's v2 endpoints over plain JSON/HTTP and references a payment channel by id (`X-Payment-Channel`) and an optional affiliate id (`X-AIMarket-Affiliate`).

What the agent *can* do cryptographically is **verify** what comes back. When `verify_receipts=True` (the default), it fetches the hub's advertised Ed25519 public key from `/.well-known/ai-market.json` and checks every invoke receipt against it. The result of that check is surfaced on the response (`receipt_verified`, `receipt_verify_reason`) and is **never raised** — a failed verification does not abort your call, it just flags the output as unverified so you can decide what to do.

> If you need a client that signs requests with its own wallet (EVM apps, on-chain channel debits via EIP-712), use the cross-platform SDKs documented in [`../../aimarket-sdks/docs/en.md`](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md). They are a separate package family.

---

## 4. Library usage

```python
from aimarket_agent import AIMarketAgent

agent = AIMarketAgent(
    base_url="https://modelmarket.dev",
    budget=3.00,
    affiliate_id="my_app",
    verify_receipts=True,
)
```

### Constructor

```python
AIMarketAgent(
    base_url: str,
    budget: float = 3.0,
    timeout: float = 120.0,
    affiliate_id: str = "",
    verify_receipts: bool = True,
)
```

| Argument | Default | Meaning |
|----------|---------|---------|
| `base_url` | — (required) | Hub URL. Trailing slash is stripped. |
| `budget` | `3.0` | Max spend in USD; bounds the channel deposit and the discovery search. |
| `timeout` | `120.0` | Per-request HTTP timeout (seconds). |
| `affiliate_id` | `""` | Sent as `X-AIMarket-Affiliate` for revenue share; empty = not sent. |
| `verify_receipts` | `True` | Verify each receipt against the hub's Ed25519 key from `/.well-known`. |

### Methods

```python
# Full autonomous cycle: discover → channel → invoke → settle → bill of materials.
result = agent.run("translate spec to 5 languages + legal review")
print(f"Spent: ${result['total_spent_usd']:.2f}")
print("verified OK:", result["bill_of_materials"]["all_ok"])

# Discovery only — returns a list of capability-match dicts (no invoke, no spend).
matches = agent.discover("summarize long documents", limit=8)
for m in matches:
    print(m["capability_id"], "$", m.get("price_per_call_usd", 0))

# Invoke one capability directly (opens + closes a channel around the call).
res = agent.invoke_single(
    "prod-translate",                       # product_id
    "translate.multi@v2",                   # capability_id
    {"text": "Hello world", "locales": ["ru", "fr", "de"]},  # input
    source_hub="local",                     # or a federated hub URL
)

# Verify a single receipt out-of-band against the hub's public key.
vr = agent.verify_receipt(res["receipt"])
print(bool(vr), vr.reason)
```

| Method | Signature | Returns |
|--------|-----------|---------|
| `run` | `run(task: str)` | bill-of-materials `dict` (see §6) |
| `discover` | `discover(query: str, limit: int = 8)` | `list[dict]` of capability matches |
| `invoke_single` | `invoke_single(product_id, capability_id, input_payload: dict, source_hub: str = "local")` | invoke-result `dict` |
| `verify_receipt` | `verify_receipt(receipt: dict)` | `receipts.VerifyResult` (truthy when verified; has `.reason`) |
| `close` | `close()` | closes the underlying `httpx.Client` |

The agent is also a context manager, so the HTTP client is always cleaned up:

```python
with AIMarketAgent(base_url="https://modelmarket.dev", budget=1.50) as agent:
    matches = agent.discover("code review")
```

---

## 5. CLI usage

Installing the package puts an `aimarket-agent` console script on your `PATH`.

```bash
# Full autonomous cycle
aimarket-agent run "translate spec to 5 languages + legal review" \
  --base-url https://modelmarket.dev \
  --budget 3.00

# Discover capabilities (no spend)
aimarket-agent search "code review" --base-url https://modelmarket.dev

# Invoke a single capability — the ref is product_id/capability_id
aimarket-agent invoke prod-translate/translate.multi@v2 \
  --base-url https://modelmarket.dev \
  --input '{"text":"Hello world"}'
```

| Command | Positional | Flags |
|---------|-----------|-------|
| `run` | `task` | `--base-url`, `--budget`, `--affiliate`, `--json` |
| `search` | `query` | `--base-url`, `--limit`, `--json` |
| `invoke` | `capability_ref` (`product_id/capability_id`) | `--base-url`, `--input`, `--budget` |

| CLI flag | Default | Description |
|----------|---------|-------------|
| `--base-url` | `http://127.0.0.1:9083` | Hub URL (local dev default; use `https://modelmarket.dev` for the live hub) |
| `--budget` | `3.0` | Max budget in USD |
| `--affiliate` | `""` | Affiliate id for revenue share (`run` only) |
| `--limit` | `8` | Max search results (`search` only) |
| `--input` | `{}` | JSON input payload (`invoke` only) |
| `--json` | off | Emit raw JSON instead of the human-readable summary |

`run` writes a `bill_of_materials.json` audit file to the current directory and exits non-zero if the cycle did not fully succeed. Example human-readable output:

```
[plan]  translate.multi@v2  (est $0.40)
[call]  translate.multi@v2 $0.40 ✓
[settle] used $0.40, refund $2.60
[total] $0.40
[saved] bill_of_materials.json
```

---

## 6. The 5-phase cycle, as Python runs it

`run(task)` drives the same canonical 5-phase value lifecycle as the cross-platform SDKs, mapped onto the hub's v2 endpoints. The difference is purely in *who holds the key* (§3) — the phases are identical.

| # | Canonical phase | Hub endpoint (v2) | What `run()` does |
|---|-----------------|-------------------|-------------------|
| 1 | **Discovery** | `GET /.well-known/ai-market.json` → `GET /ai-market/v2/search` | reads the well-known doc, builds the receipt verifier from the hub key, then searches by `intent`/`budget`/`limit` and synthesises a one-step plan from the first match |
| 2 | **Channel** | `POST /ai-market/v2/channel/open` | opens a pre-funded channel sized to `budget`; if the hub returns `404` (no channels plugin) it silently proceeds channel-less |
| 3 | **Invoke** | `POST /ai-market/v2/invoke` | calls the capability with headers `X-Payment-Channel` (if a channel opened) and `X-AIMarket-Affiliate` (if set) — **no signature header** |
| 4 | **Settle** | `POST /ai-market/v2/channel/close` | closes the channel and records the settlement (used / refund) |
| 5 | **Verify** | *(local)* | when `verify_receipts=True`, checks each receipt against the hub's Ed25519 public key and stamps `receipt_verified` / `receipt_verify_reason` on the result |

For predictable spend, `run()` caps the plan at the **first** matching capability (multi-step DAGs are a future protocol-level feature). Use `discover()` + `invoke_single()` if you want to drive the steps yourself.

The returned bill-of-materials dict has these keys:

```python
{
  "task": "...",
  "plan": [{"product_id": ..., "capability_id": ..., "source_hub": ..., "est_price_usd": ...}],
  "results": [ { "success": True, "result": {...}, "price_usd": 0.40,
                 "receipt": {...}, "receipt_verified": True, "receipt_verify_reason": "..." } ],
  "settlement": {"used_usd": 0.40, "refund_usd": 2.60},
  "channel_id": "ch_a8f3",
  "total_spent_usd": 0.40,
  "all_ok": True,
  "protocol_version": "v2",
  "agent_version": "2.0.0"
}
```

`run()` returns `{"task": ..., "ok": ..., "bill_of_materials": <the dict above>, "total_spent_usd": ...}`.

---

## 7. Budget & errors

**Budget.** `budget` is the spend ceiling in USD. It is sent as the `budget` parameter on discovery (so the hub only returns capabilities you can afford) and it is the `deposit_usd` used to pre-fund the payment channel. Unspent deposit is refunded at settle time (`settlement.refund_usd`). Because `run()` invokes only the first match, a single `run()` spends at most one capability's `price_usd`.

**Errors.** The agent is defensive by design: discovery and settlement failures are caught and returned as data, not raised. Invoke responses with a meaningful HTTP status are turned into structured result entries:

| Condition | HTTP | What you get back |
|-----------|------|-------------------|
| Discovery / search failed | 4xx/5xx | `{"error": "discovery failed: ..."}` or `{"error": "search failed: ..."}` (no plan) |
| No channels plugin on hub | 404 | channel opens silently as `""`; cycle continues channel-less |
| Safety gate tripped | 403 | result entry `{"safety_blocked": True, "category": ..., "reason": ...}`; cycle stops |
| Payment required | 402 | result entry `{"payment_required": True, "detail": ...}`; cycle stops |
| Other invoke failure | other | result entry `{"error": "HTTP <code>", "capability_id": ...}`; cycle stops |
| Receipt failed verification | 200 | result kept, but `receipt_verified=False` + `receipt_verify_reason`; **not** raised |

When the safety gate blocks a call (injection, PII, etc.), the hub returns HTTP 403 with a signed rejection receipt and the channel is auto-refunded — so a blocked call costs nothing.

---

## 8. Versioning

This package follows the **Python 2.1.x** line. That is intentional and separate from the Dart/TypeScript/Rust SDKs, which share the **0.1.x** multi-language line. All of them target **AIMarket Protocol v2**.

| Package | Registry | Version line |
|---------|----------|--------------|
| `aimarket-agent` (this) | PyPI | **2.1.x** |
| `@aimarket/agent` | npm | 0.1.x |
| `aimarket_agent` | pub.dev | 0.1.x |
| `aimarket-agent` (crate) | crates.io | 0.1.x |

The Python line shipped first (CLI, `run()` loop, BOM audit trail, hub-trust channels, no wallet in the public API); the multi-language line launched together with client-side **Ed25519** invoke signing (plus an optional EIP-712/secp256k1 key for on-chain channel debits). Python may move to **3.x** if/when its public API gains explicit wallet + signing parity. Full rationale and the release-trigger matrix: [`../../docs/sdk-version-policy.md`](../../docs/sdk-version-policy.md).

---

## 9. Related docs

- [AIMarket cross-platform SDKs (Dart / TypeScript / Rust)](https://github.com/alexar76/aimarket-sdks/blob/main/docs/en.md) — the wallet-holding SDK family
- [SDK version policy](../../docs/sdk-version-policy.md) — why Python is 2.x and the others are 0.1.x
- [Oracles](https://github.com/alexar76/oracles) — verifiable randomness, VDF, consensus, reputation capabilities discoverable from this agent
- [AIMarket Hub](https://github.com/alexar76/aimarket-hub) — lists and routes capabilities (the `base_url` you point at)
- [Live hub well-known doc](https://modelmarket.dev/.well-known/ai-market.json)

---

🇬🇧 [English](en.md) · 🇷🇺 [Русский](ru.md) · 🇪🇸 [Español](es.md)

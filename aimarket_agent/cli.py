#!/usr/bin/env python3
"""AIMarket Agent CLI — pip-installable reference consumer.

Usage:
    aimarket-agent run "translate spec to 5 langs" --budget 3.00
    aimarket-agent search "legal review"
    aimarket-agent invoke prod-xxx/translate.multi@v2 --input '{"text":"hello"}'
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from aimarket_agent.agent import AIMarketAgent

# The hub's free trial: a stable id of your choosing in X-AIMarket-Sandbox-Visitor, no payment,
# a few invokes per window. Read from the environment so the README's one-liners stay short.
_SANDBOX_ENV = "AIMARKET_SANDBOX_VISITOR"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_RESET = "\033[0m"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AIMarket Agent — AI-to-AI protocol consumer"
    )
    sub = parser.add_subparsers(dest="command")

    # run
    run_p = sub.add_parser("run", help="Execute full autonomous cycle")
    run_p.add_argument("task", nargs="?", default="translate spec to 5 langs + legal review")
    run_p.add_argument("--base-url", default="http://127.0.0.1:9083")
    run_p.add_argument("--budget", type=float, default=3.0)
    run_p.add_argument("--affiliate", default="")
    run_p.add_argument("--sandbox-visitor", default=os.environ.get(_SANDBOX_ENV, ""),
                       help=f"free-trial visitor id (8-64 chars, letters/digits/-/_); default ${_SANDBOX_ENV}")
    run_p.add_argument("--json", action="store_true")

    # search
    search_p = sub.add_parser("search", help="Discover capabilities")
    search_p.add_argument("query", nargs="?", default="")
    search_p.add_argument("--base-url", default="http://127.0.0.1:9083")
    search_p.add_argument("--limit", type=int, default=8)
    search_p.add_argument("--json", action="store_true")

    # invoke
    invoke_p = sub.add_parser("invoke", help="Invoke a single capability")
    invoke_p.add_argument("capability_ref", help="product_id/capability_id")
    invoke_p.add_argument("--base-url", default="http://127.0.0.1:9083")
    invoke_p.add_argument("--input", default="{}", help="JSON input payload")
    invoke_p.add_argument("--budget", type=float, default=3.0)
    invoke_p.add_argument("--source-hub", default="local",
                          help="hub that serves a federated capability — the source_hub shown by `search`")
    invoke_p.add_argument("--sandbox-visitor", default=os.environ.get(_SANDBOX_ENV, ""),
                          help=f"free-trial visitor id (8-64 chars, letters/digits/-/_); default ${_SANDBOX_ENV}")

    args = parser.parse_args()

    if args.command == "run":
        return _cmd_run(args)
    elif args.command == "search":
        return _cmd_search(args)
    elif args.command == "invoke":
        return _cmd_invoke(args)
    else:
        parser.print_help()
        return 0


def _cmd_run(args) -> int:
    agent = AIMarketAgent(
        base_url=args.base_url,
        budget=args.budget,
        affiliate_id=args.affiliate,
        sandbox_visitor=args.sandbox_visitor,
    )
    try:
        result = agent.run(args.task)
    except KeyboardInterrupt:
        print(f"{_YELLOW}Interrupted{_RESET}", file=sys.stderr)
        return 130
    finally:
        agent.close()

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    if result.get("error"):
        print(f"[{_RED}error{_RESET}] {result['error']}", file=sys.stderr)
        return 1

    plan = result.get("plan") or []
    steps = " → ".join(s["capability_id"] for s in plan)
    est = result.get("estimated_total_usd", 0)
    print(f"[{_GREEN}plan{_RESET}] {steps}  (est ${est:.4f})")

    bom = result.get("bill_of_materials") or {}
    for r in bom.get("results") or []:
        if r.get("safety_blocked"):
            print(f"[{_RED}safety{_RESET}] {r['capability_id']} blocked: {r.get('category', '?')}")
            continue
        ok = r.get("success")
        mark = "✓" if ok else "✗"
        color = _GREEN if ok else _RED
        print(f"[{color}call{_RESET}] {r.get('capability_id', '?')} ${r.get('price_usd', 0):.4f} {mark}")

    settlement = bom.get("settlement") or {}
    used = settlement.get("used_usd", 0)
    refund = settlement.get("refund_usd", 0)
    print(f"[{_GREEN}settle{_RESET}] used ${used:.4f}, refund ${refund:.4f}")

    total = result.get("total_spent_usd", 0)
    print(f"[{_BOLD}total{_RESET}] ${total:.4f}")

    out_path = "bill_of_materials.json"
    with open(out_path, "w") as f:
        json.dump(bom, f, indent=2, ensure_ascii=False)
    print(f"[{_DIM}saved{_RESET}] {out_path}")

    return 0 if result.get("ok") else 1


def _cmd_search(args) -> int:
    agent = AIMarketAgent(base_url=args.base_url)
    try:
        matches = agent.discover(args.query, limit=args.limit)
    finally:
        agent.close()

    if args.json:
        print(json.dumps(matches, indent=2, ensure_ascii=False))
        return 0

    print(f"\n{_BOLD}Search: \"{args.query}\"{_RESET}\n")
    for i, m in enumerate(matches, 1):
        print(f"  {i}. {_BOLD}{m.get('capability_id', '?')}{_RESET}")
        print(f"     ${m.get('price_per_call_usd', 0):.2f} · {m.get('p50_latency_ms', '?')}ms")
        if m.get("source_hub_name"):
            print(f"     🌐 {m['source_hub_name']} (trust: {m.get('trust_score', '?')})")
        if m.get("source_hub") and m.get("source_hub") != "local":
            # A federated capability is invoked with this value: `invoke … --source-hub <it>`.
            print(f"     source_hub: {m['source_hub']}")
        print()
    return 0


def _cmd_invoke(args) -> int:
    parts = args.capability_ref.split("/", 1)
    product_id = parts[0]
    capability_id = parts[1] if len(parts) > 1 else parts[0]
    inp = json.loads(args.input)

    agent = AIMarketAgent(base_url=args.base_url, budget=args.budget,
                          sandbox_visitor=args.sandbox_visitor)
    try:
        result = agent.invoke_single(product_id, capability_id, inp, source_hub=args.source_hub)
    finally:
        agent.close()

    if result.get("safety_blocked"):
        print(f"[{_RED}safety{_RESET}] Blocked: {result.get('category')} — {result.get('reason')}")
        return 1
    if result.get("refused"):
        print(f"[{_RED}refused{_RESET}] {result.get('error') or result.get('reason') or 'mandate or job refusal'}",
              file=sys.stderr)
        return 1

    ok = result.get("success", False)
    mark = "✓" if ok else "✗"
    color = _GREEN if ok else _RED
    print(f"[{color}invoke{_RESET}] {capability_id} ${result.get('price_usd', 0):.2f} {mark}")
    if ok:
        # The hub answers with `output`; older providers used `result`. Printing only the
        # latter showed "{}" for a successful free-trial call.
        payload = result.get("output", result.get("result", {}))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        sandbox = result.get("sandbox")
        if isinstance(sandbox, dict) and sandbox.get("sandbox"):
            print(f"[{_DIM}trial{_RESET}] free tier: {sandbox.get('remaining', '?')} of "
                  f"{sandbox.get('max_trials', '?')} invokes left in this window", file=sys.stderr)
        if result.get("receipt"):
            print(f"[{_DIM}receipt{_RESET}] signed, verified={result.get('receipt_verified')}",
                  file=sys.stderr)
        return 0

    # Until 2.6.1 a refused call printed only the "✗" above. A stranger following the README
    # saw a bare failure for a 402 and left without learning that the free tier existed.
    status = result.get("http_status")
    err = result.get("error") or result.get("code") or (f"http {status}" if status else "not delivered")
    detail = result.get("detail") or result.get("message") or ""
    print(f"[{_RED}error{_RESET}] {err}" + (f": {detail}" if detail else ""), file=sys.stderr)
    if status in (402, 429) or err in ("payment_required", "trial_quota_exhausted"):
        print(
            f"[{_DIM}hint{_RESET}] No key, channel or trial id was sent. Free trial: "
            f"--sandbox-visitor <a stable id you choose> (or ${_SANDBOX_ENV}); a key with prepaid "
            f"credits: {args.base_url}/start. Federated capabilities need --source-hub (see `search`).",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

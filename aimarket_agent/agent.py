"""AIMarketAgent — The reference consumer for Protocol v2.

Encapsulates the full autonomous cycle:
    discovery → channel open → invoke (safety-gated) → settle → bill of materials.

Lightweight: only httpx + cryptography dependencies. No FastAPI, no database.
Designed to be pip-installed by any AI agent runtime.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import httpx

# Pay-on-Verified: verification envelope statuses that end a verdict poll.
# Anything else ("pending", a transport hiccup) keeps the poller going.
_VERIFY_TERMINAL_STATUSES = ("settled", "refunded", "skipped")


class AIMarketAgent:
    """Reference consumer for AIMarket Protocol v2.

    Usage:
        agent = AIMarketAgent(base_url="https://hub.example.com", budget=3.00)
        result = agent.run("translate spec to 5 langs + legal review")
        print(result["bill_of_materials"])
    """

    def __init__(
        self,
        base_url: str,
        budget: float = 3.0,
        timeout: float = 120.0,
        affiliate_id: str = "",
        verify_receipts: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.budget = budget
        self.timeout = timeout
        self.affiliate_id = affiliate_id
        # When True, each invoke receipt is cryptographically verified against the
        # hub's Ed25519 public key (from /.well-known). Failures are surfaced in
        # the result as receipt_verified / receipt_verify_reason, never raised.
        self.verify_receipts = verify_receipts
        self._verifier = None  # lazily built from the hub's well-known doc
        self.session = httpx.Client(timeout=timeout)
        # channel_id -> one-time debit secret, captured at open and sent on invoke.
        self._channel_secrets: dict[str, str] = {}

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _open_channel(self) -> str:
        """Try to open a payment channel; return channel_id or "" if hub has no channels plugin."""
        try:
            ch = self.session.post(
                self._url("/ai-market/v2/channel/open"),
                json={"deposit_usd": self.budget, "tx_hash": f"agent-{int(time.time())}"},
            )
            if ch.status_code == 404:
                return ""  # channels plugin not installed on this hub
            ch.raise_for_status()
            channel = ch.json().get("channel") or {}
            channel_id = channel.get("channel_id", "")
            # Capture the one-time debit secret so invoke can present it via
            # X-Payment-Channel-Secret (required by secure channels; else 402).
            secret = channel.get("channel_secret")
            if channel_id and secret:
                self._channel_secrets[channel_id] = secret
            return channel_id
        except Exception:
            return ""

    def _close_channel(self, channel_id: str) -> dict[str, Any]:
        if not channel_id:
            return {"skipped": "no channel was opened"}
        try:
            r = self.session.post(
                self._url("/ai-market/v2/channel/close"),
                json={"channel_id": channel_id, "settle_tx_hash": f"agent-settle-{channel_id}"},
            )
            r.raise_for_status()
            return r.json().get("settlement") or {}
        except Exception:
            return {"error": "settle failed"}

    def run(self, task: str, verify: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute the full autonomous cycle for *task*.

        Pass *verify* (e.g. ``{"requested": True}``) to opt every invoke into
        Pay-on-Verified settlement — it is sent as the invoke body's ``verify``
        key, with ``intent`` defaulting to the task itself. Omitted entirely
        when not requested, so older hubs see an unchanged body.

        Returns bill-of-materials dict with all receipts.
        """
        result: dict[str, Any] = {"task": task, "ok": False}

        verify_block: dict[str, Any] | None = None
        if verify:
            verify_block = dict(verify)
            verify_block.setdefault("intent", task)

        # ── Phase 1: Discovery ──────────────────────────────────
        try:
            wk = self.session.get(self._url("/.well-known/ai-market.json"))
            wk.raise_for_status()
        except Exception as exc:
            return {**result, "error": f"discovery failed: {exc}"}

        # Build a receipt verifier from the hub's advertised signing key.
        if self.verify_receipts:
            try:
                from aimarket_agent.receipts import ReceiptVerifier
                self._verifier = ReceiptVerifier.from_well_known(wk.json())
            except Exception:
                self._verifier = None

        # Hub v3 exposes capability discovery via GET /ai-market/v2/search.
        # Older drafts proposed POST /ai-market/discover with a "plan" response —
        # we fall back to v2 search and synthesise a one-step plan per match.
        plan: list[dict[str, Any]] = []
        try:
            search = self.session.get(
                self._url("/ai-market/v2/search"),
                params={
                    "intent": task,
                    "budget": str(self.budget),
                    "limit": "6",
                },
            )
            search.raise_for_status()
            matches = search.json().get("matches") or []
            for m in matches:
                plan.append({
                    "product_id": m.get("product_id", ""),
                    "capability_id": m.get("capability_id", ""),
                    "source_hub": m.get("source_hub", "local"),
                    "draft_input": {"text": task},
                    "est_price_usd": m.get("routed_price_usd") or m.get("price_per_call_usd", 0),
                })
        except Exception as exc:
            return {**result, "error": f"search failed: {exc}"}

        if not plan:
            return {**result, "plan": [], "note": "no matching capabilities"}

        # Cap plan at first match for predictable spend; multi-step DAGs are a
        # future protocol-level feature (pipelines endpoint).
        plan = plan[:1]
        result["plan"] = plan
        result["estimated_total_usd"] = sum(s["est_price_usd"] for s in plan)

        # ── Phase 2: Channel open (optional) ──────────────────
        channel_id = self._open_channel()
        result["channel_id"] = channel_id

        # ── Phase 3: Invoke each step ──────────────────────────
        results: list[dict[str, Any]] = []
        context: dict[str, Any] = {}
        total_spent = 0.0
        all_ok = True

        for step in plan:
            pid = step["product_id"]
            cid = step["capability_id"]
            source_hub = step.get("source_hub", "local")
            inp = dict(step.get("draft_input") or {})
            if context:
                inp.setdefault("context", context)

            headers: dict[str, str] = {}
            if channel_id:
                headers["X-Payment-Channel"] = channel_id
                secret = self._channel_secrets.get(channel_id)
                if secret:
                    headers["X-Payment-Channel-Secret"] = secret
            if self.affiliate_id:
                headers["X-AIMarket-Affiliate"] = self.affiliate_id

            payload: dict[str, Any] = {
                "product_id": pid,
                "capability_id": cid,
                "source_hub": source_hub,
                "input": inp,
            }
            if verify_block:
                payload["verify"] = verify_block

            try:
                r = self.session.post(
                    self._url("/ai-market/v2/invoke"),
                    json=payload,
                    headers=headers,
                )
            except Exception as exc:
                results.append({"error": str(exc), "capability_id": cid})
                all_ok = False
                break

            if r.status_code == 403:
                rejection = r.json()
                results.append({
                    "capability_id": cid,
                    "safety_blocked": True,
                    "category": rejection.get("category"),
                    "reason": rejection.get("reason"),
                })
                all_ok = False
                break

            if r.status_code == 402:
                results.append({
                    "capability_id": cid,
                    "payment_required": True,
                    "detail": r.json(),
                })
                all_ok = False
                break

            if not r.is_success:
                results.append({"error": f"HTTP {r.status_code}", "capability_id": cid})
                all_ok = False
                break

            body = r.json()
            price_val = body.get("price_usd", 0) or 0
            total_spent += price_val

            # Cryptographically verify the signed receipt against the hub key.
            if self.verify_receipts and self._verifier is not None:
                vr = self._verifier.verify(body.get("receipt"))
                body["receipt_verified"] = bool(vr)
                body["receipt_verify_reason"] = vr.reason

            # Pay-on-Verified: surface the hub's "verification" envelope at the
            # top level even when it only rides on the receipt (unsigned field).
            self._surface_verification(body)

            results.append(body)

            if body.get("success"):
                context = body.get("result") or {}
            else:
                all_ok = False
                break

        # ── Phase 4: Settle ─────────────────────────────────────
        settlement = self._close_channel(channel_id)

        # ── Phase 5: Bill of materials ──────────────────────────
        # Lazy import: the package __init__ imports this module first.
        from aimarket_agent import __version__ as agent_version

        bom: dict[str, Any] = {
            "task": task,
            "plan": plan,
            "results": results,
            "settlement": settlement,
            "channel_id": channel_id,
            "total_spent_usd": round(total_spent, 4),
            "all_ok": all_ok,
            "protocol_version": "v2",
            "agent_version": agent_version,
        }

        result["ok"] = all_ok
        result["bill_of_materials"] = bom
        result["total_spent_usd"] = round(total_spent, 4)
        return result

    def discover(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Search for capabilities without invoking."""
        try:
            r = self.session.get(
                self._url("/ai-market/v2/search"),
                params={"intent": query, "budget": str(self.budget), "limit": str(limit)},
            )
            r.raise_for_status()
            return r.json().get("matches") or []
        except Exception:
            return []

    def invoke_single(
        self,
        product_id: str,
        capability_id: str,
        input_payload: dict[str, Any],
        source_hub: str = "local",
        verify: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke a single capability directly.

        Pass *verify* (e.g. ``{"requested": True, "intent": "..."}``) to opt
        the call into Pay-on-Verified settlement — it is sent verbatim as the
        invoke body's ``verify`` key and the hub replies with a
        ``verification`` envelope (poll it with ``wait_for_verification``).
        Omitted entirely when not requested.
        """
        channel_id = self._open_channel()
        headers: dict[str, str] = {}
        if channel_id:
            headers["X-Payment-Channel"] = channel_id

        payload: dict[str, Any] = {
            "product_id": product_id,
            "capability_id": capability_id,
            "source_hub": source_hub,
            "input": input_payload,
        }
        if verify:
            payload["verify"] = dict(verify)

        try:
            r = self.session.post(
                self._url("/ai-market/v2/invoke"),
                json=payload,
                headers=headers,
            )
        finally:
            self._close_channel(channel_id)

        if r.status_code == 403:
            return {"safety_blocked": True, **r.json()}

        body = r.json()
        if self.verify_receipts and isinstance(body, dict) and body.get("receipt"):
            vr = self.verify_receipt(body.get("receipt"))
            body["receipt_verified"] = bool(vr)
            body["receipt_verify_reason"] = vr.reason
        if isinstance(body, dict):
            self._surface_verification(body)
        return body

    @staticmethod
    def _surface_verification(body: dict[str, Any]) -> None:
        """Lift the Pay-on-Verified ``verification`` envelope to the top level.

        The hub sends the envelope both in the response body and as an unsigned
        field of the receipt; if only the receipt copy is present (older hub
        builds, trimmed proxies), mirror it up so callers can always read
        ``result["verification"]``. No-op when there is no envelope.
        """
        receipt = body.get("receipt")
        if "verification" not in body and isinstance(receipt, dict) and receipt.get("verification"):
            body["verification"] = receipt["verification"]

    def get_verification(self, nonce: str) -> dict[str, Any]:
        """One-shot Pay-on-Verified verdict lookup by receipt nonce (``rcpt_…``).

        Returns the hub body — ``{success, verification, rejection_receipt?,
        receipt?, protocol_version}`` or ``{"success": false, "error":
        "verification_not_found"}`` for an unknown nonce. Transport failures
        come back as ``{"success": False, "error": ...}``, never raised.
        """
        try:
            r = self.session.get(self._url(f"/ai-market/v2/verification/{nonce}"))
            return r.json()
        except Exception as exc:
            return {"success": False, "error": f"verification lookup failed: {exc}"}

    def wait_for_verification(
        self,
        nonce: str,
        initial_backoff_s: float = 2.0,
        max_backoff_s: float = 60.0,
        max_wait_s: float = 0.0,
    ) -> dict[str, Any]:
        """Poll the verdict lookup until the verification envelope resolves.

        Backoff doubles from *initial_backoff_s* up to *max_backoff_s* between
        polls. ``max_wait_s=0`` (default) means no overall deadline — mirroring
        the hub's own no-deadline settlement policy; pass a positive value to
        bound the wait, in which case the last (possibly still-pending) body is
        returned on expiry. An unknown nonce returns immediately; transport
        errors keep retrying like a pending verdict.
        """
        deadline = time.monotonic() + max_wait_s if max_wait_s > 0 else None
        backoff = initial_backoff_s
        while True:
            body = self.get_verification(nonce)
            envelope = body.get("verification") if isinstance(body, dict) else None
            if isinstance(envelope, dict) and envelope.get("status") in _VERIFY_TERMINAL_STATUSES:
                return body
            if isinstance(body, dict) and body.get("error") == "verification_not_found":
                return body  # unknown nonce is definitive — retrying won't fix it
            if deadline is not None and time.monotonic() >= deadline:
                return body
            sleep_s = backoff
            if deadline is not None:
                sleep_s = min(sleep_s, max(deadline - time.monotonic(), 0.0))
            time.sleep(sleep_s)
            backoff = min(backoff * 2, max_backoff_s)

    def verify_receipt(self, receipt: dict[str, Any]):
        """Verify a single receipt against the hub's public key.

        Lazily fetches the hub's well-known signing key on first use. Returns a
        ``receipts.VerifyResult`` (truthy when verified).
        """
        from aimarket_agent.receipts import ReceiptVerifier, VerifyResult

        if self._verifier is None:
            try:
                wk = self.session.get(self._url("/.well-known/ai-market.json"))
                wk.raise_for_status()
                self._verifier = ReceiptVerifier.from_well_known(wk.json())
            except Exception as exc:
                return VerifyResult(False, f"well-known-fetch-failed: {exc}")
        return self._verifier.verify(receipt)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "AIMarketAgent":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

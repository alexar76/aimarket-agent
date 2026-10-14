"""A2A 1.0 client for an AIMarket hub: search, and paid invoke as a Task.

The hub's ``/a2a`` endpoint speaks the standard A2A JSON-RPC binding (aimarket-hub
docs/a2a.md). This client is the smallest thing that uses all of it the way a paying agent
must:

* **credits** — ``api_key`` goes as the ``X-API-Key`` HTTP header;
* **mandates** — the invoke body is serialized ONCE, signed with :meth:`Mandate.headers`
  for ``POST /ai-market/v2/invoke``, and sent as a raw ``application/json`` part holding
  those exact bytes. A data part would be re-serialized by nobody knows whom, and the proof
  covers the bytes;
* **x402** — :meth:`A2AClient.pay_x402` answers a ``TASK_STATE_INPUT_REQUIRED`` task with the
  a2a-x402 ``payment-submitted`` payload. The hub verifies transfers and never settles, so
  the payload must carry the ``txHash`` of the USDC transfer the caller already made;
* **nothing** — a first call on a priced capability runs on the hub's free trial.

A retried :meth:`A2AClient.invoke` with the same ``message_id`` returns the task the first
attempt created instead of paying twice — pass your own id when you intend to retry.

Receipts are verified against the key of the party that signed them, exactly as
:class:`~aimarket_agent.agent.AIMarketAgent` does; the verdict is added to the returned task
as ``receipt_verified`` / ``receipt_verify_reason`` (client-side keys, not A2A fields).
"""
from __future__ import annotations

import base64
import itertools
import json
import uuid
from typing import Any

import httpx

from aimarket_agent.mandates import Mandate
from aimarket_agent.receipts import OriginVerifiers

A2A_VERSION = "1.0"
X402_EXTENSION = "https://github.com/google-agentic-commerce/a2a-x402/blob/main/spec/v0.2"
INVOKE_PATH = "/ai-market/v2/invoke"
#: The path a mandate proof for a task READ is signed over — the hub's own view of /a2a,
#: which is "/a2a" even when the public URL carries a prefix a proxy strips (the prefix is
#: already in the mandate's hub origin).
A2A_PATH = "/a2a"

COMPLETED = "TASK_STATE_COMPLETED"
INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"
TERMINAL_STATES = frozenset({
    "TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED",
})


class A2AError(Exception):
    """A JSON-RPC error answer: ``code`` (-32001 task not found, -32602 invalid params …)
    and the A2A ``reason`` (``TASK_NOT_FOUND`` …)."""

    def __init__(self, code: int, message: str, reason: str = "", status_code: int = 200):
        super().__init__(f"{code} {reason or 'error'}: {message}")
        self.code = code
        self.reason = reason
        self.message = message
        self.status_code = status_code


def task_state(task: dict[str, Any]) -> str:
    return str(((task or {}).get("status") or {}).get("state") or "")


def status_metadata(task: dict[str, Any]) -> dict[str, Any]:
    message = ((task or {}).get("status") or {}).get("message") or {}
    metadata = message.get("metadata") if isinstance(message, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def payment_required(task: dict[str, Any]) -> dict[str, Any] | None:
    """The x402 PaymentRequired the hub quoted, when the task waits for a payment."""
    terms = status_metadata(task).get("x402.payment.required")
    return terms if isinstance(terms, dict) else None


def artifact(task: dict[str, Any], name: str) -> dict[str, Any] | None:
    for item in (task or {}).get("artifacts") or []:
        if isinstance(item, dict) and (item.get("name") == name or item.get("artifactId") == name):
            return item
    return None


def _first_part(item: dict[str, Any] | None) -> Any:
    parts = (item or {}).get("parts") or []
    if not parts:
        return None
    part = parts[0]
    return part.get("data") if "data" in part else part.get("text")


def task_result(task: dict[str, Any]) -> Any:
    """What the capability returned (the ``result`` artifact), or None."""
    return _first_part(artifact(task, "result"))


def task_receipt(task: dict[str, Any]) -> dict[str, Any] | None:
    receipt = _first_part(artifact(task, "aimarket-receipt"))
    return receipt if isinstance(receipt, dict) else None


class A2AClient:
    """Talks A2A 1.0 JSON-RPC to one hub.

    Usage::

        client = A2AClient("https://hub.example", api_key="amk_…")
        offers = client.search("weather at a sensor")["matches"]
        task = client.invoke(offers[0]["product_id"], offers[0]["capability_id"],
                             {"device_id": "om-wx-01"}, source_hub=offers[0]["source_hub"],
                             max_price_usd=0.01)
        if task_state(task) == "TASK_STATE_COMPLETED":
            print(task_result(task), task["receipt_verified"])
    """

    def __init__(self, base_url: str, *, api_key: str = "", mandate: Mandate | None = None,
                 timeout: float = 360.0, verify_receipts: bool = True):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.mandate = mandate
        self.verify_receipts = verify_receipts
        # 360 s: a Pay-on-Verified invoke may be held by the hub for up to 300 s, and a client
        # that gives up earlier and retries under a NEW message id pays twice.
        self.session = httpx.Client(timeout=timeout)
        self._verifiers = OriginVerifiers(self.base_url, lambda: self.session)
        self._ids = itertools.count(1)
        # The exact invoke bytes per task, so a mandated follow-up can sign them again (each
        # proof is single-use) without the caller keeping them.
        self._raw_by_task: dict[str, bytes] = {}

    # ── transport ────────────────────────────────────────────────────────────

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}{A2A_PATH}"

    def card(self) -> dict[str, Any]:
        r = self.session.get(f"{self.base_url}/.well-known/agent-card.json")
        r.raise_for_status()
        return r.json()

    def _rpc(self, method: str, params: dict[str, Any], *, headers: dict[str, str] | None = None,
             authenticate_read: bool = False) -> dict[str, Any]:
        request = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        raw = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        sent = {"Content-Type": "application/json", "A2A-Version": A2A_VERSION, "A2A-Extensions": X402_EXTENSION}
        sent.update(headers or {})
        if authenticate_read:
            sent.update(self._read_credentials(raw))
        elif self.mandate is not None and method == "SendMessage":
            proof = self.mandate.headers(raw, method="POST", path=A2A_PATH)
            sent["X-AIMarket-Mandate"] = self.mandate.digest
            sent["X-AIMarket-A2A-Proof"] = proof["X-AIMarket-Mandate-Proof"]
        r = self.session.post(self.endpoint, content=raw, headers=sent)
        try:
            body = r.json()
        except ValueError as exc:
            raise A2AError(-32603, f"the hub answered HTTP {r.status_code} without JSON", "INVALID_RESPONSE",
                           r.status_code) from exc
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            data = error.get("data") or [{}]
            reason = str((data[0] if isinstance(data, list) and data else {}).get("reason") or "")
            raise A2AError(int(error.get("code") or -32603), str(error.get("message") or ""), reason, r.status_code)
        if not isinstance(body, dict) or not isinstance(body.get("result"), dict):
            raise A2AError(-32603, "the hub answered without a result", "INVALID_RESPONSE", r.status_code)
        return body["result"]

    def _read_credentials(self, raw: bytes) -> dict[str, str]:
        """Who is asking, for GetTask/ListTasks/CancelTask: a fresh mandate proof over this
        exact JSON-RPC body, or the credit key. Anonymous tasks need neither (their id is the
        capability)."""
        if self.mandate is not None:
            return self.mandate.headers(raw, method="POST", path=A2A_PATH)
        if self.api_key:
            return {"X-API-Key": self.api_key}
        return {}

    def _pay_headers(self, raw_invoke: bytes | None) -> dict[str, str]:
        if self.mandate is not None and raw_invoke is not None:
            return self.mandate.headers(raw_invoke, method="POST", path=INVOKE_PATH)
        if self.mandate is not None:
            # Binds a follow-up to the mandate's task without paying (e.g. a payment rejection).
            return {"X-AIMarket-Mandate": self.mandate.digest}
        if self.api_key:
            return {"X-API-Key": self.api_key}
        return {}

    # ── skills ───────────────────────────────────────────────────────────────

    def search(self, intent: str, *, limit: int = 10, budget: float | None = None,
               max_latency_ms: int | None = None, min_trust: float | None = None,
               hub: str | None = None, category: str | None = None) -> dict[str, Any]:
        """``marketplace-search``: the hub's structured search result (``matches`` …)."""
        options: dict[str, Any] = {"intent": intent, "limit": limit}
        for key, value in (("budget", budget), ("maxLatencyMs", max_latency_ms), ("minTrust", min_trust),
                           ("hub", hub), ("category", category)):
            if value is not None:
                options[key] = value
        message = {"messageId": str(uuid.uuid4()), "role": "ROLE_USER",
                   "parts": [{"data": options, "mediaType": "application/json"}]}
        result = self._rpc("SendMessage", {"message": message})
        for part in (result.get("message") or {}).get("parts") or []:
            if isinstance(part, dict) and isinstance(part.get("data"), dict):
                return part["data"]
        return {"matches": []}

    def invoke(self, product_id: str, capability_id: str, input_payload: dict[str, Any] | None = None, *,
               source_hub: str = "local", max_price_usd: float | None = None,
               verify: dict[str, Any] | None = None, subcontract: dict[str, Any] | None = None,
               context_id: str | None = None, message_id: str | None = None) -> dict[str, Any]:
        """``marketplace-invoke``: run one capability; returns the A2A Task.

        COMPLETED carries ``result``, ``aimarket-receipt`` and ``provenance`` artifacts;
        INPUT_REQUIRED with :func:`payment_required` terms waits for :meth:`pay_x402`;
        AUTH_REQUIRED waits for credentials (:meth:`resume`). Pass the same ``message_id``
        to retry safely.
        """
        body: dict[str, Any] = {"product_id": product_id, "capability_id": capability_id,
                                "source_hub": source_hub, "input": dict(input_payload or {})}
        if max_price_usd is not None:
            body["max_price_usd"] = max_price_usd
        if verify:
            body["verify"] = dict(verify)
        if subcontract:
            body["subcontract"] = dict(subcontract)
        # Serialized once: these exact bytes are what a mandate proof signs.
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        message: dict[str, Any] = {
            "messageId": message_id or str(uuid.uuid4()),
            "role": "ROLE_USER",
            "parts": [{"text": f"invoke {capability_id}", "mediaType": "text/plain"}, self._invoke_part(body, raw)],
        }
        message["contextId"] = context_id or message["messageId"]
        task = self._rpc("SendMessage", {"message": message}, headers=self._pay_headers(raw))["task"]
        self._raw_by_task[task["id"]] = raw
        return self._checked(task, source_hub)

    def _invoke_part(self, body: dict[str, Any], raw: bytes) -> dict[str, Any]:
        if self.mandate is not None:
            return {"raw": base64.b64encode(raw).decode("ascii"), "mediaType": "application/json"}
        return {"data": {"invoke": body}, "mediaType": "application/json"}

    def pay_x402(self, task: dict[str, Any], payload: dict[str, Any] | str, *,
                 message_id: str | None = None) -> dict[str, Any]:
        """Answer a payment-required task with an a2a-x402 ``payment-submitted`` payload.

        ``payload`` is the x402 PaymentPayload (with the settle ``txHash`` in it — this hub
        verifies and never settles) or a bare transaction hash. The invoice nonce the task was
        quoted is used unless the payload names its own.
        """
        message = self._follow_up_message(task, message_id, "x402 payment submitted", {
            "x402.payment.status": "payment-submitted",
            "x402.payment.payload": payload,
        })
        # The account key, when there is one, only proves the task is ours: the hub charges a
        # verified on-chain payment in full and reserves nothing else on top of it.
        headers = {"X-API-Key": self.api_key} if self.api_key and self.mandate is None else {}
        answer = self._rpc("SendMessage", {"message": message}, headers=headers)["task"]
        return self._checked(answer, self._source_hub(task))

    def reject_payment(self, task: dict[str, Any], *, message_id: str | None = None) -> dict[str, Any]:
        """Decline the quoted price (a2a-x402 ``payment-rejected``): the task is CANCELED."""
        message = self._follow_up_message(task, message_id, "payment declined",
                                          {"x402.payment.status": "payment-rejected"})
        return self._rpc("SendMessage", {"message": message}, headers=self._pay_headers(None))["task"]

    def resume(self, task: dict[str, Any], *, input_payload: dict[str, Any] | None = None,
               message_id: str | None = None) -> dict[str, Any]:
        """Run a waiting task again — after fixing credentials (AUTH_REQUIRED) or with the
        complete input (INPUT_REQUIRED for missing fields). Same capability, fresh payment."""
        raw = self._raw_by_task.get(task["id"])
        part: dict[str, Any] | None = None
        if input_payload is not None:
            if raw is None:
                raise ValueError("this client did not start the task; pass the full invoke with invoke()")
            body = json.loads(raw)
            body["input"] = dict(input_payload)
            raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            part = self._invoke_part(body, raw)
        elif self.mandate is not None and raw is None:
            raise ValueError("a mandated follow-up re-signs the exact invoke bytes; this client does not have them")
        message = self._follow_up_message(task, message_id, "retry", None)
        if part is not None:
            message["parts"].append(part)
            self._raw_by_task[task["id"]] = raw  # type: ignore[assignment]
        answer = self._rpc("SendMessage", {"message": message}, headers=self._pay_headers(raw))["task"]
        return self._checked(answer, self._source_hub(task))

    def _follow_up_message(self, task: dict[str, Any], message_id: str | None, text: str,
                           metadata: dict[str, Any] | None) -> dict[str, Any]:
        message: dict[str, Any] = {
            "messageId": message_id or str(uuid.uuid4()),
            "taskId": task["id"],
            "contextId": task["contextId"],
            "role": "ROLE_USER",
            "parts": [{"text": text, "mediaType": "text/plain"}],
        }
        if metadata:
            message["metadata"] = metadata
        return message

    # ── task management ──────────────────────────────────────────────────────

    def get_task(self, task_id: str, *, history_length: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"id": task_id}
        if history_length is not None:
            params["historyLength"] = history_length
        task = self._rpc("GetTask", params, authenticate_read=True)
        return self._checked(task, self._source_hub(task))

    def list_tasks(self, *, context_id: str | None = None, status: str | None = None,
                   page_size: int | None = None, page_token: str | None = None,
                   include_artifacts: bool = False) -> dict[str, Any]:
        """This principal's tasks (an API key's account, or the mandate). Anonymous: none."""
        params: dict[str, Any] = {"includeArtifacts": include_artifacts}
        for key, value in (("contextId", context_id), ("status", status), ("pageSize", page_size),
                           ("pageToken", page_token)):
            if value is not None:
                params[key] = value
        return self._rpc("ListTasks", params, authenticate_read=True)

    def cancel(self, task_id: str) -> dict[str, Any]:
        """Cancel a task waiting for payment or credentials (-32002 otherwise)."""
        return self._rpc("CancelTask", {"id": task_id}, authenticate_read=True)

    # ── receipts ─────────────────────────────────────────────────────────────

    @staticmethod
    def _source_hub(task: dict[str, Any]) -> str:
        item = artifact(task, "aimarket-receipt")
        if item is not None:
            return str((item.get("metadata") or {}).get("sourceHub") or "local")
        capability = ((task.get("metadata") or {}).get("aimarket") or {}).get("capability") or {}
        return str(capability.get("source_hub") or "local")

    def _checked(self, task: dict[str, Any], source_hub: str) -> dict[str, Any]:
        if not self.verify_receipts or task_state(task) != COMPLETED:
            return task
        receipt = task_receipt(task)
        if receipt is None:
            task["receipt_verified"] = False
            task["receipt_verify_reason"] = "no-receipt"
            return task
        verdict = self._verifiers.verify(receipt, source_hub=source_hub)
        task["receipt_verified"] = bool(verdict)
        task["receipt_verify_reason"] = verdict.reason
        return task

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "A2AClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

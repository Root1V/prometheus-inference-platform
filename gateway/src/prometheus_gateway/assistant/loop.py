"""The assistant's tool loop — PRM-272.

**Server-side, and that is not a style choice.** The operator decided the
assistant holds its own principal with its own scopes rather than inheriting
the signed-in admin's, so that the audit trail can tell the two apart. A
principal has a `client_secret`, and a secret cannot live in a browser — so the
loop runs here, the panel only sends messages and renders what comes back.

The credential follows the one this gateway already uses for the manager
(`admin/client.py`): id and secret from the environment, a token minted on
demand and renewed before it expires. Nothing new is invented for it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..telemetry import get_logger
from . import tools

logger = get_logger(__name__)

_TOKEN_RENEW_BEFORE_S = 60.0
_REQUEST_TIMEOUT_S = 180.0

#: How many times the model may call tools before it has to answer.
#:
#: A cap rather than a trust: a loop that can run forever will, on the day a
#: tool returns something the model keeps re-reading. Four covers the deepest
#: real path here — list_instances to find the id, then tail its log, then
#: answer — with one spare.
MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = """You are the operations assistant for Prometheus, a self-hosted LLM \
inference platform. You help an administrator understand and operate it.

Use the tools to answer from what the platform actually reports. Never guess a \
number, a state or a name — if a tool did not return it, say you do not have it.

Be brief. An operator is reading this beside the dashboard, not instead of it. \
Prefer concrete values over description: say which node, which model, how long \
ago. When something is wrong, say what you would check next.

Never name your tools to the operator — they cannot call them. Offer what you \
can do instead: "I can read that instance's log" rather than a function name.

You can only read. If the operator asks you to change, restart, stop or delete \
anything, tell them you cannot do that yet and point them at the page where \
they can."""


@dataclass
class AssistantReply:
    """What the loop produces. `rounds` and `elapsed_s` are returned rather than
    only logged, because the first question about this feature on this hardware
    is whether it is fast enough to use."""

    content: str
    tool_calls_made: list[str] = field(default_factory=list)
    rounds: int = 0
    elapsed_s: float = 0.0


class Assistant:
    """Runs one conversation to an answer, calling tools on the way."""

    def __init__(
        self,
        *,
        client_id: str | None,
        client_secret: str | None,
        auth_token_url: str | None,
        base_url: str,
        model: str,
        auth_tls_verify: bool = True,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._auth_token_url = auth_token_url
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._auth_tls_verify = auth_tls_verify
        self._token: str | None = None
        self._token_expires_at = 0.0

    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret and self._auth_token_url)

    def missing(self) -> list[str]:
        """What is absent, by name — the shape `NodeHeartbeat.missing()` uses.

        A feature that silently does nothing because one environment variable is
        unset is the thing this names.
        """
        absent = []
        if not self._client_id:
            absent.append("ASSISTANT_CLIENT_ID")
        if not self._client_secret:
            absent.append("ASSISTANT_CLIENT_SECRET")
        if not self._auth_token_url:
            absent.append("AUTH_SERVICE_TOKEN_URL")
        return absent

    async def _bearer(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._token_expires_at - _TOKEN_RENEW_BEFORE_S:
            return self._token
        resp = await client.post(
            self._auth_token_url,  # type: ignore[arg-type]
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                # Exactly what it needs and no more — `admin:fleet:read` and
                # not `admin:read`, which is the parent of all five read scopes
                # and would hand a leaked secret every principal's scopes and
                # emails and every client's billing alongside the fleet. The
                # four tools read nodes, instances, activity and instance logs;
                # all four live in the fleet family (PRM-274).
                #
                # `model:<slug>` is not optional: this gateway authorises
                # inference per model, so a token with `inference:read` alone is
                # refused 403 by the assistant's own model — verified against
                # the running stack rather than assumed. It is built from
                # `assistant_model` so pointing the assistant at a different
                # model cannot leave the scope naming the old one.
                "scope": f"admin:fleet:read inference:read model:{self._model}",
            },
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = str(body["access_token"])
        self._token_expires_at = time.time() + int(body.get("expires_in", 300))
        logger.info("assistant.token_renewed")
        return self._token

    async def _run_tool(
        self, client: httpx.AsyncClient, token: str, name: str, arguments: str
    ) -> str:
        """Execute one tool call and return what the model should see.

        A failure comes back as a tool result, never as an exception: the model
        asking for a log that does not exist is an ordinary turn in the
        conversation, and crashing the request would tell the operator nothing.
        """
        handler = tools.HANDLERS.get(name)
        if handler is None:
            return json.dumps({"error": f"no such tool: {name}"})
        try:
            kwargs = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            return json.dumps({"error": "arguments were not valid JSON"})
        try:
            result = await handler(client, self._base_url, token, **kwargs)
        except httpx.HTTPStatusError as exc:
            return json.dumps(
                {
                    "error": f"{exc.response.status_code} from {name}",
                    "detail": exc.response.text[:200],
                }
            )
        except Exception as exc:  # noqa: BLE001 — the model is the error handler here
            return json.dumps({"error": f"{name} failed: {exc}"})
        return json.dumps(result, default=str)

    async def ask(self, messages: list[dict[str, Any]]) -> AssistantReply:
        started = time.monotonic()
        convo: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
        called: list[str] = []

        async with httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT_S, verify=self._auth_tls_verify
        ) as client:
            token = await self._bearer(client)

            for round_no in range(1, MAX_TOOL_ROUNDS + 2):
                # The last round is answered without tools, so the model cannot
                # spend the cap and leave the operator with nothing.
                offer_tools = round_no <= MAX_TOOL_ROUNDS
                payload: dict[str, Any] = {"model": self._model, "messages": convo}
                if offer_tools:
                    payload["tools"] = tools.DEFINITIONS

                resp = await client.post(
                    f"{self._base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                )
                resp.raise_for_status()
                message = resp.json()["choices"][0]["message"]
                convo.append(message)

                requested = message.get("tool_calls") or []
                if not requested:
                    return AssistantReply(
                        content=message.get("content") or "",
                        tool_calls_made=called,
                        rounds=round_no,
                        elapsed_s=round(time.monotonic() - started, 2),
                    )

                for call in requested:
                    fn = call.get("function", {})
                    name = fn.get("name", "")
                    called.append(name)
                    logger.info("assistant.tool_call", extra={"tool": name})
                    convo.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": await self._run_tool(
                                client, token, name, fn.get("arguments", "")
                            ),
                        }
                    )

        return AssistantReply(
            content="I ran out of steps before I could answer that. Try asking for one thing.",
            tool_calls_made=called,
            rounds=MAX_TOOL_ROUNDS + 1,
            elapsed_s=round(time.monotonic() - started, 2),
        )

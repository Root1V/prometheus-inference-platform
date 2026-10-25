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

#: The read families its tools touch: fleet (nodes, instances, models, activity,
#: overview, logs), policy (limits), billing (spend, prices, tiers) and audit.
_READ_SCOPES = "admin:fleet:read admin:policy:read admin:billing:read admin:audit:read"

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
they can.

Always answer in Spanish, whatever language the question or the tool results \
are in. Keep identifiers — node names, model ids, scopes — exactly as written."""

# The dashboard's pages, so the assistant knows where the operator is standing
# and where to send them. Keyed by route: the panel sends only the route, and
# only text from this table ever reaches the system prompt. A route it does not
# know is dropped, never quoted — otherwise the browser could write
# instructions into the prompt through a field meant to say which page is open.
#
# The third field is what its tools *cannot* see on that page, or None when
# they see all of it. Without it the assistant would answer from nothing about
# data it cannot read. A sentence and not a yes/no flag, because Users was
# neither: marked unreadable, the assistant told an operator it could not see
# client names — which it can, through every tool that names a client — when
# what it cannot see is their emails and scopes.
_ALL_OF_IT: None = None
PAGES: dict[str, tuple[str, str, str | None]] = {
    "/": (
        "Overview",
        "today's requests, tokens and cost against yesterday, what needs attention, a 14-day trend",
        _ALL_OF_IT,
    ),
    "/instances": (
        "Instances",
        "every model instance on every node, its state, and start/stop/restart",
        _ALL_OF_IT,
    ),
    "/models": (
        "Models",
        "the model catalog per node and downloading models from Hugging Face",
        _ALL_OF_IT,
    ),
    "/nodes": (
        "Nodes",
        "registered nodes, whether they answer, their hardware and inference engines",
        _ALL_OF_IT,
    ),
    "/playground": (
        "Playground",
        "trying a model out by chatting with it",
        "the conversation the operator is having there",
    ),
    "/usage": ("Usage", "token usage per client", _ALL_OF_IT),
    "/billing": (
        "Billing",
        "prices, currency rates, spend caps and the usage export",
        _ALL_OF_IT,
    ),
    "/limits": (
        "Limits",
        "rate-limit tiers, which ceiling is refusing right now, the circuit breaker",
        _ALL_OF_IT,
    ),
    "/activity": (
        "Activity",
        "who is calling the gateway now and recently, and their end users",
        _ALL_OF_IT,
    ),
    # Names it has, through every tool that names a client. The rest is the
    # credential inventory, kept out of reach on purpose (PRM-274).
    "/users": (
        "Users",
        "clients and their credentials and scopes",
        "clients' emails, scopes, roles or secrets — you do see every client's name, "
        "id and whether it is active, so answer questions about who the clients are "
        "by listing them",
    ),
}


def system_prompt(page: str | None = None) -> str:
    """The fixed prompt, the page map, and where the operator is right now."""
    lines = [SYSTEM_PROMPT, "", "The dashboard's pages:"]
    lines += [f"- {name} ({path}): {what}" for path, (name, what, _) in PAGES.items()]
    lines += [
        "",
        'Clients appear in tool results as "Name (id)". Call them by name; give the '
        "id only when two share a name or the operator asks for it.",
    ]
    if page in PAGES:
        name, what, blind_to = PAGES[page]
        lines += [
            "",
            f"The operator is on the {name} page ({page}) right now: {what}. "
            'When they say "here" or "this page", that is what they mean.',
        ]
        if blind_to:
            lines.append(
                f"On this page your tools cannot see {blind_to}. If asked about that, "
                "say so plainly instead of guessing, and say what you can read."
            )
    return "\n".join(lines)


class AssistantCredentialError(Exception):
    """auth-service refused the assistant's own credential.

    Not an outage, and not a node: a grant a human admin fixes on the Users
    page. It surfaced as "502 — the manager node returned an error … 400" the
    first time the assistant asked for a scope its credential did not hold,
    which named the wrong component and gave no way forward. Every change to
    what the assistant reads goes through this, so it says which scopes.
    """


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
                # Exactly what its tools read and no more: four of the five read
                # families (PRM-274), and never `admin:read`, their parent.
                # What is left out is `admin:principals:read` — every
                # principal's email and scopes. It was granted to give the
                # assistant client *names*, and names come from the gateway
                # instead (admin/names.py), so the credential inventory stays
                # out of reach of a secret that could leak.
                #
                # `model:<slug>` is not optional: this gateway authorises
                # inference per model, so a token with `inference:read` alone is
                # refused 403 by the assistant's own model — verified against
                # the running stack rather than assumed. It is built from
                # `assistant_model` so pointing the assistant at a different
                # model cannot leave the scope naming the old one.
                "scope": f"{_READ_SCOPES} inference:read model:{self._model}",
            },
        )
        # 400 is a scope the credential does not hold, 401 a wrong or revoked
        # secret. auth-service names which in `error_description`.
        if resp.status_code in (400, 401):
            try:
                problem = resp.json()
            except ValueError:
                problem = {}
            raise AssistantCredentialError(
                problem.get("error_description")
                or problem.get("error")
                or f"{resp.status_code} from the token endpoint"
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

    async def ask(self, messages: list[dict[str, Any]], page: str | None = None) -> AssistantReply:
        started = time.monotonic()
        convo: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(page)},
            *messages,
        ]
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

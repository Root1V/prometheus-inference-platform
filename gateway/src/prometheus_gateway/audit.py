"""The audit log — who changed what, and whether it worked. PRM-157.

Implements: docs/roadmap.md — PRM-157.

## Two destinations, and only one of them is the record

The row in the gateway's own database is the record of truth. Argus gets a copy,
for search, correlation and alerting.

Not the other way round, and the reason is not a preference. An observability
pipeline is lossy **by design** — sampled at the collector, retained for weeks,
exported best-effort — and a trail that may drop an event is not an audit trail.
Kubernetes draws the same line: the API server writes audit events to its own
backend and whatever scrapes the cluster is a consumer of them.

So this module writes the row first and emits second. If the emission fails the
record survives; if the row fails, that is logged as loudly as this codebase knows
how, because a gap in the trail is the one thing an operator must not learn about
later.

## What reaches Argus, and the one thing still ours

`argus_semconv` gives `argus.event`, `argus.outcome` (with its five values),
`argus.app`, `argus.component.role`, `argus.feature` and `argus.tenant`. Those are
used as they stand — the outcome word in the row and the outcome word Argus
receives are the same string, taken from their tuple, so the two can never drift
into different vocabularies.

The actor was the gap, and it is closed (PRM-162). We proposed four
`prometheus.audit.*` names in channel entry P-31 rather than inventing `argus.*`
ones in someone else's namespace, and A-32 answered that **three already existed**:
`user.id`, `user.email`, and the action, which is `http.request.method` plus
`http.route` on the server span — already emitted, so ours was deleted rather than
renamed. Only the actor's *kind* needed naming and that one is theirs,
`argus.actor.kind`, because nothing standard says it.

`prometheus.audit.target` is what is left: neither vocabulary has a name for *what
a change was made to*. It stays here and is raised in P-32, which is the shape A-32
asked for — a second round rather than four invented names across two namespaces.

## Two payloads, because they reach different places

The span event hangs off the **server span** and leaves to it everything that span
already carries: the route template, the status code, the client address. The log
line carries those itself, because nothing joins a log line to a span — an
attribute the event can inherit is one the log line has to state.

## What is never recorded

Request bodies. `/admin/api/auth/login` carries a password and a secret rotation
returns a secret, so a log that kept bodies would become the largest credential
store in the platform. The action and the path parameters say what was touched,
and nothing here needs the payload to be useful.

`actor_email` is the one identifier read from a body, by the login handler alone,
because "who tried to log in" is the most audited fact in any system and an email
is an identifier rather than a secret.
"""

from __future__ import annotations

import json
from typing import Any

from argus_semconv import attributes as argus
from fastapi import Request

from . import db
from opentelemetry import trace

from .telemetry import get_logger

logger = get_logger(__name__)

# Their word, from their tuple. Taken rather than retyped so a change on their
# side is an import error here instead of two systems quietly disagreeing about
# what "ok" means.
_OK, _ERROR = argus.ARGUS_OUTCOME_VALUES[0], argus.ARGUS_OUTCOME_VALUES[1]

# PRM-162: the vocabulary is theirs now. P-31 proposed four `prometheus.audit.*`
# names and A-32 answered that three already exist in the standard and one of them
# we were already emitting — so only the actor's *kind* needed a name, and that one
# is `argus.*` because nothing standard says it.
#
# Their reasoning is the one this codebase applies to itself: inventing a name for
# something that already has a stable one is RM-07's mistake in reverse.
ATTR_ACTOR_KIND = argus.ARGUS_ACTOR_KIND
KIND_USER, KIND_SERVICE, KIND_UNKNOWN = argus.ARGUS_ACTOR_KIND_VALUES

# `user.id` and `user.email` from OTel's registry. Written out rather than imported
# because the constants live under `opentelemetry.semconv._incubating`, and an
# import path with an underscore in it is one that moves — the names themselves do
# not. `user.id` carries whichever subject acted, a person or a machine credential,
# and `argus.actor.kind` is what makes that readable: `svc-7` does not mean the
# same thing both ways, which was A-32's own argument for the attribute.
ATTR_ACTOR_ID = "user.id"
ATTR_ACTOR_EMAIL = "user.email"

# `target` is the one with no standard name and no `argus.*` one either: nothing in
# either vocabulary says *what a change was made to*. Kept under `prometheus.` and
# raised with them in P-32, which is what A-32 asked for — «mejor una segunda ronda
# que cuatro nombres inventados en dos namespaces».
ATTR_TARGET = "prometheus.audit.target"

# These three are on the server span already, measured: `http.request.method` +
# `http.route` (the route template A-32 pointed out we emit), and
# `http.response.status_code`, and `client.address`. The span event hangs off that
# span, so repeating them there would be the same fact twice.
#
# **The log line is a different matter and keeps them.** It travels on its own —
# nothing joins it to a span — so an attribute the event can inherit is one the log
# line has to carry. Same event, two payloads, and the difference is not sloppiness:
# it is what each channel can and cannot reach.
LOG_ATTR_ACTION = "action"
LOG_ATTR_STATUS = "status_code"
LOG_ATTR_SOURCE_IP = "source_ip"

# Everything under /admin/api is administrative. A read changes nothing and is not
# audited: recording every dashboard poll would bury the writes in noise, which is
# how audit logs stop being read.
_AUDITED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# The login route records itself, from inside the handler that already parsed the
# body and therefore knows who tried. Excluded here so it is not recorded twice.
_SELF_AUDITING_PATHS = frozenset({"/admin/api/auth/login"})


def should_audit(method: str, path: str) -> bool:
    """Is this request an administrative change worth a row? — PRM-157."""
    return (
        method.upper() in _AUDITED_METHODS
        and path.startswith("/admin/api/")
        and path not in _SELF_AUDITING_PATHS
    )


def outcome_for(status_code: int) -> str:
    """One of argus_semconv's outcome values.

    A refusal is `error`: an action that was attempted and denied is exactly what
    an auditor is looking for, and calling a 403 "ok" because the system behaved
    correctly would hide the attempt.
    """
    return _OK if 200 <= status_code < 400 else _ERROR


def actor_kind(actor_user_id: str | None, actor_client_id: str | None) -> str:
    """One of `argus.actor.kind`'s three values — PRM-162.

    A person when the dashboard authenticated one, a machine credential when only a
    client id is present, and `unknown` when neither is — which A-32 asked for as a
    **legitimate value and not filler**: in an audit record "not stated" is a fact,
    and it has to be distinguishable from "nobody set this". That is the three-state
    disease this codebase has brought them twice (RM-98, PRM-133), avoided at the
    start this time instead of repaired afterwards.
    """
    if actor_user_id:
        return KIND_USER
    if actor_client_id:
        return KIND_SERVICE
    return KIND_UNKNOWN


def action_for(request: Request) -> tuple[str, str | None]:
    """`METHOD /route/{template}` and the path parameters as JSON — PRM-157.

    The template, never the resolved path: `POST /admin/api/nodes/{node_id}/check`
    groups, where the resolved path makes every id its own action and nothing can
    be counted or alerted on.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None) or request.url.path
    params: dict[str, Any] = dict(request.scope.get("path_params") or {})
    return f"{request.method.upper()} {template}", (json.dumps(params) if params else None)


async def record(
    *,
    action: str,
    outcome: str,
    status_code: int,
    actor_client_id: str | None = None,
    actor_user_id: str | None = None,
    actor_email: str | None = None,
    target: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    source_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Write the row, then tell Argus — PRM-157, in that order.

    The order is the design: the record must exist before anything best-effort is
    attempted with it.
    """
    await db.record_audit_event(
        action=action,
        outcome=outcome,
        status_code=status_code,
        actor_client_id=actor_client_id,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        target=target,
        request_id=request_id,
        trace_id=trace_id,
        source_ip=source_ip,
        user_agent=user_agent,
    )

    attributes: dict[str, Any] = {
        argus.ARGUS_EVENT: "audit.admin_action",
        argus.ARGUS_OUTCOME: outcome,
        argus.ARGUS_COMPONENT_ROLE: "api",
        argus.ARGUS_FEATURE: "admin",
        ATTR_ACTOR_KIND: actor_kind(actor_user_id, actor_client_id),
    }
    # `argus.tenant` is theirs and fits: the client a change was made under is the
    # tenant boundary everything else in this platform is scoped by. It is also why
    # there is no separate attribute for the client id — that would be the same
    # value under two names, and for a machine credential a third, since `user.id`
    # below is the client id in that case.
    if actor_client_id:
        attributes[argus.ARGUS_TENANT] = actor_client_id
    # One subject, whoever it was. A person when the dashboard authenticated one,
    # the machine credential otherwise; `argus.actor.kind` above says which.
    if subject := (actor_user_id or actor_client_id):
        attributes[ATTR_ACTOR_ID] = subject
    if actor_email:
        attributes[ATTR_ACTOR_EMAIL] = actor_email
    if target:
        attributes[ATTR_TARGET] = target

    # PRM-162: the event goes on the span that is **already current**, which is the
    # SERVER span the ASGI instrumentation opened for this request.
    #
    # This is what the previous version said it did and did not: it called
    # `start_span`, which creates a second span per admin action — exactly the
    # doubling the comment claimed to avoid — and hung the event off that. The
    # event then sat on a span carrying no attributes at all, so A-32's
    # "you already emit the route template" was true of the request and false of
    # the event. Measured before changing it, and the fix is what makes deleting
    # our action, status and source-ip attributes correct rather than lossy.
    try:
        trace.get_current_span().add_event("audit.admin_action", attributes=attributes)
    except Exception as exc:  # noqa: BLE001 — telemetry never fails an action
        logger.warning("audit.span_failed", action=action, error=str(exc))

    # And the structured log line, which is what Argus collects and searches. The
    # row is already written by now, so this being dropped costs discoverability
    # and not the record.
    #
    # It carries the three the event leaves to the span, because a log line reaches
    # no span: nothing joins it, so what it does not say is not written down
    # anywhere its reader can get to.
    logger.info(
        "audit.admin_action",
        **{k.replace(".", "_"): v for k, v in attributes.items()},
        **{LOG_ATTR_ACTION: action, LOG_ATTR_STATUS: status_code},
        **({LOG_ATTR_SOURCE_IP: source_ip} if source_ip else {}),
    )


async def record_request(request: Request, status_code: int) -> None:
    """Audit one completed admin request — PRM-157.

    Called from one middleware rather than from each mutating handler. There are
    more than twenty of those, and a rule that has to be remembered at twenty call
    sites is the defect this codebase has met repeatedly: PRM-142 was five
    handlers forgetting to check a status, PRM-131 was five forgetting a metric
    input. One door, one rule.
    """
    claims = getattr(getattr(request, "state", None), "claims", None)
    action, target = action_for(request)
    await record(
        action=action,
        outcome=outcome_for(status_code),
        status_code=status_code,
        actor_client_id=getattr(claims, "client_id", None),
        actor_user_id=getattr(claims, "user_id", None),
        target=target,
        request_id=getattr(getattr(request, "state", None), "request_id", None),
        trace_id=getattr(getattr(request, "state", None), "trace_id", None),
        source_ip=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
    )

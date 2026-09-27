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

## What reaches Argus, and the half their conventions do not cover yet

`argus_semconv` 1.0.0 gives `argus.event`, `argus.outcome` (with its five values),
`argus.app`, `argus.component.role`, `argus.feature` and `argus.tenant`. Those are
used as they stand — the outcome word in the row and the outcome word Argus
receives are the same string, taken from their tuple, so the two can never drift
into different vocabularies.

What their conventions have no attribute for is **the actor**, which for an audit
event is the entire point. Rather than inventing `argus.*` names for another
team's namespace, the actor and the action go out under `prometheus.audit.*` and
the gap is raised with them in the channel. That is the PRM-144 lesson applied to
ourselves: a private vocabulary standing in for a missing contract is exactly what
we asked them not to make us do.

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
from .telemetry import get_logger, get_tracer

logger = get_logger(__name__)
_tracer = get_tracer("prometheus.audit")

# Their word, from their tuple. Taken rather than retyped so a change on their
# side is an import error here instead of two systems quietly disagreeing about
# what "ok" means.
_OK, _ERROR = argus.ARGUS_OUTCOME_VALUES[0], argus.ARGUS_OUTCOME_VALUES[1]

# Attribute names for the half argus_semconv does not cover. Namespaced under
# `prometheus.` deliberately: `argus.` is their vocabulary and inventing names in
# it would make this platform the source of a convention nobody agreed to.
ATTR_ACTOR_CLIENT = "prometheus.audit.actor.client_id"
ATTR_ACTOR_USER = "prometheus.audit.actor.user_id"
ATTR_ACTOR_EMAIL = "prometheus.audit.actor.email"
ATTR_ACTION = "prometheus.audit.action"
ATTR_TARGET = "prometheus.audit.target"
ATTR_STATUS = "prometheus.audit.status_code"
ATTR_SOURCE_IP = "prometheus.audit.source_ip"

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
        ATTR_ACTION: action,
        ATTR_STATUS: status_code,
    }
    # `argus.tenant` is theirs and fits: the client a change was made under is the
    # tenant boundary everything else in this platform is scoped by.
    if actor_client_id:
        attributes[argus.ARGUS_TENANT] = actor_client_id
        attributes[ATTR_ACTOR_CLIENT] = actor_client_id
    if actor_user_id:
        attributes[ATTR_ACTOR_USER] = actor_user_id
    if actor_email:
        attributes[ATTR_ACTOR_EMAIL] = actor_email
    if target:
        attributes[ATTR_TARGET] = target
    if source_ip:
        attributes[ATTR_SOURCE_IP] = source_ip

    # A span event rather than a span: the action already has a server span, and a
    # second one would double every admin request in their trace view for no
    # information. An event hangs off the span that is already there.
    try:
        span = _tracer.start_span("audit.admin_action")
        try:
            span.add_event("audit.admin_action", attributes=attributes)
        finally:
            span.end()
    except Exception as exc:  # noqa: BLE001 — telemetry never fails an action
        logger.warning("audit.span_failed", action=action, error=str(exc))

    # And the structured log line, which is what Argus collects and searches. The
    # row is already written by now, so this being dropped costs discoverability
    # and not the record.
    logger.info("audit.admin_action", **{k.replace(".", "_"): v for k, v in attributes.items()})


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

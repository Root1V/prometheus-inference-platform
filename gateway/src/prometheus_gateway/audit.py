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

`argus_semconv` gives `argus.event`, `argus.outcome` (with its own set of values —
not counted here, because this line said "five" until `1.0.0a17` made it seven),
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

# PRM-164: the target has their names now. P-32 asked and A-34 answered with the
# flat pair over the JSON blob, on an argument better than ours: inside a JSON,
# type and id become one value again — which is the route-template problem moved
# one level down. A JSON also forces a reader to know the shape per route
# (`node_id` here, `client_id` there), so querying "the object" would have to
# enumerate routes.
ATTR_TARGET_TYPE = argus.ARGUS_TARGET_TYPE
ATTR_TARGET_ID = argus.ARGUS_TARGET_ID

# PRM-175: where the object lives, proposed in P-35 and accepted in A-41 §3 on the
# argument that settled it — *"they are not two peer objects; it is an object and
# the place it lives"*. A `target2` would have claimed two things of equal rank, and
# then grouping by "the object" is ambiguous; `parent` says which is which.
#
# **Literals, and deliberately so**: `argus_semconv` ships no constant for this pair
# at any published version (checked through `1.0.0a17`), and the alternative to a
# literal is waiting for one. They are the only two attribute names in this module
# not read from the package, which is a thing to undo rather than live with — a test
# fails as soon as the package ships them, so the switch is forced rather than
# remembered.
ATTR_TARGET_PARENT_TYPE = "argus.target.parent.type"
ATTR_TARGET_PARENT_ID = "argus.target.parent.id"

# The type is closed cardinality — it exists to **group**. Declared here because a
# closed set that nothing enforces is not closed: a guard test walks every mutating
# admin route and fails when one yields a type that is not in this set, so a new
# route is a decision rather than a new value appearing in their store.
#
# Some of these are load-bearing beyond grouping: their pipeline hashes the id
# where the type names a principal, so the type is what stands between a person's
# identifier and their store (A-34 §2). Mislabelling one is the hole they had just
# closed, one level down.
#
# **Which types those are is not restated here.** It used to be — this comment said
# "`user`", then PRM-172 had to come back and add "and now `client`" — and a fact
# kept in prose next to a fact kept in code is the shape both teams have now been
# bitten by four times. `argus.ARGUS_TARGET_TYPE_PRINCIPALS` ships it as data since
# `1.0.0a14`, so the set is read from there and the tests compare against it.
TARGET_TYPES: frozenset[str] = frozenset(
    {
        "node",
        "user",
        "model",
        "instance",
        "download",
        "catalog",
        "pricing",
        "traffic_split",
        "share",
        # PRM-172: `client` says `client` again. It was emitted as `user` for a
        # while, and the reason is worth keeping because it is why the override
        # could be removed rather than forgotten.
        #
        # `client_id` is the same value under two resources —
        # `/admin/api/users/{client_id}` administers the principal,
        # `/admin/api/billing/clients/{client_id}/settings` configures its billing —
        # and Argus's pipeline hashed only `user`. Emitting what each segment said
        # would have left the same subject hashed in one row and in the clear in
        # another, and a pseudonym is worth what the least protected place that
        # subject's identifier appears is worth. So both said `user`, erring toward
        # hashing a machine client's id rather than publishing a person's.
        #
        # Raised in P-33; A-36 answered that it was a hole on their side and
        # `client` is now protected exactly like `user`. With both protected the
        # override stops buying anything and costs the distinction, so it is gone
        # and the type describes the id again.
        "client",
    }
)

# PRM-172: a `_NON_OBJECT_PARAMS` set stood here with one member, `{action}` — a
# verb in the path, so never the thing acted upon. Those two routes are now seven
# explicit verbs, so no parameter across the admin routes is anything but an
# identifier, and an empty set plus the filter that read it was code with nothing
# left to do. The guard over `TARGET_TYPES` is what will ask the question if a
# route ever puts a verb in a parameter again.

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
# PRM-164: and the correlation ids, for the same reason and one worse. They were on
# the line by accident — bound as structlog contextvars by `TraceIDMiddleware`, which
# clears them in a `finally` when the response is done. `record_request` runs *after*
# `call_next`, so every admin action except the login (which audits itself from
# inside its handler, while the binding still exists) emitted `trace_id: "none"`.
#
# Measured: the row carried `744c92ad…` and the response header carried the same id,
# while the log line for that action said `none`. The row was never wrong; what was
# lost is the correlation Argus said the copy is *for* — search, `trace_id` join, and
# alerts on patterns. So they are stated, not inherited.
LOG_ATTR_TRACE_ID = "trace_id"
LOG_ATTR_REQUEST_ID = "request_id"

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


def target_for(request: Request) -> tuple[str | None, str | None]:
    """`(argus.target.type, argus.target.id)` for what this request acted on — PRM-164.

    **The type describes the id, not the object of the action.** That is A-34's
    semantics — *«es el único que sabe si el id de al lado es una cosa o alguien»* —
    and it settles the cases where the two differ: `PATCH
    /admin/api/nodes/{node}/models/config` changes a node's model configuration, and
    the id it carries is a node, so the type is `node`. What was done is already in
    `http.route`.

    So the type comes from the **path segment the parameter belongs to**, singular
    and lowercase, which is what makes the value read like the route's resource
    without being the route. Not from the parameter's name: ours are not a reliable
    guide — `{node}` and `{node_id}` are both nodes, and `/admin/api/users/{client_id}`
    administers a principal that is very often a person. Taking `users` from the
    route rather than `client` from the parameter is what makes their hashing rule
    fire, and erring that way costs a little query convenience where the principal
    turns out to be a machine. The other direction costs an identifier in the clear.

    The **last** object parameter wins, because that is the most specific — A-34's
    rule. Where a route carries two that both matter, the outer one is `parent_for`'s
    — `argus.target.parent.*`, proposed in P-35 and accepted in A-41 §3.
    """
    return _pair_at(request, -1)


def parent_for(request: Request) -> tuple[str | None, str | None]:
    """`(argus.target.parent.type, .parent.id)` — where the target lives — PRM-175.

    The **next-outer** object parameter, derived exactly as the target is, because
    it is the same question asked one segment out. Twelve routes carry two objects
    today and all twelve are `/admin/api/nodes/{node}/<resource>/{model_id}...`, so
    in practice this says which node an instance, model or download belongs to.

    Until now that outer id reached Argus only inside the row's JSON parameter set,
    which is the route-template problem one level down: a reader had to know each
    route's shape to find it, so "every action on node X" could not be asked.

    Two obligations came with A-41 §3 accepting it, and both are tested:

    1. **The parent is classified like a target from the first event.** The type is
       taken from the route segment, never the parameter name, so a parent that is a
       principal says so and their pipeline protects it. Argus's reason is the one
       that matters: debuting the attribute without this would repeat, on a new
       field, the leak the two teams had just fixed three times.
    2. **The pair goes together or it does not go.** A `parent.id` with no
       `parent.type` is an identifier without knowing what of — and, more to the
       point, without knowing whether it needs protecting.

    Returns `(None, None)` for a single-object route, which is most of them.
    """
    return _pair_at(request, -2)


def _pair_at(request: Request, index: int) -> tuple[str | None, str | None]:
    """The `(type, id)` of the object parameter at *index* in route order.

    One derivation serving both the target and its parent. It was duplicated in the
    first draft of PRM-175 — two copies of "singularise the preceding segment",
    which is the defect this codebase keeps meeting under its own name.

    All-or-nothing, for the reason A-41 §3 gives about the parent, which is just as
    true of the target: an id whose type is unknown is an id nobody knows whether to
    protect. Both branches that used to return a bare id were unreachable anyway —
    `names` is built from `segments`, so the lookup cannot fail, and a template
    cannot begin with a parameter.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None) or request.url.path
    params: dict[str, Any] = dict(request.scope.get("path_params") or {})
    if not params:
        return None, None

    segments = template.strip("/").split("/")
    # Walk the template so the order is the route's, not the dict's.
    names = [
        seg[1:-1]
        for seg in segments
        if seg.startswith("{")
        and seg.endswith("}")
        # A `:path` converter is a catch-all, not an identifier — PRM-172 added one
        # for unknown `/admin/api` paths, and a 404 has no target to name.
        and ":path" not in seg
    ]
    names = [n for n in names if n in params]
    if len(names) < abs(index):
        return None, None
    chosen = names[index]

    try:
        at = segments.index("{" + chosen + "}")
    except ValueError:  # pragma: no cover — names come from segments
        return None, None
    resource = segments[at - 1] if at > 0 else ""
    object_type = resource.rstrip("s").replace("-", "_") if resource else ""
    if not object_type:
        return None, None
    return object_type, str(params[chosen])


async def record(
    *,
    action: str,
    outcome: str,
    status_code: int,
    actor_client_id: str | None = None,
    actor_user_id: str | None = None,
    actor_email: str | None = None,
    target: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    parent_type: str | None = None,
    parent_id: str | None = None,
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
    # PRM-164: the pair goes to Argus; the row keeps `target`, the full parameter
    # set as JSON. Not the same fact twice — the row is the record of truth and
    # holds every parameter, including the outer one a two-object route loses here.
    if target_type:
        attributes[ATTR_TARGET_TYPE] = target_type
    if target_id:
        attributes[ATTR_TARGET_ID] = target_id
    # PRM-175: and where it lives. `and` rather than two `if`s is the whole of
    # A-41 §3's second condition — half a pair is an identifier nobody knows
    # whether to protect, so it is better absent.
    if parent_type and parent_id:
        attributes[ATTR_TARGET_PARENT_TYPE] = parent_type
        attributes[ATTR_TARGET_PARENT_ID] = parent_id

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
        **({LOG_ATTR_TRACE_ID: trace_id} if trace_id else {}),
        **({LOG_ATTR_REQUEST_ID: request_id} if request_id else {}),
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
    target_type, target_id = target_for(request)
    parent_type, parent_id = parent_for(request)
    await record(
        action=action,
        outcome=outcome_for(status_code),
        status_code=status_code,
        actor_client_id=getattr(claims, "client_id", None),
        actor_user_id=getattr(claims, "user_id", None),
        target=target,
        target_type=target_type,
        target_id=target_id,
        parent_type=parent_type,
        parent_id=parent_id,
        request_id=getattr(getattr(request, "state", None), "request_id", None),
        trace_id=getattr(getattr(request, "state", None), "trace_id", None),
        source_ip=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
    )

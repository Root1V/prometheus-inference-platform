from dataclasses import dataclass
from datetime import datetime


# PRM-274: one scope used to guard all 33 admin read endpoints, so a credential
# that needed one of them got every one — the fleet, every principal's scopes
# and emails, and per-client billing. The read surface is split by resource
# family now, and `admin:read` is kept as the parent that implies all five.
#
# That is what makes the split cost nothing to adopt: every credential issued
# before it keeps working, and the dashboard — which holds `admin:read` — needs
# no change. The flip side is that the split decays in silence if a new endpoint
# is written against `admin:read` again, which is why a test forbids it.
_SCOPE_IMPLIES: dict[str, frozenset[str]] = {
    "admin:read": frozenset(
        {
            "admin:fleet:read",
            "admin:billing:read",
            "admin:principals:read",
            "admin:policy:read",
            "admin:audit:read",
        }
    ),
}


@dataclass
class Claims:
    """Verified JWT claims propagated to downstream middleware and route handlers.

    Implements: memory/specs/002-jwt-authentication-middleware.md — AC-1
    """

    user_id: str  # JWT `sub`   — caller identity
    client_id: str  # JWT `azp`   — OAuth2 authorized party (RFC 7519)
    scope: str  # JWT `scope` — space-separated permission strings
    expires_at: datetime  # JWT `exp`
    issued_at: datetime  # JWT `iat`
    issuer: str  # JWT `iss`
    jti: str | None = None  # JWT `jti` — used for revocation lookups

    def has_scope(self, required: str) -> bool:
        """Return True if this token's scope claim permits *required*.

        Not literal membership: a scope that implies *required* satisfies it.
        Every caller asks this to decide access, never to report what a token
        carries — `self.scope` is the answer to that question.
        """
        held = self.scope.split()
        if required in held:
            return True
        return any(required in _SCOPE_IMPLIES.get(s, frozenset()) for s in held)

    def has_model_scope(self, model_id: str) -> bool:
        """RM-07: per-model access grant, additive to inference:read/inference:stream.

        Deny-by-default: a token with no `model:*` scope at all has no model
        access, even with inference:read. See docs/roadmap.md RM-07.
        """
        return self.has_scope(f"model:{model_id}")

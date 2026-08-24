from dataclasses import dataclass
from datetime import datetime


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
        """Return True if the given scope is included in this token's scope claim."""
        return required in self.scope.split()

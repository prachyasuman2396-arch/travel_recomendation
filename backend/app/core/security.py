"""Admin authentication: a static bearer token compared in constant time.

Simple by design for a single-operator deployment. If ADMIN_API_TOKEN is not
set, admin endpoints are disabled entirely rather than left open.
"""

from __future__ import annotations

import hmac

from app.core.errors import AuthenticationError, ServiceNotConfiguredError


def verify_admin_token(provided: str | None, expected: str | None) -> None:
    if not expected:
        raise ServiceNotConfiguredError("Admin endpoints are disabled: ADMIN_API_TOKEN is not set")
    if not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
        raise AuthenticationError("Invalid or missing admin token")

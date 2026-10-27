"""Admin login for the session-history endpoints (``/sessions*``).

Those endpoints expose every recorded session, so they need an admin token:
``POST /admin/login`` checks one shared username and password (``ADMIN_USERNAME``,
``ADMIN_PASSWORD``) and returns a short-lived JWT, which ``require_admin`` in
``server.py`` checks on each request.

Admin tokens are signed with the same secret as audience tokens, but carry their
own ``aud`` claim, so neither kind can pass for the other.
"""

import secrets
import time
import uuid
from typing import Any

import jwt

from app.delivery.rooms import Clock, IssuedToken, TokenError

# ``aud`` claim in admin tokens. Audience join tokens use ``JWT_AUDIENCE`` with
# the same secret, so this keeps them out of /sessions.
JWT_AUDIENCE_ADMIN = "sea-cast-admin"


def verify_credentials(
    username: str,
    password: str,
    *,
    expected_username: str | None,
    expected_password: str | None,
) -> bool:
    """Check a login against the configured admin credentials.

    Uses constant-time comparisons, and always runs both, so response timing
    reveals neither how much of a guess was right nor which field was wrong.

    Args:
        username: The submitted username.
        password: The submitted password.
        expected_username: ``ADMIN_USERNAME``, or None when unset.
        expected_password: ``ADMIN_PASSWORD``, or None when unset.

    Returns:
        Whether both match; always False when either expected value is unset, so
        a server with no admin credentials keeps the history locked.
    """
    if not expected_username or not expected_password:
        return False
    user_ok = secrets.compare_digest(username, expected_username)
    pass_ok = secrets.compare_digest(password, expected_password)
    return user_ok and pass_ok


def issue_admin_token(
    jwt_secret: str, *, ttl_secs: int, clock: Clock = time.time
) -> IssuedToken:
    """Create a short-lived admin token.

    Args:
        jwt_secret: The server's signing secret.
        ttl_secs: How long the token is valid.
        clock: The current time in epoch seconds (injectable for tests).

    Returns:
        The token and its expiry (epoch milliseconds).
    """
    iat = int(clock())
    exp = iat + ttl_secs
    claims = {
        "aud": JWT_AUDIENCE_ADMIN,
        "iat": iat,
        "exp": exp,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(claims, jwt_secret, algorithm="HS256")
    return IssuedToken(token=token, expires_at=exp * 1000)


def verify_admin_token(token: str, jwt_secret: str) -> dict[str, Any]:
    """Check an admin token's signature, audience and expiry.

    Args:
        token: The token from the ``Authorization: Bearer`` header.
        jwt_secret: The server's signing secret.

    Returns:
        The token's claims.

    Raises:
        TokenError: The token is invalid, expired, or not an admin token
            (``require_admin`` turns this into HTTP 401).
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            audience=JWT_AUDIENCE_ADMIN,
        )
    except jwt.InvalidTokenError as e:
        raise TokenError(str(e)) from e
    return claims

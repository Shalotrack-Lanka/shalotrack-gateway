"""
Authentication: verify the Cloudflare Access JWT on every session.

Cloudflare Access sits in front of the app and injects a signed JWT in the
`Cf-Access-Jwt-Assertion` header. We do NOT trust that the header is there because "the
request came through Cloudflare": we verify the signature (RS256, keys fetched from the
team's certs endpoint), the audience (this Access application), the issuer, the expiry,
and then check the email against our own allow-list. If Access is ever mis-scoped, or the
origin is reached some other way, the app still refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import jwt

from .config import Settings

LEEWAY_SECONDS = 10


class AuthError(Exception):
    """Authentication failed. `reason` is for server logs only; the UI shows a generic message."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Identity:
    email: str
    expires_at: int  # unix seconds, from the JWT `exp`


class _KeyClient(Protocol):
    def get_signing_key_from_jwt(self, token: str): ...


def make_key_client(settings: Settings) -> jwt.PyJWKClient:
    """One of these per process (cache it). It caches the key set for an hour."""
    return jwt.PyJWKClient(
        f"https://{settings.cf_team_domain}/cdn-cgi/access/certs",
        cache_jwk_set=True,
        lifespan=3600,
        timeout=5,
    )


def email_allowed(email: str, settings: Settings) -> bool:
    email = email.strip().lower()
    if email in settings.allowed_emails:
        return True
    _, _, domain = email.rpartition("@")
    return bool(domain) and domain in settings.allowed_email_domains


def verify_access_jwt(token: str | None, settings: Settings, key_client: _KeyClient) -> Identity:
    if not token:
        raise AuthError("missing Cf-Access-Jwt-Assertion header")
    try:
        signing_key = key_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],  # never "whatever the token header says"
            audience=settings.cf_aud,
            issuer=f"https://{settings.cf_team_domain}",
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"jwt rejected: {type(exc).__name__}") from None
    except Exception as exc:  # key endpoint unreachable, malformed JWKS, ...
        raise AuthError(f"key lookup failed: {type(exc).__name__}") from None

    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        # Service tokens carry no email. This console is for people.
        raise AuthError("token has no user email")
    if not email_allowed(email, settings):
        raise AuthError("email not on the allow-list")
    return Identity(email=email.strip().lower(), expires_at=int(claims["exp"]))

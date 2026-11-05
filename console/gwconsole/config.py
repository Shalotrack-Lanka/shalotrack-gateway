"""
Settings, read once from the environment. Fail CLOSED: anything missing or malformed
raises ConfigError and the app refuses to render, rather than starting half-configured.

Messages in ConfigError never contain secret values; they name the variable only.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

AUTH_CLOUDFLARE = "cloudflare_access"
AUTH_NONE = "none"

_TEAM_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}\.cloudflareaccess\.com$")
_AUD_RE = re.compile(r"^[A-Za-z0-9]{16,128}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

MIN_REFRESH_SECONDS = 5


class ConfigError(Exception):
    """Raised for any invalid or missing setting. The message is safe to show."""


@dataclass(frozen=True)
class Settings:
    gateway_url: str
    api_key: str = field(repr=False)  # never printed, even by accident
    auth_mode: str
    cf_team_domain: str = ""
    cf_aud: str = ""
    allowed_emails: frozenset[str] = frozenset()
    allowed_email_domains: frozenset[str] = frozenset()
    refresh_seconds: int = 10
    timezone: str = "Asia/Colombo"


def _csv(raw: str) -> list[str]:
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _int(env: dict, name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}")
    return value


def load_settings(env: dict | None = None) -> Settings:
    env = dict(os.environ if env is None else env)

    # --- gateway --------------------------------------------------------------
    url = env.get("GATEWAY_URL", "http://gateway.shalotrack.internal:8001").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("GATEWAY_URL must be an http(s) URL with a host")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ConfigError("GATEWAY_URL must be just scheme://host:port (no credentials, path or query)")

    api_key = env.get("COMMAND_API_KEY", "").strip()
    if not api_key:
        raise ConfigError("COMMAND_API_KEY is not set")

    # --- who may view this ----------------------------------------------------
    mode = env.get("CONSOLE_AUTH", AUTH_CLOUDFLARE).strip().lower()
    if mode not in (AUTH_CLOUDFLARE, AUTH_NONE):
        raise ConfigError(f"CONSOLE_AUTH must be '{AUTH_CLOUDFLARE}' or '{AUTH_NONE}'")

    team = env.get("CF_ACCESS_TEAM_DOMAIN", "").strip().lower()
    aud = env.get("CF_ACCESS_AUD", "").strip()
    emails = frozenset(_csv(env.get("ALLOWED_EMAILS", "")))
    domains = frozenset(_csv(env.get("ALLOWED_EMAIL_DOMAINS", "")))

    if mode == AUTH_CLOUDFLARE:
        if not _TEAM_DOMAIN_RE.match(team):
            raise ConfigError("CF_ACCESS_TEAM_DOMAIN must look like <team>.cloudflareaccess.com")
        if not _AUD_RE.match(aud):
            raise ConfigError("CF_ACCESS_AUD must be the Access application's AUD tag")
        # Defence in depth: an Access policy of "everyone with an email" is a one-click
        # mistake. The app therefore insists on its own allow-list as a second lock.
        if not emails and not domains:
            raise ConfigError("set ALLOWED_EMAILS and/or ALLOWED_EMAIL_DOMAINS (second lock behind Access)")
        for e in emails:
            if not _EMAIL_RE.match(e):
                raise ConfigError("ALLOWED_EMAILS contains an invalid address")
        for d in domains:
            if not _DOMAIN_RE.match(d):
                raise ConfigError("ALLOWED_EMAIL_DOMAINS contains an invalid domain")
    elif env.get("CONSOLE_ALLOW_INSECURE_NO_AUTH", "") != "1":
        raise ConfigError("CONSOLE_AUTH=none needs CONSOLE_ALLOW_INSECURE_NO_AUTH=1 (local development only)")

    return Settings(
        gateway_url=url,
        api_key=api_key,
        auth_mode=mode,
        cf_team_domain=team,
        cf_aud=aud,
        allowed_emails=emails,
        allowed_email_domains=domains,
        refresh_seconds=_int(env, "REFRESH_SECONDS", 10, MIN_REFRESH_SECONDS, 300),
        timezone=env.get("CONSOLE_TZ", "Asia/Colombo").strip() or "Asia/Colombo",
    )

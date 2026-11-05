import pytest

from gwconsole.config import ConfigError, Settings, load_settings

GOOD_CF = {
    "GATEWAY_URL": "http://gateway.shalotrack.internal:8001",
    "COMMAND_API_KEY": "s3cret-value",
    "CF_ACCESS_TEAM_DOMAIN": "shalotrack.cloudflareaccess.com",
    "CF_ACCESS_AUD": "a" * 64,
    "ALLOWED_EMAILS": "Ops@Shalotrack.com, dev@shalotrack.com",
}


def test_good_cloudflare_config():
    s = load_settings(GOOD_CF)
    assert s.auth_mode == "cloudflare_access"
    assert s.allowed_emails == {"ops@shalotrack.com", "dev@shalotrack.com"}  # normalised
    assert s.refresh_seconds == 10


def test_key_is_required_and_never_printed():
    with pytest.raises(ConfigError, match="COMMAND_API_KEY"):
        load_settings({**GOOD_CF, "COMMAND_API_KEY": "  "})
    s = load_settings(GOOD_CF)
    assert "s3cret-value" not in repr(s) and "s3cret-value" not in str(s)


@pytest.mark.parametrize("url", [
    "ftp://gateway:8001", "gateway:8001", "http://", "http://user:pw@gateway:8001",
    "http://gateway:8001/admin", "http://gateway:8001?x=1", "javascript:alert(1)",
])
def test_bad_gateway_urls_rejected(url):
    with pytest.raises(ConfigError, match="GATEWAY_URL"):
        load_settings({**GOOD_CF, "GATEWAY_URL": url})


@pytest.mark.parametrize("key,value", [
    ("CF_ACCESS_TEAM_DOMAIN", "evil.example.com"),
    ("CF_ACCESS_TEAM_DOMAIN", "shalotrack.cloudflareaccess.com.evil.com"),
    ("CF_ACCESS_TEAM_DOMAIN", ""),
    ("CF_ACCESS_AUD", "short"),
    ("CF_ACCESS_AUD", ""),
])
def test_cloudflare_settings_validated(key, value):
    with pytest.raises(ConfigError):
        load_settings({**GOOD_CF, key: value})


def test_second_lock_allowlist_is_mandatory():
    env = {k: v for k, v in GOOD_CF.items() if k != "ALLOWED_EMAILS"}
    with pytest.raises(ConfigError, match="ALLOWED_EMAILS"):
        load_settings(env)
    assert load_settings({**env, "ALLOWED_EMAIL_DOMAINS": "shalotrack.com"}).allowed_email_domains == {"shalotrack.com"}


def test_bad_allowlist_entries_rejected():
    with pytest.raises(ConfigError):
        load_settings({**GOOD_CF, "ALLOWED_EMAILS": "not-an-email"})
    with pytest.raises(ConfigError):
        load_settings({**GOOD_CF, "ALLOWED_EMAIL_DOMAINS": "-bad-"})


def test_no_auth_mode_needs_explicit_insecure_flag():
    env = {"GATEWAY_URL": "http://127.0.0.1:8001", "COMMAND_API_KEY": "k", "CONSOLE_AUTH": "none"}
    with pytest.raises(ConfigError, match="CONSOLE_ALLOW_INSECURE_NO_AUTH"):
        load_settings(env)
    assert load_settings({**env, "CONSOLE_ALLOW_INSECURE_NO_AUTH": "1"}).auth_mode == "none"
    with pytest.raises(ConfigError):
        load_settings({**env, "CONSOLE_ALLOW_INSECURE_NO_AUTH": "true"})  # exactly "1"


def test_unknown_auth_mode_rejected():
    with pytest.raises(ConfigError, match="CONSOLE_AUTH"):
        load_settings({**GOOD_CF, "CONSOLE_AUTH": "basic"})


@pytest.mark.parametrize("value", ["1", "4", "301", "abc", "5.5"])
def test_refresh_bounds(value):
    with pytest.raises(ConfigError, match="REFRESH_SECONDS"):
        load_settings({**GOOD_CF, "REFRESH_SECONDS": value})


def test_refresh_ok():
    assert load_settings({**GOOD_CF, "REFRESH_SECONDS": "30"}).refresh_seconds == 30


def test_settings_is_frozen():
    s = load_settings(GOOD_CF)
    assert isinstance(s, Settings)
    with pytest.raises(Exception):
        s.api_key = "x"  # type: ignore[misc]

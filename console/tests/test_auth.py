import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from gwconsole.auth import AuthError, email_allowed, verify_access_jwt
from gwconsole.config import load_settings

TEAM = "shalotrack.cloudflareaccess.com"
AUD = "a" * 64
ISS = f"https://{TEAM}"


def _settings(**over):
    env = {
        "COMMAND_API_KEY": "k", "CF_ACCESS_TEAM_DOMAIN": TEAM, "CF_ACCESS_AUD": AUD,
        "ALLOWED_EMAILS": "ops@shalotrack.com", "ALLOWED_EMAIL_DOMAINS": "staff.shalotrack.com",
    }
    env.update(over)
    return load_settings(env)


def _keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def key():
    return _keypair()


class FakeKeys:
    """Stands in for PyJWKClient: always returns the given public key."""

    def __init__(self, public_key, fail=False):
        self.public_key, self.fail = public_key, fail

    def get_signing_key_from_jwt(self, token):
        if self.fail:
            raise RuntimeError("certs endpoint down")
        return SimpleNamespace(key=self.public_key)


def _token(private_key, **over):
    now = int(time.time())
    claims = {"iss": ISS, "aud": [AUD], "email": "ops@shalotrack.com", "iat": now, "exp": now + 3600, "sub": "u1"}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, private_key, algorithm="RS256")


def test_valid_token_accepted(key):
    ident = verify_access_jwt(_token(key), _settings(), FakeKeys(key.public_key()))
    assert ident.email == "ops@shalotrack.com"
    assert ident.expires_at > time.time()


def test_domain_allowlist(key):
    t = _token(key, email="Anyone@Staff.Shalotrack.com")
    assert verify_access_jwt(t, _settings(), FakeKeys(key.public_key())).email == "anyone@staff.shalotrack.com"


@pytest.mark.parametrize("email", ["x@shalotrack.com", "ops@shalotrack.com.evil.io", "ops@evilshalotrack.com", "nobody"])
def test_unlisted_email_rejected(key, email):
    with pytest.raises(AuthError):
        verify_access_jwt(_token(key, email=email), _settings(), FakeKeys(key.public_key()))


def test_subdomain_is_not_implicitly_allowed():
    s = _settings(ALLOWED_EMAIL_DOMAINS="shalotrack.com", ALLOWED_EMAILS="")
    assert not email_allowed("a@sub.shalotrack.com", s)
    assert email_allowed("a@shalotrack.com", s)


def test_missing_token(key):
    for tok in (None, ""):
        with pytest.raises(AuthError, match="missing"):
            verify_access_jwt(tok, _settings(), FakeKeys(key.public_key()))


def test_expired_rejected(key):
    with pytest.raises(AuthError, match="ExpiredSignatureError"):
        verify_access_jwt(_token(key, exp=int(time.time()) - 600), _settings(), FakeKeys(key.public_key()))


def test_wrong_audience_rejected(key):
    with pytest.raises(AuthError, match="InvalidAudienceError"):
        verify_access_jwt(_token(key, aud=["b" * 64]), _settings(), FakeKeys(key.public_key()))


def test_wrong_issuer_rejected(key):
    with pytest.raises(AuthError, match="InvalidIssuerError"):
        verify_access_jwt(_token(key, iss="https://evil.cloudflareaccess.com"), _settings(), FakeKeys(key.public_key()))


@pytest.mark.parametrize("missing", ["exp", "iat", "iss", "aud"])
def test_required_claims(key, missing):
    now = int(time.time())
    claims = {"iss": ISS, "aud": [AUD], "email": "ops@shalotrack.com", "iat": now, "exp": now + 3600}
    del claims[missing]
    with pytest.raises(AuthError):
        verify_access_jwt(jwt.encode(claims, key, algorithm="RS256"), _settings(), FakeKeys(key.public_key()))


def test_signed_by_another_key_rejected(key):
    attacker = _keypair()
    with pytest.raises(AuthError, match="InvalidSignatureError"):
        verify_access_jwt(_token(attacker), _settings(), FakeKeys(key.public_key()))


def test_tampered_payload_rejected(key):
    head, payload, sig = _token(key).split(".")
    forged = jwt.utils.base64url_encode(b'{"iss":"%s","aud":["%s"],"email":"ops@shalotrack.com","iat":1,"exp":9999999999}'
                                        % (ISS.encode(), AUD.encode())).decode()
    with pytest.raises(AuthError):
        verify_access_jwt(f"{head}.{forged}.{sig}", _settings(), FakeKeys(key.public_key()))


def test_alg_none_rejected(key):
    now = int(time.time())
    unsigned = jwt.encode({"iss": ISS, "aud": [AUD], "email": "ops@shalotrack.com", "iat": now, "exp": now + 99},
                          None, algorithm="none")
    with pytest.raises(AuthError):
        verify_access_jwt(unsigned, _settings(), FakeKeys(key.public_key()))


def test_hs256_key_confusion_rejected(key):
    """Classic attack: sign with HS256 using the PUBLIC key bytes as the HMAC secret.
    Built by hand because PyJWT's encoder (rightly) refuses a PEM as an HMAC secret."""
    import hashlib
    import hmac
    import json

    b64 = lambda b: jwt.utils.base64url_encode(b).decode()  # noqa: E731
    public_pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    now = int(time.time())
    signing_input = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps(
        {"iss": ISS, "aud": [AUD], "email": "ops@shalotrack.com", "iat": now, "exp": now + 99}).encode())
    sig = b64(hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest())
    with pytest.raises(AuthError):
        verify_access_jwt(f"{signing_input}.{sig}", _settings(), FakeKeys(key.public_key()))


def test_service_token_without_email_rejected(key):
    with pytest.raises(AuthError, match="no user email"):
        verify_access_jwt(_token(key, email=None), _settings(), FakeKeys(key.public_key()))


def test_key_endpoint_failure_fails_closed(key):
    with pytest.raises(AuthError, match="key lookup failed"):
        verify_access_jwt(_token(key), _settings(), FakeKeys(key.public_key(), fail=True))


def test_garbage_token(key):
    with pytest.raises(AuthError):
        verify_access_jwt("not.a.jwt", _settings(), FakeKeys(key.public_key()))

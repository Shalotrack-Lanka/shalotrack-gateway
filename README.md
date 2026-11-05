# ShaloTrack Gateway Console

A **read-only** Streamlit operations view of the TCP gateway: health, connected devices, and the command catalogue. It talks only to the gateway's Command API (`GET /health`, `/devices`, `/commands`). It cannot send a command to a device and has no database access.

| Tab | Shows |
|---|---|
| Overview | OK / degraded / down banner with the gateway's own reasons, devices connected, last valid packet, uptime, DB latency, per-check table, raw report |
| Devices | Connected devices (IMEI, protocol, IP, state, last packet, connected for), longest-silent first, search and filters |
| Commands | The gateway's command list grouped by impact. Sending is deliberately not here: the Admin portal does it with role checks and an audit trail |

## Where it lives (and why not `gateway.shalotrack.com:8000`)

`gateway.shalotrack.com` is a DNS-only record to the NLB, and port **8000 is the raw TCP listener for GPS devices**. Cloudflare does not proxy HTTP on port 8000, and a browser request there would reach the GPS parser, not this app. Use its own hostname, for example `console.shalotrack.com`, through **Cloudflare Tunnel + Cloudflare Access** (free tier, no inbound ports, no ALB changes):

```
browser ──HTTPS──> Cloudflare (Access: SSO/OTP login) ──tunnel──> cloudflared ──> console :8501 ──HTTP + key──> gateway :8001 (VPC)
```

## Security model

1. **Access** authenticates the person (identity provider, short session, MFA at the IdP).
2. **The app re-verifies** the `Cf-Access-Jwt-Assertion` JWT itself: RS256 only, signature against the team's published keys, audience = this Access application, issuer, expiry, and the email must be on `ALLOWED_EMAILS` / `ALLOWED_EMAIL_DOMAINS`. Missing or invalid means no page and **no gateway request**.
3. **Fails closed.** Bad or missing configuration shows a generic error. `CONSOLE_AUTH=none` (local development) needs `CONSOLE_ALLOW_INSECURE_NO_AUTH=1` and is refused unless the server is bound to loopback, so the container image can never run unauthenticated.
4. **Read-only by construction.** The client has one HTTP verb (GET) and a three-path allow-list; a test fails if a write verb is ever added. Redirects are never followed (they would resend the key).
5. **Secrets.** `COMMAND_API_KEY` comes from the environment (SSM SecureString in production), is excluded from `repr`, errors, logs and the UI. Gateway failures are reduced to a small set of messages: no hostnames, tracebacks or response bodies reach the browser.
6. **Load protection.** All viewers share one cached read (health 10 s, devices 5 s), including failures, so ten open tabs cause one request per interval and a gateway outage cannot turn into a pile of timeouts.
7. **Container.** Non-root (uid 10001), read-only code, pinned dependencies, healthcheck.

Known limit: the Command API has **one all-or-nothing key**, so this console's key could also call `POST /command`. The console never does, but a compromised console host could. The proper fix is a GET-only key on the gateway (`COMMAND_API_READ_KEY`); until then keep the console on its own host with no inbound ports.

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `GATEWAY_URL` | yes | `http://gateway.shalotrack.internal:8001` (scheme://host:port only) |
| `COMMAND_API_KEY` | yes | The gateway's shared key |
| `CF_ACCESS_TEAM_DOMAIN` | yes | `<team>.cloudflareaccess.com` |
| `CF_ACCESS_AUD` | yes | The Access application's AUD tag |
| `ALLOWED_EMAILS` / `ALLOWED_EMAIL_DOMAINS` | at least one | Second lock behind Access |
| `REFRESH_SECONDS` | no | 5 to 300, default 10 |
| `CONSOLE_TZ` | no | Default `Asia/Colombo` |

See `.env.example`.

## Deploy (Cloudflare Tunnel + Access)

1. **Host.** A small instance in the VPC that can reach `gateway.shalotrack.internal:8001`. Streamlit needs a few hundred MB of RAM, so choose a micro-class instance. Security group: **no inbound rules**; outbound 443 (Cloudflare, ECR, SSM) and 8001 to the gateway. Prefer a dedicated instance over sharing the Admin one, because the key can control vehicles.
2. **Access application.** Zero Trust, Access, Applications, Self-hosted: hostname `console.shalotrack.com`; policy Allow with the staff emails; session duration short (for example 8 h). Note the **AUD tag** and your **team domain**.
3. **Tunnel.** Zero Trust, Networks, Tunnels, create `gateway-console`; public hostname `console.shalotrack.com` to `http://localhost:8501`; run `cloudflared` with the tunnel token on the same host.
4. **SSM.** Store `COMMAND_API_KEY` and the Access values as SecureString parameters.
5. **Run** (example):

```bash
docker run -d --name gateway-console --restart unless-stopped \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --memory 512m \
  -p 127.0.0.1:8501:8501 \
  -e GATEWAY_URL=http://gateway.shalotrack.internal:8001 \
  -e COMMAND_API_KEY="$(aws ssm get-parameter --name /shalotrack/prod/gateway-console/command_api_key --with-decryption --query Parameter.Value --output text)" \
  -e CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com -e CF_ACCESS_AUD=<aud> \
  -e ALLOWED_EMAIL_DOMAINS=shalotrack.com \
  <ecr>/gateway-console:<tag>
```

6. **Verify.** Opening the hostname must show the Access login; after login, the Overview. `curl` to the hostname without Access must never return the app. The origin port must not be reachable from anywhere except loopback.

## Develop and test

```bash
pip install -r requirements-dev.txt
python -m pytest -q                       # unit + app tests (fake gateway)

# try the UI locally, no gateway needed
python -m tests.fake_gateway --scenario degraded --port 8001 --key dev-key &
GATEWAY_URL=http://127.0.0.1:8001 COMMAND_API_KEY=dev-key \
CONSOLE_AUTH=none CONSOLE_ALLOW_INSECURE_NO_AUTH=1 streamlit run app.py
```

`tests/test_contract_real_gateway.py` boots the real gateway handler (no database) and is skipped unless the gateway repo is available:
`GATEWAY_REPO=../shalotrack-gateway GATEWAY_PYTHON=<python with gateway requirements> python -m pytest tests/test_contract_real_gateway.py`.
It also fails if the gateway adds a command the console has not classified, so new commands are never silently mislabelled.

## Limits

- Point-in-time view: no history (that needs a database role, which this app intentionally does not have).
- IMEIs and device IPs are shown to allow-listed staff.
- State is in memory; restarting the console loses nothing important.

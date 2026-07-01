# Deployment

*Author: Suwen Jayathunga — Lead Architect, ShaloTrack Lanka*

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.11+ |
| PostgreSQL | Any recent version (the gateway is a client; it does not manage the database) |
| Docker | Optional, required only for containerized deployment |

## Environment Variables

All configuration is injected via environment variables. Create a `.env` file in the project root (loaded automatically by `python-dotenv` via `database.py`):

```env
# Required
DATABASE_URL=postgresql://user:password@host:5432/dbname

# Optional — these have defaults
PORT=9000
CONNECTION_TIMEOUT=60
LOW_BATTERY_THRESHOLD=2
OVERSPEED_THRESHOLD=80
```

> **Important:** `tcp_server.py` reads `PORT` directly from `os.environ` with a default of `9000`, rather than from `config.py`. `CONNECTION_TIMEOUT`, `LOW_BATTERY_THRESHOLD`, and `OVERSPEED_THRESHOLD` are defined in `config.py` but are not currently read by any other module — they are set here for forward compatibility.

## Local Development

```bash
git clone https://github.com/Shalotrack-Lanka/shalotrack-gateway.git
cd shalotrack-gateway
pip install -r requirements.txt
cp .env.example .env    # then fill in your DATABASE_URL
python tcp_server.py
```

The server will print:

```
[2025-xx-xx xx:xx:xx] 🚀 TCP Server listening on port 9000

===== ShaloTrack Command Console =====
Commands:
where <imei>
reset <imei>
relay_on <imei>
relay_off <imei>
exit
```

## Docker

### Build

```bash
docker build -t shalotrack-gateway .
```

### Run

```bash
docker run \
  -p 9000:9000 \
  --env-file .env \
  shalotrack-gateway
```

Or with inline environment variables:

```bash
docker run \
  -p 9000:9000 \
  -e DATABASE_URL=postgresql://user:password@host:5432/dbname \
  -e PORT=9000 \
  shalotrack-gateway
```

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 9000
CMD ["python", "tcp_server.py"]
```

## AWS Deployment

The gateway has been tested deployed on AWS (exact service not specified in the codebase, but the Docker setup is compatible with EC2, ECS Fargate, or App Runner).

Key points for AWS:
- **Port:** Open TCP port `9000` inbound in the Security Group for the IP ranges used by your GPS trackers (or `0.0.0.0/0` if trackers are dynamic IP).
- **Database:** `DATABASE_URL` should point to your RDS PostgreSQL endpoint. Ensure the gateway's Security Group has access to the RDS Security Group on port 5432.
- **No HTTPS/TLS:** The gateway communicates with GPS trackers in raw TCP (the GT06/V5 protocol is binary, not HTTP). TLS termination is not part of the current implementation.
- **Process restart:** There is no supervisor (systemd, PM2, supervisord) configured — if the process crashes it will not restart automatically unless the container orchestrator handles it (e.g. ECS task restarts, or a Docker `--restart=always` flag).

## Dependencies (`requirements.txt`)

| Package | Purpose |
|---|---|
| `psycopg2-binary` | PostgreSQL adapter |
| `python-dotenv` | `.env` file loading |
| `certifi` | TLS certificates (transitive dependency) |
| `requests` | HTTP (present but not imported in current code — likely a planned dependency for API integration) |
| `urllib3` | HTTP (same — transitive from `requests`) |
| `pytz` | Timezone support (present but not used in current code — timestamps are stored as UTC naive datetimes via `datetime.utcnow()`) |

## Running Tests

The `test/` directory contains three manual scripts, not an automated test suite:

```bash
python test/test_db.py           # tests DB connectivity only
python test/test_commands.py     # sends a WHERE# command (requires a connected device)
python test/test_device_lookup.py  # WARNING: imports a function that does not exist
```

There is no `pytest` or equivalent runner configured. See the Known Limitations section of the README.

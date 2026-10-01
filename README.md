# cowork-booking-purchase

Purchase is the Cowork Booking service a Member uses directly (port 8001). It owns:

- **Members**: email, display name, password hash, `is_operator`, `plan_active` (no separate Identity service, ADR-0002).
- **Spaces**: name, capacity, hourly rate in satang, `archived_at` (archived, never deleted).
- **Bookings**: reference `BK-XXXXXX`, times on the 30-minute grid, party size, agreed price, coverage (`pay`/`plan`/`free`), status (`held → confirmed | expired | cancelled`, `confirmed → cancelled`), payment session, refund and grant follow-up state.
- **Price and coverage**, fixed at creation.

Purchase is the only caller of the other two services: `payment_client.py` talks to Payment (sessions, expire, refunds) and `access_client.py` talks to Access (grants, revoke). Every call uses a bearer token, `timeout=5`, and never runs inside a DB transaction.

- Contract (frozen at `contract-v1`): [CONTRACT.md](CONTRACT.md), [openapi.yaml](openapi.yaml)
- Docs repo (rules, decisions, PRD, ADRs, diagrams): https://github.com/JedizR/cowork-booking-docs
- Seed history: [PROVENANCE.md](PROVENANCE.md)

## Quick start

```bash
docker compose up -d --build --wait
curl http://localhost:8001/health      # {"revision":"compose","status":"ok"}
docker compose down
```

Open http://localhost:8001, sign up, and book. Four demo spaces are seeded when the spaces table is empty. Register `operator@example.com` (the default `OPERATOR_EMAIL`) to get the operator pages. Paid bookings need Payment on :8002 and e-tickets need Access on :8003; run the whole stack from `cowork-booking-docs/integration`. Without them a paid booking stays held with "Payment is not reachable. Please try again." and a free booking shows "E-ticket being prepared".

## Ports

| What | Port |
|---|---|
| Purchase app | 8001 (container 8000) |
| Purchase postgres (this compose file only) | 5441 |
| Payment / Access (other repos) | 8002 / 8003 |

## Environment

| Variable | Required | Default in compose | Meaning |
|---|---|---|---|
| `DATABASE_URL` | yes | `postgresql://cowork:cowork@db:5432/cowork_purchase` | Postgres 16; DDL runs at start, fail fast when unreachable |
| `SECRET_KEY` | yes | dev value | Signs the `purchase_session` cookie; the app refuses to start without it |
| `APP_REVISION` | no | `compose` | Shown by `/health` |
| `PUBLIC_URL` | no | `http://localhost:8001` | Builds `success_url`/`cancel_url`; `https` turns on Secure cookies |
| `PAYMENT_INTERNAL_URL` | yes | `http://host.docker.internal:8002` | Payment API base for server calls |
| `PAYMENT_PUBLIC_URL` | yes | `http://localhost:8002` | Browser redirect to `/pay/<id>` and the "Payment totals" link |
| `PAYMENT_API_TOKEN` | yes | dev value | Bearer token for Payment |
| `ACCESS_INTERNAL_URL` | yes | `http://host.docker.internal:8003` | Access API base for server calls |
| `ACCESS_PUBLIC_URL` | yes | `http://localhost:8003` | Only `ticket_url`s under `<this>/t/` are stored |
| `ACCESS_API_TOKEN` | yes | dev value | Bearer token for Access |
| `OPERATOR_EMAIL` | no | `operator@example.com` | This Member becomes operator at sign-up or login |
| `TEST_CLOCK_ENABLED` | no | unset | `true` only in the e2e override: opens `POST /_test/clock` (else 404) |

## Tests

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
docker run -d --rm --name purchase-test-pg -e POSTGRES_PASSWORD=postgres -p 55461:5432 postgres:16
DATABASE_URL=postgresql://postgres:postgres@localhost:55461/postgres .venv/bin/python -m pytest -q --junitxml=reports/junit.xml
docker rm -f purchase-test-pg
```

Each test runs on a clean schema and is named after the rule it proves (`test_pur_r30_...`). Payment and Access are stubbed at `payment_client.py` / `access_client.py` with the answers from their contracts, including the amount mismatch (D14) the real Payment cannot produce.

## Layout

`app.py` (routes, DB, sync), `purchase.py` (price, grid, refund policy, references), `clock.py` (the only time source), `payment_client.py`, `access_client.py`, `templates/`, `static/style.css`, `gunicorn.conf.py` (2 workers, one connection each).

# Provenance

Seeded from `cs403bkk-2026/spacey` at `5a1cf3d90e538f431625cbb959987b7bdbe3c946` by copy-and-prune (ADR-0006). The project goes straight to three repos; the course takes a strangler path through "Separate ownership, still one monolith".

## Removed before the seed commit

`STARTUP_LOG.md`, `LOAD_TEST.md`, `CONTRIBUTING.md`, `scripts/`, `deploy/` (the Nomad job), `.github/workflows/delivery.yml` (it pushed to ghcr and deployed on every push to main). Startup persona names were replaced by Member A, Member B and Member C.

## The prune (`refactor: prune to purchase context`)

Kept (Purchase context, BRIEF section 10):

- spaces, bookings (reshaped in M5), users (become members with `is_operator` and `plan_active` in M5)
- register, login, logout, `inject_current_user`
- `purchase.py` (`calculate_booking_price_cents`)
- booking metrics and the dashboard
- templates: index, my_bookings, login, register, dashboard, base, booking_not_found, and the booking header of `confirmation.html`
- coverage code (`member_key`, `is_subscribed`, the subscribe route, the subscriptions table) until it becomes `Member.plan_active` in M5
- `/health` with `revision`, the fail-fast DB connect, the gunicorn query-string scrubbing access log, `base.html` + `style.css`, the `money` and `local_time` filters

Deleted (owned by another context):

- the pay routes (`POST /bookings/<id>/pay`, `POST /bookings/<id>/confirmation/pay`), `mark_booking_paid`, the card form in `confirmation.html`, `validate_card` and the card regexes: Payment owns them
- the unlock routes (`POST /bookings/<id>/unlock`, `POST /bookings/<id>/confirmation/unlock`) and `issue_access_code`: Access owns them
- the `force_failure` test hook
- the 41 tests that exercised the routes above

Added: `.github/workflows/ci.yml` (pytest against postgres:16, then `docker build`, no push), `CONTRACT.md` and `openapi.yaml` (contract-v1).

## Seed flaws relevant to Purchase

From cowork-booking-docs `DECISIONS.md`, "Seed flaws and where they are handled".

| Flaw | Summary | Handled by | Fixed or out of scope |
|---|---|---|---|
| F1 | Free-form times | D2, D3, D4; PUR-R07, PUR-R08, PUR-R09, PUR-R13 | Fixed: 30-minute blocks, 1-8 blocks, 08:00-20:00, server builds the instant |
| F2 | Past bookings accepted | D5; PUR-R10 | Fixed: start at least 60 min after clock.now(), at most 30 days ahead |
| F3 | Unpaid holds block the slot forever | D11, D12, D13; PUR-R12, PUR-R21, PUR-R22, PUR-R24 | Fixed: 15-min hold, lazy expiry, partial EXCLUDE on held and confirmed. Out of scope: deliberate hold cycling (ADR-0016, PUR-Q17) |
| F4 | Access code regenerated, ?code= rendered | D20, D22, D28; PUR-R26 | Fixed: Purchase requests one grant per confirmed booking; no page shows text from the URL |
| F5 | Cancel hard-deletes the booking | D18, D19; PUR-R28, PUR-R30, PUR-R31, PUR-R32 | Fixed: cancel is a status with refund amount, reason and outcome stored |
| F6 | Subscription keyed by a typed name | D10, D15; PUR-R06, PUR-R19, PUR-R20 | Fixed: coverage from `plan_active` on the logged-in Member, set by the Operator |
| F7 | No ownership checks | D15, D17, D22, D23; PUR-R05, PUR-R06, PUR-R29 | Fixed: owner or Operator only (else 404); random references |
| F8 | Public dashboard | D24, D17; PUR-R06, PUR-R34 | Fixed: Operator-only dashboard, no money |
| F9 | INTEGER amount overflow | D1, D4, D7, D9; PUR-R15, PUR-R17, PUR-R18 | Fixed: BIGINT satang, rate cap, capacity cap 1,000 |
| F10 | Sessions never expire, default SECRET_KEY | D15; PUR-R03 | Fixed: 12 h absolute sessions, SECRET_KEY required. Out of scope: cookie replay inside 12 h |
| F11 | Registration reveals an email exists | D16; PUR-R01, PUR-R02 | Out of scope: accepted trade-off; login keeps a uniform error |
| F12 | ?error= reflected | D28; PUR-R36 | Fixed: flash() only |
| F13 | No CSRF tokens | D15; PUR-R03, PUR-R37 | Fixed by mitigation: SameSite=Lax, every action a POST (ADR-0009) |
| F15 | No party size | D7; PUR-R14 | Fixed: party size 1..capacity |
| F16 | 0-amount booking asks for a card | D10, D1, D9; PUR-R19, PUR-R20 | Fixed: free coverage confirms at once, no Payment call |
| A1 | Pay racing cancel crashes | D18, D28; PUR-R31 | Fixed: held cancel expires the session first |
| A3 | Subscribing "guest" frees anonymous bookings | D10, D15; PUR-R05, PUR-R19 | Fixed: no anonymous bookings |
| A4 | Typed member name vs account | D15; PUR-R05 | Fixed: bookings belong to the logged-in Member |
| A5 | Metrics conflate coverage with payment | D24, D10; PUR-R34 | Fixed: members are accounts; plan/free never revenue |
| A6 | force_failure live in production | D27; PUR-R38 | Fixed: removed in this prune; only the clock hook, 404 unless TEST_CLOCK_ENABLED |
| A7 | Naive time accepted by the form | D2; PUR-R07 | Fixed: JSON needs an offset; the form sends date + block |
| A8 | One shared connection, DDL at import | D28; PUR-R22, PUR-R35 | Fixed in part: one connection per worker, 2 workers. Out of scope: reconnect; psycopg_pool is the upgrade path |
| A9 | JSON member not checked | D15; PUR-R05 | Fixed: no member field |
| A10 | Money shown in dollars | D1; PUR-R18 | Fixed: "THB 1,234.50", satang integers |
| A11 | No injectable clock | D27; PUR-R12, PUR-R38 | Fixed: clock.now() everywhere |
| A12 | Nullable times in the EXCLUDE range | D2, D4, D11; PUR-R07, PUR-R09, PUR-R22 | Fixed: both bounds built by the server; EXCLUDE on held and confirmed only |
| A13 | Homepage lists upcoming bookings | D4, D17; PUR-R13 | Out of scope: the grid shows taken blocks, no member data |
| A14 | Deleting a space erases revenue history | D23, D18; PUR-R16, PUR-R28 | Fixed: spaces are archived, never deleted |
| A15 | Backfill fills NULL amount with the current rate | D8; PUR-R17 | Fixed: price stored at creation, never recalculated |

F14 and A2 belong to Payment and Access.

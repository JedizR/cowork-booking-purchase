# Contract: Purchase public surface

| Field | Value |
|---|---|
| Provider | Purchase (`cowork-booking-purchase`), port 8001 |
| Consumers | The Member's and the Operator's browser; the e2e suite (`requests.Session`) |
| State | proposed (M2 draft). Becomes agreed at M4 sign-off (tag `contract-v1`), verified by the M6 e2e run |
| OpenAPI | [openapi/purchase.yaml](openapi/purchase.yaml) |
| Outbound calls | Purchase → Payment ([purchase-payment.md](purchase-payment.md)); Purchase → Access ([purchase-access.md](purchase-access.md)) |
| Decisions | D1-D5, D7-D11, D13-D20, D23, D24, D27, D28; ADR-0002, ADR-0004, ADR-0007, ADR-0009, ADR-0013, ADR-0014 |

## 1. Purpose and parties

Purchase is the only service a Member uses directly. It owns members, spaces, 30-minute availability, bookings, price, coverage, booking status and cancellation (PUR-T33). This contract pins every browser route, form field, flashed message and redirect, plus the JSON API, so that the browser pages, the e2e suite and the other two implementers agree on one surface.

- Account data (email, display name, password hash) lives on Member in Purchase. There is no Identity service (ADR-0002).
- Purchase sends the browser to Payment's hosted page and links Access's e-ticket. It is the only caller of the other two services (PUR-R35, ADR-0004).

## 2. Authentication and session

- **Login.** `POST /login` (form) sets the cookie `purchase_session`: signed, HttpOnly, SameSite=Lax, plus Secure when `PUBLIC_URL` starts with https (PUR-R03). The JSON API uses the same cookie. There is no JSON login in contract-v1.
- **Lifetime.** 12 h from login, whatever the activity. The session holds the member id, the Member's email and login_at; a Member row that is missing or has another email counts as an ended login. On the first request that finds an ended login, Purchase removes the login from `purchase_session` in that same response, keeps the kept path and flashes "Please log in again" once; the request then runs as anonymous. Routes that need a login (Member, Owner and Operator routes, `/api/bookings*`) redirect to `/login` (the booking form included) or answer 401 "Please log in again"; the public routes (`/`, `/spaces/<id>`, `GET /api/spaces*`, `/register`, `/login`, `/logout`, `/health`) answer as for an anonymous visitor, and `POST /login` and `POST /register` always run (PUR-R03). "Log in to book" and "Please log in" are only for a browser with no login.
- **Logout.** `POST /logout` clears the cookie in that browser. A copy replayed inside the 12 h still works: an accepted trade-off (D15, ADR-0016).
- **Anonymous callers** get the login step before any lookup: 303 to `/login` with a flash (pages) or 401 (JSON) (PUR-R05, PUR-R06). The login step keeps a local GET path in the signed session cookie, never in the URL; after a successful login the browser goes there (PUR-R05). Only GET paths are kept, built by Purchase from the refused route (never from a request field): a refused booking POST keeps the space page with its date and blocks, and a refused POST on `/bookings/<ref>/...` keeps `/bookings/<ref>`. The kept path survives `POST /register` until the next successful login.
- **Ownership.** A booking and its actions are for its owner or an operator. Any other logged-in Member gets 404, the same as for an unknown reference (PUR-R05, D17).
- **Operator pages** (`/operator/*`, `/dashboard`) answer a logged-in non-operator with 404 (PUR-R06). The Member whose email equals `OPERATOR_EMAIL` becomes operator at registration or login (PUR-R04).
- **CSRF.** SameSite=Lax plus "every state change is a POST" is the mitigation. There are no CSRF tokens (PUR-R37, ADR-0009). A GET to a POST route gets 405.

The e2e suite logs in like a browser:

```http
POST /register
Content-Type: application/x-www-form-urlencoded

email=a%40example.com&display_name=Member+A&password=correct-horse
```

```http
HTTP/1.1 303 See Other
Location: /login
```

```http
POST /login
Content-Type: application/x-www-form-urlencoded

email=a%40example.com&password=correct-horse
```

```http
HTTP/1.1 303 See Other
Location: /
Set-Cookie: purchase_session=...; HttpOnly; Path=/; SameSite=Lax
```

`Location` is `/` unless the login step kept a local path, for example `/bookings/BK-7KQ2M9/return` when the session ended during payment. A kept path is used only if it starts with `/`, its second character is neither `/` nor `\`, and it holds no control character; otherwise `/` (PUR-R05).

## 3. Conventions

- **Forms** post `application/x-www-form-urlencoded`. Every successful or refused form POST answers 303 (post, redirect, get). The outcome shows once as a flashed message on the next page (PUR-R36).
- **No text from the URL is ever shown.** `?error=`, `?message=` and `?code=` are ignored (PUR-R36).
- **JSON** requests send `Content-Type: application/json`; a body that is not a JSON object gets 400, except that a route that takes no fields (`POST /api/bookings/<ref>/cancel`) treats an empty body, with or without Content-Type, as `{}`. JSON answers never redirect.
- **Times.** Pages show Bangkok time, "2026-10-07 09:00". JSON needs ISO 8601 with an offset and answers with `+07:00`; a naive time gets 400 (PUR-R07, D2).
- **Money.** Pages show "THB 450.00". JSON uses integer satang in fields ending `_satang`, with `"currency": "THB"` (PUR-R18, D1).
- **JSON errors** always have this shape. The rule rows quote only the text, which is `error.message`. Only `GET /health` keeps the seed shape, `{"status": "error", "error": "database unreachable"}` (section 4):

```json
{"error": {"code": "slot_taken", "message": "Slot just taken"}}
```

| Status | error.code | message (example) | Rule |
|---|---|---|---|
| 400 | `invalid_request` | "Time needs an offset, for example +07:00"; "Start on :00 or :30"; "Duration must be 1 to 8 blocks"; "Outside opening hours 08:00-20:00"; "Book at least 60 minutes ahead"; "Book at most 30 days ahead"; "Party size must be 1 to 6"; "Note must be at most 500 characters"; "Pick a date from today to 30 days ahead" | PUR-R07, PUR-R08, PUR-R09, PUR-R10, PUR-R13, PUR-R14 |
| 401 | `unauthorized` | "Please log in"; "Please log in again" for an ended login | PUR-R03, PUR-R05 |
| 404 | `not_found` | "Not found" (unknown, not yours, archived space, or not an operator) | PUR-R05, PUR-R06, PUR-R16 |
| 409 | `slot_taken` | "Slot just taken" | PUR-R12, PUR-R22 |
| 409 | `held_booking_exists` | "Finish or cancel your held booking BK-7KQ2M9 first"; from that hold's payment deadline "Time to pay has run out on BK-7KQ2M9; cancel it, or try again from 10:15" | PUR-R39, PUR-R40 |
| 409 | `payment_time_over` | "Time to pay has run out; cancel this hold or book again from 10:15" | PUR-R40 |
| 409 | `booking_started` | "This booking has started; ask the operator" | PUR-R30 |
| 409 | `booking_ended` | "This booking has ended" | PUR-R30 |
| 409 | `hold_expired` | "This hold has already expired" (also a cancel of a booking that is already expired) | PUR-R31 |
| 503 | `payment_unreachable` | "Payment is not reachable. Please try again." | PUR-R22, PUR-R23, PUR-R31 |

## 4. Browser routes

"Member" means any logged-in Member; "Owner" means the booking's Member or an operator; "Operator" means `is_operator`. Anonymous callers of a Member, Owner or Operator route get 303 to `/login` with "Log in to book" (booking form) or "Please log in" (other pages); an ended login gets "Please log in again" once, on the first request that finds it, and is anonymous from then on (section 2, PUR-R03). Every page's header shows Spaces, My bookings and Log out with "Member A (a@example.com)", or Log in and Sign up; for an operator it also links All bookings, Spaces admin, Members, Dashboard and "Payment totals" (`PAYMENT_PUBLIC_URL/operator`).

| Method | Path | Who | Input | Success | Other outcomes | Rules |
|---|---|---|---|---|---|---|
| GET | `/health` | anyone | none | 200 `{"status": "ok", "revision": "<APP_REVISION>"}` | 503 `{"status": "error", "error": "database unreachable"}` | none (kept from the seed) |
| GET | `/register` | anyone | none | 200 sign-up form | | PUR-R01 |
| POST | `/register` | anyone | `email`, `display_name`, `password` | 303 `/login`, flash "Registered. Please log in." | 303 `/register` with "Email already registered", "Enter a valid email" (not ASCII, over 254 characters or not like a@example.com), "Password must be at least 8 characters", "Display name is required" or "Display name must be 1 to 50 letters, digits or spaces" (letters, marks, digits, spaces and . ' - only) | PUR-R01, PUR-R04 |
| GET | `/login` | anyone | none | 200 login form | | PUR-R02 |
| POST | `/login` | anyone | `email`, `password` | 303 to the kept local path, else `/` (section 2); the header shows the display name and the email, "Member A (a@example.com)" | 303 `/login` with "Invalid email or password" (same for unknown email and wrong password) | PUR-R02, PUR-R03, PUR-R04 |
| POST | `/logout` | anyone | none | 303 `/`, flash "Logged out"; cookie cleared | GET `/logout` gets 405 | PUR-R37 |
| GET | `/` | anyone | none | 200 non-archived spaces: name, capacity, "THB 300.00 per hour, THB 150.00 per 30 min". Empty: "No spaces to book yet." | | PUR-R15, PUR-R16, PUR-R18 |
| GET | `/spaces/<space_id>` | anyone | query `date` (YYYY-MM-DD, default today), `blocks` (1-8, default 1) | 200 space page: a GET form to `/spaces/<space_id>` with the date input (min today, max today + 30), the duration 1-8 and a "Show starts" button (an optional onchange submit); then the price for that duration, the start-block grid, party size 1 to capacity, note, the review line and one submit button (section 6). Anonymous: the pay-coverage review and cancellation terms (a free space, rate 0: "THB 0.00" and no refund line), and the button reads "Log in to book"; the login step keeps this page with its date and blocks, and the start, party size and note are chosen again after login (PUR-R05) | 404 unknown or archived space; a date outside the horizon gets 303 `/spaces/<space_id>` with "Pick a date from today to 30 days ahead"; blocks outside 1-8 get 303 with "Duration must be 1 to 8 blocks" | PUR-R08, PUR-R09, PUR-R10, PUR-R13, PUR-R14, PUR-R17, PUR-R19 |
| POST | `/spaces/<space_id>/book` | Member | `date`, `start` (HH:MM), `blocks`, `party_size`, `note` (optional) | Pay: 303 to `PAYMENT_PUBLIC_URL/pay/<stored session id>` (PUR-R23). Plan or free: 303 `/bookings/<ref>` | See section 5.3 | PUR-R05, PUR-R07 to PUR-R14, PUR-R17, PUR-R19 to PUR-R23, PUR-R39, PUR-R40 |
| GET | `/bookings/<ref>` | Owner | none | 200 booking page (section 7). Reconciles a held booking and retries pending follow-ups first. The owner and an operator see the party size and the note; an operator also sees the Member (display name, email) | 404 not yours or unknown | PUR-R05, PUR-R24, PUR-R26, PUR-R28, PUR-R32, PUR-R33, PUR-R40 |
| GET | `/bookings/<ref>/return` | Owner | query `session_id=ps_…` (ignored) | Reconciles with the stored session id, then 303 `/bookings/<ref>`. This is the `success_url` | 404 not yours; anonymous (the login ended during payment): 303 `/login` with "Please log in again", keeping `/bookings/<ref>/return` for after login | PUR-R03, PUR-R05, PUR-R24, PUR-R25, PUR-R26 |
| GET | `/bookings/<ref>/cancel` | Owner | query `return_to` (only `booking` is read; anything else means the list) | 200 confirm screen naming the booking and showing the refund (section 5.4). Reconciles a held booking first (PUR-R24). The form carries `shown_refund_satang`, the amount the screen names (section 5.4), and posts to `/bookings/<ref>/cancel`, or to `/operator/bookings/<ref>/cancel` for an operator, whose form carries hidden `return_to=booking` when the screen was opened with it; its button reads "Cancel booking", and a "Keep booking" link goes back to `/bookings/<ref>` | 303 `/bookings/<ref>` when the booking cannot be cancelled, with the flash that a POST would get | PUR-R24, PUR-R30, PUR-R31 |
| POST | `/bookings/<ref>/cancel` | Owner | `shown_refund_satang` (required, integer satang from the confirm screen) | 303 `/bookings/<ref>`, flash "Booking cancelled" | See section 5.4 | PUR-R30, PUR-R31, PUR-R32 |
| POST | `/bookings/<ref>/retry` | Owner | none | 303 `/bookings/<ref>` after retrying, in order: pending grant, pending revoke, pending refund, exactly like opening the booking page, with one flash per step tried, as on the operator route: "E-ticket issued", "Grant revoked", "Refund attempt <n> succeeded" or "Refund attempt <n> failed" (n is the stored pending attempt), "Access is not reachable. Please try again." or "Payment is not reachable. Please try again."; with nothing pending, "Nothing to retry" and no call. It never starts attempt+1, whoever calls it: an operator's Retry on the booking page posts to `/operator/bookings/<ref>/retry` | After a failed refund nothing is sent: the page keeps "Refund failed. The operator will follow up." | PUR-R26, PUR-R32, PUR-R33 |
| GET | `/bookings/mine` | Member | none | 200 upcoming bookings (end after now, any status) by start ascending, then past ones by start descending, confirmed past ones marked "Completed". Each row: reference, room, time, status, plus for a held booking "Pay by 10:13" (from the payment deadline "Time to pay has run out on BK-7KQ2M9; cancel it, or try again from 10:15"; "Payment status unknown" when its read got no answer), and for a confirmed one "View e-ticket" (grant issued) or "E-ticket being prepared" (grant pending). Empty: "No bookings yet" with a link to `/`. Reconciles held bookings first | | PUR-R05, PUR-R24, PUR-R28 |
| GET | `/operator/bookings` | Operator | none | 200 every booking, flagged ones first (those with a refund owed by oldest "Refund requested" time), then newest start first. Columns: reference, Member display name and email, space, date and time, status (a held row reads "held, pay by 10:13", "held, payment not started" or "held, hold ended 10:15 (Reconcile)"; a confirmed row past its end reads "Completed"), coverage, price, flags ("Being prepared", "Revocation pending", "Refund pending", "Refund failed"), and beside a refund flag "Refund requested 2026-10-05 11:00" (refund_requested_at); a cancelled row also shows "Cancelled 2026-10-05 11:00" (cancelled_at). Actions: Cancel (a link to the confirm screen `GET /bookings/<ref>/cancel`; only on held bookings and on confirmed bookings before their end), Retry (only with a pending or failed step), Reconcile (held only), and one "Reconcile all held" button. Empty: "No bookings yet". Markers in section 11 | 404 for a non-operator | PUR-R06, PUR-R24, PUR-Q12 |
| POST | `/operator/bookings/<ref>/cancel` | Operator | `shown_refund_satang` (required, integer satang from the confirm screen); hidden `return_to` | 303 `/bookings/<ref>` when `return_to` is `booking`, else `/operator/bookings`, flash "Booking cancelled". Operator policy: before the end, always 100%, on any booking, the Operator's own included | The two shown-refund refusals answer 303 `/bookings/<ref>/cancel`, as on the Member route; every other refusal answers 303 `/bookings/<ref>` with its flash (section 5.4), for example "This booking has ended" at or after the end | PUR-R06, PUR-R30, PUR-R32 |
| POST | `/operator/bookings/<ref>/retry` | Operator | hidden `return_to` | 303 `/bookings/<ref>` when `return_to` is `booking` (the booking page's form), else `/operator/bookings`. Retries pending steps; a stored failed refund gets attempt+1 (committed as pending before the call). One flash per step tried: "E-ticket issued", "Grant revoked", "Refund attempt <n> succeeded", "Refund attempt <n> failed" (n is the attempt sent: "Refund attempt 1 succeeded" for a resent pending attempt 1, "Refund attempt 2 succeeded" after a failed attempt 1), "Access is not reachable. Please try again." or "Payment is not reachable. Please try again.", or for any other 4xx answer "Payment refused the call (401): check the service settings and the Purchase log." (or Access, with its status; PUR-R35); with no pending or failed step, "Nothing to retry" and no call | 404 for a non-operator: a Member cannot start attempt+1 | PUR-R06, PUR-R32, PUR-R33 |
| POST | `/operator/bookings/<ref>/reconcile` | Operator | hidden `return_to` | 303 `/bookings/<ref>` when `return_to` is `booking`, else `/operator/bookings`, flash "1 confirmed, 0 expired, 0 cancelled, 0 unchanged" (counts for this booking; cancelled counts an amount_mismatch) | "Payment is not reachable. Please try again."; a booking that is not held: no call, "0 confirmed, 0 expired, 0 cancelled, 1 unchanged" | PUR-R06, PUR-R24 |
| POST | `/operator/bookings/reconcile` | Operator | hidden `return_to`: `bookings` or `dashboard` | "Reconcile all held": reconciles every held booking (with a session: the Payment read; without one: expired once lapsed), then 303 to `/dashboard` when `return_to` is `dashboard`, else to `/operator/bookings` (the Referer header is never read), flash "1 confirmed, 1 expired, 0 cancelled, 1 unchanged" | After the first read with no answer Purchase stops calling Payment and counts the rest unchanged; the flash adds "Payment is not reachable. Please try again." After the first POST /grants or POST /refunds with no answer it sends no more of that kind (those bookings stay flagged; the flash adds the matching "... is not reachable" text), and after the first read answered with another 4xx it stops reading and adds "Payment refused the call (401): check the service settings and the Purchase log." | PUR-R06, PUR-R24, PUR-R34 |
| GET | `/operator/spaces` | Operator | none | 200 every space, each labelled with its room number as the kiosk shows it, "Meeting Room A (room 1)", with create, edit and archive forms; archived spaces are listed last, still with their room number, marked "Archived 2026-10-07", with no edit or archive form. Empty: "No spaces yet. Create one below." | | PUR-R06, PUR-R15, PUR-R16 |
| POST | `/operator/spaces` | Operator | `name`, `capacity` (1-1000), `hourly_rate` (whole THB: 0 or 20-10000) | 303 `/operator/spaces`, flash "Space saved". Stored in satang | 303 with the first failing check's flash, in this order: "Name is required" (blank or spaces only), "Name already used" (another non-archived space has that name), "Capacity must be a whole number from 1 to 1,000", "Rate must be 0 or 20 to 10,000 THB per hour"; typed values are not kept | PUR-R15 |
| POST | `/operator/spaces/<space_id>` | Operator | `name`, `capacity`, `hourly_rate` | 303 `/operator/spaces`, flash "Space saved". Existing bookings keep their price and party size; issued e-tickets keep the old name | Same flashes as create; 404 unknown or archived space (PUR-Q13) | PUR-R14, PUR-R15, PUR-R16, PUR-R17 |
| POST | `/operator/spaces/<space_id>/archive` | Operator | none | 303 `/operator/spaces`, flash "Space archived". A repeat for a space already archived answers the same and leaves `archived_at` unchanged | "Cancel its upcoming bookings first: BK-7KQ2M9" while a held or confirmed booking ends after now; the flash names each one, a held one as "BK-7KQ2M9 (held)" | PUR-R16 |
| GET | `/operator/members` | Operator | none | 200 Members with email, display name, `plan_active` and a plan button; markup in section 11. The list always holds at least the Operator | | PUR-R06, PUR-R19 |
| POST | `/operator/members/<member_id>/plan` | Operator | `plan_active` (`true` or `false`, required): the button sends the target value, `true` when the plan is off and `false` when it is on | 303 `/operator/members`, flash "Plan on for b@example.com" or "Plan off for b@example.com", the latter followed by "; upcoming plan bookings: BK-3MZ8QT" when the Member has upcoming confirmed plan bookings (they stay plan, D10). A repeat (a double click) changes nothing. Only new bookings see it | 404 unknown Member or non-operator; a missing or other `plan_active` value gets 303 with "Plan must be true or false" and no change; there is no toggle | PUR-R06, PUR-R19 |
| GET | `/dashboard` | Operator | none | 200 last 7 Bangkok days: bookings by status, utilization "1.0%", counted members, confirmed hours by coverage, with the markers of section 11, and a "Reconcile all held" button (its form sends `return_to=dashboard`). Stored statuses: a lapsed hold counts as held until reconciled. No money. With no non-archived space: utilization "0.0%" and every figure 0, never a 500 | 404 for a non-operator | PUR-R06, PUR-R34 |
| POST | `/_test/clock` | e2e only | JSON `{"now": "<ISO with offset>"}` or `{"now": null}` | 200 `{"now": ...}` when `TEST_CLOCK_ENABLED` is exactly `true` | 404 otherwise; 400 for a `now` without an offset; nothing stored. With the flag off `clock.now()` never reads `test_clock` | PUR-R38 |

## 5. Booking by form: outcomes

### 5.1 The request

`POST /spaces/<space_id>/book` builds the start instant from `date` + `start` in Bangkok time (PUR-R07). The name and email come from the logged-in Member, never from a field (PUR-R05). The price and coverage are set here and never change (PUR-R17, PUR-R19). A field `amount_satang` or any other extra field is ignored.

### 5.2 Checks, in this order (PUR-R39)

1. Shape (400 in JSON, a flash on the form): a time with an offset, block alignment, duration, opening hours.
2. The Member's own lapsed holds are reconciled; then the Member's own match: a slot-blocking hold of the same space, start and blocks is resumed (from its payment deadline: "Time to pay has run out", PUR-R40), and a confirmed booking of the same space, start and blocks answers with that booking (PUR-R21). A match answers with the stored booking and its stored values (party size, note), so the checks below never refuse it.
3. Notice, horizon, party size, note length (400 in JSON, a flash on the form).
4. A remaining slot-blocking held booking refuses any other request (409).
5. The pre-insert sweep reconciles stale holds of the space; a lapsed hold that overlaps the slot and could not be reconciled answers 503, unless a slot-blocking booking also overlaps (409 wins). Then the check and insert run in one transaction that first locks the Member's row (`SELECT ... FOR UPDATE`) and runs steps 2 and 4 again, so two requests from one Member never make two holds; the partial EXCLUDE constraint catches a race (409), and a violation by the Member's own matching hold answers as a resume (PUR-R22, PUR-R39).

### 5.3 Results

| Case | Form answer | JSON answer (`POST /api/bookings`) | Rule |
|---|---|---|---|
| New pay booking | 303 to `PAYMENT_PUBLIC_URL/pay/<stored session id>`, never to the `url` in Payment's answer | 201, `status` held, `payment_url` set, built the same way | PUR-R21, PUR-R23 |
| Same space, start and blocks while the hold blocks (double submit, or "Continue to payment" on the booking page) | 303 to the same hosted page. The hold keeps its stored party size and note; to change them, cancel and book again | 200, the same booking and `payment_url` | PUR-R21 |
| New plan or free booking | 303 `/bookings/<ref>`; page "Confirmed. Covered by your plan. No payment was taken." (plan) or "Confirmed. This space is free. No payment was taken." (free) | 201, `status` confirmed, `payment_url` null | PUR-R20 |
| Same space, start and blocks as your own confirmed booking (double submit of Book, or Back after paying) | 303 `/bookings/<ref>` of that booking, flash "You already booked this slot" | 200, that booking; nothing new, no second grant request | PUR-R21, PUR-R39 |
| Input error | 303 `/spaces/<space_id>?date=…&blocks=…` with the JSON message as the flash. One flash differs: a misaligned start gets "Pick a start on the half hour" (JSON: "Start on :00 or :30") | 400 `invalid_request` | PUR-R07 to PUR-R10, PUR-R14 |
| Anonymous | 303 `/login`, "Log in to book"; after login, back to `/spaces/<space_id>?date=…&blocks=…` | 401 | PUR-R05 |
| Unknown or archived space | 404 | 404 | PUR-R16 |
| Another held booking blocks | 303 back to the space page, "Finish or cancel your held booking BK-7KQ2M9 first" (from that hold's payment deadline: "Time to pay has run out on BK-7KQ2M9; cancel it, or try again from 10:15") | 409 `held_booking_exists` | PUR-R39, PUR-R40 |
| Slot taken | 303 back, "Slot just taken" | 409 `slot_taken` | PUR-R12, PUR-R22 |
| A lapsed hold that overlaps the slot could not be reconciled (Payment unreachable), and no slot-blocking booking overlaps | 303 back, "Payment is not reachable. Please try again." | 503 `payment_unreachable`; nothing changes. A lapsed hold that does not overlap never blocks the insert | PUR-R22 |
| A resume's repeated create (or read) answers paid (paid in another tab, not yet read) | Confirm the booking (D14), request the grant, then 303 `/bookings/<ref>`; never the hosted page | 200, the booking, `status` confirmed | PUR-R21, PUR-R25 |
| A resume's repeated create answers expired before the payment deadline (an earlier held cancel reached Payment, but its answer was lost) | No redirect to Payment: 303 `/bookings/<ref>`, "Time to pay has run out; cancel this hold or book again from 10:15"; the page shows the from-deadline state | 409 `payment_time_over` | PUR-R21, PUR-R40 |
| Session create unreachable after the insert, or on a resume | 303 `/bookings/<ref>`, "Payment is not reachable. Please try again."; booking held, without a session if it had none | 503 `payment_unreachable` | PUR-R23, PUR-Q09 |
| Same slot after the payment deadline (`hold_expires_at` - 2 min, with or without a session), before `hold_expires_at` | 303 `/bookings/<ref>`, "Time to pay has run out; cancel this hold or book again from 10:15" | 409 `payment_time_over`; no Payment call | PUR-R40 |

### 5.4 Cancel

`GET /bookings/<ref>/cancel` shows the refund before the person confirms (PUR-R30, PUR-R31). For a held booking it first runs the reconcile read (PUR-R24): a paid session confirms the booking, and the screen then shows the confirmed text. The screen first names the booking, "Cancel BK-7KQ2M9, Meeting Room A 2026-10-07 09:00-10:30", plus "Member A (a@example.com)" for an operator; its button reads "Cancel booking", and a "Keep booking" link goes back to `/bookings/<ref>`.

| Booking | Screen text |
|---|---|
| Confirmed, pay, 24 h or more before start (or any operator cancel before the end) | "Refund THB 450.00 (100%)" |
| Confirmed, pay, under 24 h (Member) | "Refund THB 0.00 (0%)" |
| Confirmed, plan or free | "No payment was taken" |
| Held, read unpaid, before the payment deadline | "We have not received a payment for this booking. Cancelling closes the payment page. If a payment completes first, the refund is THB 450.00 (100%)." Under 24 h before start (Member): "... the refund is THB 0.00 (0%): the start is less than 24 h away." An operator always sees "THB 450.00 (100%)" before the end. `shown_refund_satang` is that amount |
| Held, Payment gave no answer, before the payment deadline | "We could not check your payment just now. If a payment completes or went through, the refund is THB 450.00 (100%)." (or "THB 0.00 (0%): the start is less than 24 h away."), never "We have not received a payment"; `shown_refund_satang` is that amount |
| Held, read unpaid, from the payment deadline | "Nothing was charged. Cancel this hold?": no payment can complete; `shown_refund_satang` 0 |
| Held, Payment gave no answer, from the payment deadline | "Payment status unknown. If a payment went through, the refund is THB 450.00 (100%)." (or "THB 0.00 (0%): the start is less than 24 h away."), never "Nothing was charged"; `shown_refund_satang` is that amount |
| Held, no session (PUR-Q09), at any time | "Payment was not started. Nothing was charged. Cancel this hold?"; `shown_refund_satang` 0; the POST makes no Payment call |

Every confirm form carries `shown_refund_satang`: the amount the screen names (for a held booking, the PUR-R30 amount for that caller where a sentence names one, as in the table; 0 for plan and free and for "Nothing was charged"). The POST checks it before any Payment or Access call (PUR-R30). A held cancel stores `cancel_reason` member_cancel or operator_cancel, after the caller (PUR-R31).

`POST /bookings/<ref>/cancel` (and the operator route) answers:

| Case | Form answer | JSON answer (`POST /api/bookings/<ref>/cancel`) | Rule |
|---|---|---|---|
| Confirmed, allowed | 303, "Booking cancelled"; the page shows the follow-up state | 200 booking, `status` cancelled | PUR-R30, PUR-R32 |
| The policy now gives less than `shown_refund_satang` (the 24 h line passed between the screen and the POST), held or confirmed; checked before any Payment or Access call, so a held session is not expired | 303 `/bookings/<ref>/cancel`, "The refund is now THB 0.00 (0%): the start is less than 24 h away. Confirm again."; nothing cancelled | No check: JSON sends no shown amount | PUR-R30, PUR-R31 |
| `shown_refund_satang` missing, empty or not an integer | 303 `/bookings/<ref>/cancel`, "Please confirm the refund again."; nothing cancelled, no call | Not applicable | PUR-R30 |
| Held, session unpaid, inside the hold | 303, "Booking cancelled" | 200, `status` cancelled, `refund_amount_satang` 0 | PUR-R31 |
| Held, the expire answer says paid | 303, "Booking cancelled"; refund per policy | 200, `status` cancelled | PUR-R31 |
| Held, hold lapsed, unpaid | 303 `/bookings/<ref>`, "This hold has already expired" | 409 `hold_expired`; `status` is now expired | PUR-R31 |
| Already expired | 303 `/bookings/<ref>`, "This hold has already expired"; no call | 409 `hold_expired` | PUR-R28 |
| Held, Payment unreachable | 303, "Payment is not reachable. Please try again." | 503 `payment_unreachable`; still held | PUR-R31 |
| Member, at or after start | 303 `/bookings/<ref>`, "This booking has started; ask the operator" | 409 `booking_started` | PUR-R30 |
| Operator, at or after end | 303 `/bookings/<ref>`, "This booking has ended" | 409 `booking_ended` | PUR-R30 |
| Already cancelled (repeat) | 303 `/bookings/<ref>`, "This booking is already cancelled"; pending follow-ups retried, nothing new sent | 200, the stored booking; pending follow-ups retried, nothing new sent | PUR-R28, PUR-R32 |
| Not yours | 404 | 404 | PUR-R05 |

The policy follows the caller: an operator caller gets the operator policy on either route (D18), on any booking, the Operator's own included (accepted). The operator reaches the confirm screen from the Cancel link in the all-bookings list; there is no one-click cancel. The POST checks, in this order on both routes: ownership (404), already cancelled or expired, started (Member) or ended (operator), the `shown_refund_satang` comparison, then the calls (PUR-R30). The two shown-refund refusals answer 303 `/bookings/<ref>/cancel` on both routes; every other refusal answers 303 `/bookings/<ref>` with the flash above, and so does the GET confirm screen for a booking that cannot be cancelled.

After a confirmed cancel the booking page shows the cancelled lines of section 7, built from the stored follow-up fields (PUR-R32, PUR-R33). The refund is sent only after the revoke succeeded (PUR-Q10).

## 6. Start-block grid (page and JSON)

For one space, one date and one duration, the grid lists every start from 08:00 to 19:30 (24 starts). A start is available only if all its blocks are inside opening hours, past the notice period and free of slot-blocking bookings. Otherwise the first reason that applies wins: "Too soon", then "Runs past 20:00", then "Booked" (PUR-R13). The end is exclusive, so a start right at another booking's end is available (PUR-R11).

The date and the duration sit in their own GET form to `/spaces/<space_id>` with a "Show starts" button (an optional onchange submit), so a changed date is never booked from the old grid. Page markup, inside the booking form that posts to `/spaces/<space_id>/book` with hidden `date` and `blocks` (the ones the grid was built for) and the fields `party_size` and `note`. A start is a choice, not a submit: pressing it creates nothing. Each start's label shows its full range for the chosen duration ("09:00-10:30"), so the chosen radio is the time review; a greyed start also shows its reason as text ("19:00-20:30 (Runs past 20:00)"), because a title shows no tooltip on a phone and is not reliably read aloud.

```html
<label><input type="radio" name="start" value="09:00"> 09:00-10:30</label>
<label title="Runs past 20:00"><input type="radio" name="start" value="19:00" disabled title="Runs past 20:00"> 19:00-20:30 (Runs past 20:00)</label>
<p>Meeting Room A, 2026-10-07, 1 h 30 min: THB 450.00</p>
<p>Full refund if you cancel 24 h or more before the start; after that, no refund.</p>
<button type="submit">Continue to payment</button>
```

Under the grid and the party size and note fields, one review line shows the room, date, duration and price, e.g. "Meeting Room A, 2026-10-07, 1 h 30 min: THB 450.00", or for Member B "THB 450.00, covered by your plan. No payment needed." (PUR-R17, PUR-R19). For pay coverage the cancellation terms follow: "Full refund if you cancel 24 h or more before the start; after that, no refund." (PUR-R30). When the grid date is today or tomorrow, the server adds "Starts less than 24 h away cannot be refunded." The review line always shows the date and duration the grid was built for. The one submit button reads "Continue to payment" for pay coverage and "Book" for plan and free; for an anonymous visitor the review shows pay coverage (free coverage when the rate is 0: "THB 0.00", no refund line) and the button reads "Log in to book" (PUR-R05). A post with no start picked goes back to the space page with the flash "Pick a start time".

When no start is free: "No free start on this date. Try another date or a shorter duration." When the logged-in Member has a slot-blocking held booking, a banner on this page and on My bookings says "You have a held booking BK-7KQ2M9, Meeting Room A 2026-10-07 09:00-10:30: pay by 10:13 or cancel it" and links to `/bookings/BK-7KQ2M9` (PUR-R39). From the payment deadline it says "Time to pay has run out on BK-7KQ2M9; cancel it, or try again from 10:15" (PUR-R40). The grid shows that hold's own starts as "Booked"; the booking page resumes it.

The JSON form is `GET /api/spaces/<space_id>/availability?date=2026-10-07&blocks=3` (section 8.2).

## 7. Booking page

`GET /bookings/<ref>` shows status, reference, times, price, coverage, payment outcome, the party size ("Party of 4"), the note and the e-ticket link (PUR-R05). An operator also sees the Member's display name and email, and on a cancelled booking "Cancelled 2026-10-05 11:00" (cancelled_at). "View e-ticket" opens the e-ticket in a new tab (`target="_blank" rel="noopener"`), so the booking page stays open. "Continue to payment" and "Book again" show only to the booking's own Member: an operator viewing another Member's held booking sees the deadline, Cancel and Reconcile, and an operator's Retry posts to `/operator/bookings/<ref>/retry` (PUR-R05, PUR-R33). An operator's Retry and Reconcile forms on this page send hidden `return_to=booking`, and its Cancel link is `/bookings/<ref>/cancel?return_to=booking`, so the answer comes back to this page (section 4). For its own Member a Retry button (`POST /bookings/<ref>/retry`) shows beside each pending line ("Your e-ticket is being prepared", "Your ticket is still being cancelled; any refund follows.", "Refund of THB 450.00 pending."), never beside "Refund failed. The operator will follow up."; for an operator it also shows beside "Refund failed". "Book again" links to the booking's own date and blocks only while that date is from today to today + 30 and the space is not archived; otherwise it links to `/spaces/<space_id>` (today) while the space exists, or to `/` once it is archived. The held states apply in this order (PUR-R40): (1) from the payment deadline "Continue to payment" is never shown; (2) "Nothing was charged" shows only after a read returned unpaid, or when there is no session; (3) a read with no answer at or after the deadline shows "Payment status unknown. If you paid before 10:13, your booking will be confirmed: refresh in a minute." and Cancel, never "Time to pay has run out". States:

| Booking | Page |
|---|---|
| Held, payment open | "Pay by 10:13 (N min left)" with `data-seconds-left` (the seconds from `clock.now()` to the payment deadline, `hold_expires_at` - 2 min; N is the whole minutes rounded up, and under 60 s it reads "less than 1 min left"); a few lines of inline script count down and at zero hide "Continue to payment" and show "Time to pay has run out"; without script the server decides on reload. "Continue to payment" (posts the same fields to `/spaces/<space_id>/book`) and Cancel (PUR-R21, PUR-R40) |
| Held, payment not started (session create got no answer), before the deadline | "Payment could not be started. Pay by 10:13 (N min left)", with `data-seconds-left` and the same countdown script, "Continue to payment" (retries the session create) and Cancel (PUR-Q09) |
| Held, Payment gave no answer on the read, before the deadline | "Pay by 10:13 (N min left)" stays, beside "Payment status unknown, refresh later. If you already paid, refresh in a minute; otherwise Continue to payment by 10:13."; "Continue to payment" stays (a repeat create returns the paid session, PMT-R03) and Cancel (PUR-R24) |
| Held, from the payment deadline to `hold_expires_at`, read unpaid or no session | "Time to pay has run out. Nothing was charged. Cancel this hold to book again now, or it ends at 10:15." and Cancel; no countdown and no "Continue to payment" (PUR-R40) |
| Held, from the payment deadline, read with no answer | "Payment status unknown. If you paid before 10:13, your booking will be confirmed: refresh in a minute." and Cancel; no "Continue to payment", no "Nothing was charged" and no "Time to pay has run out" (PUR-R40) |
| Confirmed, pay, before the start | "Confirmed. Paid THB 450.00." on every visit; for the owner "Cancel by 2026-10-06 09:00 for a full refund." (or "No refund if you cancel: the start is less than 24 h away."), and for an operator viewer "Operator cancel: full refund until 10:30"; "View e-ticket" linking `ticket_url` or "Your e-ticket is being prepared" (PUR-R26, PUR-R30) |
| Confirmed, plan or free, before the start | Page text on every visit: "Confirmed. Covered by your plan. No payment was taken." or "Confirmed. This space is free. No payment was taken."; "You can cancel until the start." with no refund line; "View e-ticket" or "Your e-ticket is being prepared" (PUR-R20, PUR-R30) |
| Confirmed, started (start at or before now, now before the end) | An operator viewer sees Cancel and "Operator cancel: full refund until 10:30" until the end. No Cancel and no refund line for the owner: "This booking has started; ask the operator". v1 has no contact channel in the app; door Staff phone the Operator, who is on call through opening hours (PRD section 3). "View e-ticket" stays (PUR-R30) |
| Confirmed, end passed | "Completed" and "View e-ticket"; no Cancel (PUR-R28) |
| Expired, nothing paid | "Not paid in time. The hold ended at 10:15 and nothing was charged." and, for its own Member, "Book again" linking `/spaces/1?date=2026-10-07&blocks=3` (PUR-R24) |
| Expired, a payment landed after the hold (slot_unavailable) | "Paid after the hold ended.", then the refund line of the table below ("THB 450.00 refunded." only once refund_status is succeeded); "Book again" for its own Member (PUR-R25) |
| Cancelled | The lines of the table below, from stored fields. "View e-ticket" shows when grant_status is revoked and ticket_url is set, and opens the CANCELLED ticket; it is hidden while the revoke is pending, and a booking that never had a grant (ticket_url null: a held cancel that raced a payment, or a cancel while the grant was being prepared) shows no ticket link. "Book again" shows to its own Member only on a booking cancelled while held (payment_status unpaid), linking `/spaces/<space_id>?date=...&blocks=...` like the Expired row (PUR-R28, PUR-R32, PUR-R33) |

Cancelled and refund lines. The page builds them from stored fields, in this order, and shows only the lines that apply; a line never says "refunded" before refund_status is succeeded, and the amount is `refund_amount_satang` (an amount_mismatch refund returns the collected amount, PUR-R25):

| Line | Stored fields | Text |
|---|---|---|
| 1 | cancelled from confirmed, `cancel_reason` member_cancel | "Cancelled." |
| 1 | cancelled from confirmed, `cancel_reason` operator_cancel | "Cancelled by the operator." |
| 1 | `cancel_reason` amount_mismatch | "Cancelled: the payment did not match." |
| 1 | cancelled while held (`payment_status` unpaid), member_cancel | "Cancelled before payment. Nothing was charged." |
| 1 | cancelled while held, operator_cancel | "Cancelled by the operator before payment. Nothing was charged." |
| 2 | coverage plan or free | "No payment was taken." |
| 2 | coverage pay, paid, `refund_amount_satang` 0 (a 0% Member cancel) | "No refund: cancelled less than 24 h before the start." |
| 2 | `refund_amount_satang` above 0, `refund_status` succeeded | "THB 450.00 refunded." |
| 2 | `refund_amount_satang` above 0, `refund_status` pending (also while it waits for the revoke) | "Refund of THB 450.00 pending." |
| 2 | `refund_amount_satang` above 0, `refund_status` failed | "Refund failed. The operator will follow up." |
| 3 | `grant_status` revoke_pending | "Your ticket is still being cancelled; any refund follows." |

A booking cancelled while held has no line 2. The Operator's list flags the same states "Revocation pending", "Refund pending" and "Refund failed" (section 4). The slot_unavailable Expired row uses the line-2 texts.

A GET may change a booking only by the idempotent sync of D13, D19 and D20: reconcile, pending grant, pending revoke, pending refund. Nothing in the request steers it (PUR-R37).

## 8. JSON API

All JSON routes use the `purchase_session` cookie. Space reads need no login.

v1 has no JSON route for sign-up, login, operator work, plans, the dashboard or a refund retry; their rule rows are form rows on the routes of section 4. An unknown `/api/...` path gets 404 `not_found`, logged in or not (PUR-R06, PUR-R33, PUR-R34).

### 8.1 GET /api/spaces

200 with non-archived spaces (PUR-R15, PUR-R16):

```json
{"spaces": [
  {"space_id": 1, "name": "Meeting Room A", "capacity": 6, "hourly_rate_satang": 30000, "block_price_satang": 15000, "currency": "THB"},
  {"space_id": 4, "name": "Community Table", "capacity": 8, "hourly_rate_satang": 0, "block_price_satang": 0, "currency": "THB"}
]}
```

### 8.2 GET /api/spaces/{space_id}/availability

Query `date` (YYYY-MM-DD, required) and `blocks` (1-8, required). Answers: 200 grid; 400 `invalid_request` for a date outside today to today + 30 or bad blocks; 404 unknown or archived space (PUR-R13).

```json
{"space_id": 1, "date": "2026-10-07", "blocks": 3, "price_satang": 45000, "currency": "THB",
 "starts": [
   {"start": "2026-10-07T08:00:00+07:00", "available": false, "reason": "Booked"},
   {"start": "2026-10-07T10:30:00+07:00", "available": true, "reason": null},
   {"start": "2026-10-07T19:00:00+07:00", "available": false, "reason": "Runs past 20:00"}
 ]}
```

Three of the 24 entries, with BK-7KQ2M9 confirmed 09:00-10:30: 08:00 to 10:00 are Booked (3 blocks from 08:00 run to 09:30), 10:30 is free (the end is exclusive), 19:00 and 19:30 run past 20:00. The real answer always has 24 entries (the full example is in `openapi/purchase.yaml`). The grid is a read: it never reconciles and never holds a slot (PUR-R13).

### 8.3 POST /api/bookings

Request:

| Field | Type | Required | Constraints | Rule |
|---|---|---|---|---|
| `space_id` | integer | yes | A non-archived space, else 404 | PUR-R16 |
| `start` | string | yes | ISO 8601 with an offset; exactly :00 or :30 Bangkok time, no seconds; at least 60 min after now; date at most today + 30; inside 08:00-20:00 with the end. It may equal another booking's end: no gap is kept | PUR-R07, PUR-R08, PUR-R09, PUR-R10, PUR-R11 |
| `blocks` | integer | yes | 1 to 8 | PUR-R09 |
| `party_size` | integer | yes | 1 to the space's capacity | PUR-R14 |
| `note` | string | no | Free text for the Member and the operator, at most 500 characters; longer gets 400 "Note must be at most 500 characters" (the form: the same flash) | PUR-R14 |

Responses: 201 new booking; 200 resumed hold (same Member, space, start and blocks); 400; 401; 404; 409 `held_booking_exists`, `slot_taken` or `payment_time_over`; 503 `payment_unreachable` (section 5.3).

Idempotency: no header. A repeat of the same space, start and blocks by the same Member resumes the slot-blocking hold, with the same reference, `payment_url`, party size and note (PUR-R21, ADR-0014); the match runs before the notice, horizon and party-size checks (section 5.2). A repeat of the same space, start and blocks as the Member's own confirmed booking (a plan or free double submit, or Back after paying) answers 200 with that booking; nothing new is created and no grant is requested again (PUR-R21).

### 8.4 GET /api/bookings/{ref}

Owner only. 200 booking object; 401 anonymous; 404 not yours or unknown (same body). Any unknown reference, well-formed or not (a lower-case `bk-7kq2m9` too), gets 404, never 400. Reconciles a held booking and retries pending follow-ups first, like the page (PUR-R05, PUR-R24).

### 8.5 GET /api/bookings/mine

Member only. 200 `{"bookings": [<booking object>, ...]}`, ordered by start. Reconciles held bookings first (PUR-R24, PUR-R28).

### 8.6 POST /api/bookings/{ref}/cancel

Owner only. Empty body or `{}`. Answers in section 5.4.

### 8.7 Booking object

The pinned fields, plus `completed`, `currency`, `note`, `payment_url` and `refund_reason`, which the rules need (PUR-R18, PUR-R28, PUR-T34).

| Field | Type | Values | Rule |
|---|---|---|---|
| `reference` | string | `BK-7KQ2M9` | PUR-R29 |
| `status` | string | `held`, `confirmed`, `expired`, `cancelled` | PUR-R28 |
| `completed` | boolean | true when confirmed and now is at or after the end | PUR-R28 |
| `space_id`, `space_name` | integer, string | 1, "Meeting Room A" | |
| `start`, `end` | string | ISO, `+07:00`; the end is exclusive | PUR-R07, PUR-R09 |
| `blocks`, `party_size` | integer | 3, 4 | PUR-R09, PUR-R14 |
| `note` | string or null | as sent | |
| `agreed_price_satang` | integer | 45000 | PUR-R17 |
| `currency` | string | `THB` | PUR-R18 |
| `coverage` | string | `pay`, `plan`, `free` | PUR-R19 |
| `hold_expires_at` | string or null | set for pay bookings at creation and kept after the hold ends; null for plan and free | PUR-R21 |
| `payment_session_id` | string or null | `ps_…` | PUR-R23 |
| `payment_url` | string or null | the hosted page; only while held with a session | PUR-R23 |
| `payment_status` | string | `not_required` (plan, free), `unpaid`, `paid`: the last payment status Purchase read. This is PUR-T34 payment_outcome under its JSON name; Payment's own payment_status (PMT-T04) has no `not_required` | PUR-R24, PUR-T34 |
| `cancel_reason` | string or null | `member_cancel`, `operator_cancel`, `amount_mismatch` | PUR-R25, PUR-R32 |
| `refund_reason` | string or null | the same, or `slot_unavailable` | PUR-R25, PUR-R32 |
| `refund_amount_satang` | integer or null | null until cancel; then 0 or the full price | PUR-R30 |
| `refund_status` | string | `none`, `pending`, `succeeded`, `failed`. A 0% cancel and a plan or free cancel keep `none` | PUR-R32, PUR-R33 |
| `refund_attempt` | integer or null | 1, 2, …; null while `refund_status` is `none` | PUR-R33 |
| `grant_status` | string | `not_requested`, `pending`, `issued`, `revoke_pending`, `revoked` | PUR-R26, PUR-R32 |
| `ticket_url` | string or null | the Access e-ticket link, once issued | PUR-R26 |

## 9. Examples

Standard data: clock 2026-10-05 10:00 Bangkok; Member A logged in (cookie `purchase_session`); Meeting Room A, space_id 1, THB 300 per hour.

### 9.1 Success

```http
POST /api/bookings
Cookie: purchase_session=...
Content-Type: application/json

{"space_id": 1, "start": "2026-10-07T09:00:00+07:00", "blocks": 3, "party_size": 4, "note": "Team planning"}
```

```http
HTTP/1.1 201 Created

{"reference": "BK-7KQ2M9", "status": "held", "completed": false, "space_id": 1,
 "space_name": "Meeting Room A", "start": "2026-10-07T09:00:00+07:00",
 "end": "2026-10-07T10:30:00+07:00", "blocks": 3, "party_size": 4, "note": "Team planning",
 "agreed_price_satang": 45000, "currency": "THB", "coverage": "pay",
 "hold_expires_at": "2026-10-05T10:15:00+07:00",
 "payment_session_id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "payment_url": "http://localhost:8002/pay/ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "payment_status": "unpaid", "cancel_reason": null, "refund_reason": null,
 "refund_amount_satang": null, "refund_status": "none", "refund_attempt": null,
 "grant_status": "not_requested", "ticket_url": null}
```

After Member A pays and the browser returns, `GET /api/bookings/BK-7KQ2M9` shows `"status": "confirmed"`, `"payment_status": "paid"`, `"payment_url": null`, `"grant_status": "issued"` and `"ticket_url": "http://localhost:8003/t/q3Vt9XbLk2Rm8PzW4nHc7A"`.

### 9.2 Invalid input

```http
POST /api/bookings

{"space_id": 1, "start": "2026-10-07T09:00:00", "blocks": 3, "party_size": 4}
```

```http
HTTP/1.1 400 Bad Request

{"error": {"code": "invalid_request", "message": "Time needs an offset, for example +07:00"}}
```

A misaligned start `2026-10-07T09:15:00+07:00` gets "Start on :00 or :30". `2026-10-07T19:30:00+07:00` for 2 blocks gets "Outside opening hours 08:00-20:00". The same post from the form redirects back to the space page with the message as a flash, except the misaligned start, whose flash is "Pick a start on the half hour" (PUR-R09).

### 9.3 Failure

Member B asks for 10:00-11:00 while BK-7KQ2M9 is confirmed for 09:00-10:30 (PUR-R12):

```http
HTTP/1.1 409 Conflict

{"error": {"code": "slot_taken", "message": "Slot just taken"}}
```

Payment does not answer within 5 s when the session is created (PUR-R23):

```http
HTTP/1.1 503 Service Unavailable

{"error": {"code": "payment_unreachable", "message": "Payment is not reachable. Please try again."}}
```

BK-7KQ2M9 stays held with no `payment_session_id`. The same request later resumes it and creates the session.

Member A cancels 2026-10-07 at 09:00, the start (PUR-R30):

```http
HTTP/1.1 409 Conflict

{"error": {"code": "booking_started", "message": "This booking has started; ask the operator"}}
```

### 9.4 Repeat

At 10:05 Member A sends the 9.1 body again (double submit, PUR-R21):

```http
HTTP/1.1 200 OK

{"reference": "BK-7KQ2M9", "status": "held", "completed": false, "space_id": 1,
 "space_name": "Meeting Room A", "start": "2026-10-07T09:00:00+07:00",
 "end": "2026-10-07T10:30:00+07:00", "blocks": 3, "party_size": 4, "note": "Team planning",
 "agreed_price_satang": 45000, "currency": "THB", "coverage": "pay",
 "hold_expires_at": "2026-10-05T10:15:00+07:00",
 "payment_session_id": "ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "payment_url": "http://localhost:8002/pay/ps_Q7mZ3xK9vT2bN8rL4wYc1A",
 "payment_status": "unpaid", "cancel_reason": null, "refund_reason": null,
 "refund_amount_satang": null, "refund_status": "none", "refund_attempt": null,
 "grant_status": "not_requested", "ticket_url": null}
```

No second booking and no second session: Payment returns the stored session for the same reference (PMT-R03).

At 10:05 Member A asks for Focus Pod 1 instead (PUR-R39):

```http
HTTP/1.1 409 Conflict

{"error": {"code": "held_booking_exists", "message": "Finish or cancel your held booking BK-7KQ2M9 first"}}
```

Cancel twice: the second `POST /api/bookings/BK-7KQ2M9/cancel` answers 200 with the stored cancelled booking. No second revoke or refund is started; only pending steps are retried.

### 9.5 Coverage skips collection

**No call is made to Payment.** Member B (plan_active true) books (PUR-R19, PUR-R20):

```http
POST /api/bookings

{"space_id": 1, "start": "2026-10-07T10:30:00+07:00", "blocks": 2, "party_size": 2}
```

```http
HTTP/1.1 201 Created

{"reference": "BK-3MZ8QT", "status": "confirmed", "completed": false, "space_id": 1,
 "space_name": "Meeting Room A", "start": "2026-10-07T10:30:00+07:00",
 "end": "2026-10-07T11:30:00+07:00", "blocks": 2, "party_size": 2, "note": null,
 "agreed_price_satang": 30000, "currency": "THB", "coverage": "plan",
 "hold_expires_at": null, "payment_session_id": null, "payment_url": null,
 "payment_status": "not_required", "cancel_reason": null, "refund_reason": null,
 "refund_amount_satang": null, "refund_status": "none", "refund_attempt": null,
 "grant_status": "issued", "ticket_url": "http://localhost:8003/t/Zk7Pq2Wn9Tb4Xm6Rc3Lv8H"}
```

The price is stored but never collected. The only outbound call was POST /grants to Access; its answer is the grant of the Access contract's example, ticket code M4TR-8WCE ([purchase-access.md](purchase-access.md), section 5.5). A Community Table booking (THB 0 per hour) gets `"coverage": "free"`, `"agreed_price_satang": 0` and the same shape. A later cancel refunds 0, calls only Access to revoke, and says "No payment was taken" (PUR-R30).

## 10. Timeouts and retries (outbound)

Purchase is the only caller (PUR-R35, ADR-0004):

- Every call to Payment or Access uses `timeout=5` seconds, a bearer token, and the internal URL.
- A timeout, a connection error or a 5xx answer means "unreachable". No result is stored.
- No call runs while a database transaction is open. Purchase commits, calls, then stores the answer in a new short transaction.
- One attempt per call inside a request. No loops or sleeps. The next touch retries:
  - the reconcile read of a held booking (D13) at the reconcile points of PUR-R24: the booking page and its return URL, `GET /api/bookings/<ref>`, My bookings (the page and `GET /api/bookings/mine`), the cancel confirm screen, the pre-insert sweep, the Member's own lapsed holds (PUR-R39), and the Operator's Reconcile of one booking or of all held; the POST cancel uses expire in place of the GET (PUR-R31). "Reconcile all held" and the sweep stop calling Payment after the first read with no answer;
  - a pending grant, revoke or refund (D19, D20) at the retry points: the booking page (the page or `GET /api/bookings/<ref>`), the owner's Retry (`POST /bookings/<ref>/retry`) and the Operator's Retry (`POST /operator/bookings/<ref>/retry`, PUR-R26, PUR-R32).
- Any other 4xx answer is a Purchase or config defect: Purchase logs the operation, the booking reference and the status (never the token), stores nothing, and handles the step like "unreachable" (PUR-R35); an operator's Retry or Reconcile flashes the status instead, for example "Payment refused the call (401): check the service settings and the Purchase log.", so a mismatched token shows as a settings fault.
- What the Member sees:
  - A read that cannot reach Payment changes nothing and shows "Payment status unknown, refresh later" (from the payment deadline "Payment status unknown. If you paid before 10:13, your booking will be confirmed: refresh in a minute.").
  - A request that needs Payment's answer (the conflicting insert, a held cancel, session create) gets 503 or the flash "Payment is not reachable. Please try again.".
  - Access unreachable during issuance: "Your e-ticket is being prepared". During a revoke: "Your ticket is still being cancelled; any refund follows." (the Operator's list flags "Revocation pending"), and the refund waits.

## 11. Test-observable markers

| Where | Marker |
|---|---|
| `GET /spaces/<space_id>` | `<input type="radio" name="start" value="HH:MM">`; unavailable ones have `disabled` and a `title` with the reason (on the input and its label); the label text is the range, "09:00-10:30", plus "(reason)" when greyed |
| Every Purchase page | Flashed messages appear as text in the body once; tests match the text |
| `GET /bookings/<ref>/cancel` | `<form method="post" action="/bookings/<ref>/cancel">` (an operator's: `action="/operator/bookings/<ref>/cancel"`) holding `<input type="hidden" name="shown_refund_satang" value="45000">`; the value equals the amount the screen names |
| Booking state | Tests read `GET /api/bookings/<ref>` with the same cookie (it reconciles, so a race test that must not reconcile reads nothing in Purchase until its cancel, below) |
| `GET /bookings/<ref>` (held) | `data-seconds-left` on the countdown |
| `GET /operator/bookings` | Each row carries `data-booking-reference="BK-7KQ2M9"` and `data-flags` (space-separated: `being_prepared`, `revocation_pending`, `refund_pending`, `refund_failed`; empty when none); tests find their row by reference |
| `GET /operator/members` | Each Member row is `<form method="post" action="/operator/members/<member_id>/plan" data-member-email="b@example.com" data-plan-active="false">`; tests find the row by email |
| `GET /dashboard` | Always present, 0 when empty: `data-utilization="0.0104"` (the ratio, always 4 decimals, "0.0000"), `data-members="2"` (integer), `data-status-count-<status>="2"` for held, confirmed, expired and cancelled (integers), `data-hours-<coverage>="1.5"` for pay, plan and free (shortest decimal with no trailing zero: "2", "1.5", "0") (PUR-R34) |
| Payment hosted page | `data-decline-code` ([purchase-payment.md](purchase-payment.md), section 8) |
| Access e-ticket and kiosk | `data-ticket-code`, `data-status`, `data-result`, `data-selected-space-id`, `data-scan-result`, `data-scan-last4` ([purchase-access.md](purchase-access.md), section 8) |

Only these Purchase markers exist in contract-v1.

Test hooks: `POST /_test/clock` on all three services, 404 unless `TEST_CLOCK_ENABLED=true` (PUR-R38, PMT-R20, AXS-R19). The e2e suite sets the same instant on all three and follows the harness conventions in ARCHITECTURE.md ("The e2e harness"): unique Members and spaces per test, a fresh login after each clock move of 12 h or more past a login, or back before it (PUR-R03), and shared totals asserted as before-and-after deltas.

"Held cancel racing with payment" (PUR-R31 row 12) runs with no Purchase read between the pay and the cancel, because every read reconciles: (1) `POST /api/bookings` and keep `payment_url`; (2) `POST /pay/<id>` with 4242424242424242 and `allow_redirects=False`, never following `success_url`; (3) no Purchase read; (4) `POST /api/bookings/<ref>/cancel`. Expected: 200 with `status` cancelled, `refund_amount_satang` equal to the price, `refund_status` succeeded, `grant_status` revoked and `ticket_url` null; Access `GET /grants/<ref>` with the token answers `status` revoked and `ticket_code` null; Payment's collected and refunded each rise by the price. The form variant opens the confirm screen while unpaid, pays, then posts the form.

## 12. Versioning

- This draft is state proposed. At M4 a consumer-lens reviewer (browser and e2e) signs it off in `REVIEW_LOG.md`; then `cowork-booking-purchase` is tagged `contract-v1`, and this text moves to `cowork-booking-purchase/CONTRACT.md` with its `openapi.yaml` (ADR-0005). That extends the brief's layout, which names `CONTRACT.md` for Payment and Access only, so the Purchase contract also has one home.
- Any change after `contract-v1`, even an added field, goes through a CONTRACT_CHANGE_REQUEST and the tag `contract-v2`.
- Paths carry no version prefix. Consumers ignore JSON fields they do not know.

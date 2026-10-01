"""Purchase: members, spaces, availability, bookings, price, coverage and cancellation (CONTRACT.md)."""
import logging
import os
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

import psycopg
from flask import (Flask, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   session)
from psycopg.errors import ExclusionViolation, UniqueViolation
from psycopg.rows import dict_row
from werkzeug.security import check_password_hash, generate_password_hash

import access_client
import clock
import payment_client
from payment_client import CallFailed
from purchase import (BKK, BLOCK, HOLD, PAY_MARGIN, date_in_horizon, duration_text, fdate, grid,
                      hours_text, money, month, new_reference, price_satang, refund_policy, shape_error,
                      today, window_error)

log = logging.getLogger(__name__)
SESSION_HOURS = 12
DEMO_SPACES = [("Meeting Room A", 6, 30000), ("Focus Pod 1", 2, 2000), ("Board Room", 12, 100000),
               ("Community Table", 8, 0)]

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE IF NOT EXISTS members (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_operator BOOLEAN NOT NULL DEFAULT FALSE,
    plan_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS spaces (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity BETWEEN 1 AND 1000),
    hourly_rate_satang BIGINT NOT NULL
        CHECK (hourly_rate_satang = 0 OR hourly_rate_satang BETWEEN 2000 AND 1000000),
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS bookings (
    id BIGSERIAL PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    member_id BIGINT NOT NULL REFERENCES members (id),
    space_id BIGINT NOT NULL REFERENCES spaces (id),
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    blocks INTEGER NOT NULL CHECK (blocks BETWEEN 1 AND 8),
    party_size INTEGER NOT NULL,
    note TEXT,
    agreed_price_satang BIGINT NOT NULL,
    coverage TEXT NOT NULL CHECK (coverage IN ('pay', 'plan', 'free')),
    status TEXT NOT NULL CHECK (status IN ('held', 'confirmed', 'expired', 'cancelled')),
    hold_expires_at TIMESTAMPTZ,
    payment_session_id TEXT,
    payment_status TEXT NOT NULL CHECK (payment_status IN ('not_required', 'unpaid', 'paid')),
    cancel_reason TEXT,
    cancelled_at TIMESTAMPTZ,
    refund_reason TEXT,
    refund_amount_satang BIGINT,
    refund_status TEXT NOT NULL DEFAULT 'none',
    refund_attempt INTEGER,
    refund_requested_at TIMESTAMPTZ,
    grant_status TEXT NOT NULL DEFAULT 'not_requested',
    ticket_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bookings_no_overlap EXCLUDE USING gist
        (space_id WITH =, tstzrange(start_at, end_at) WITH &&) WHERE (status IN ('held', 'confirmed'))
);
CREATE TABLE IF NOT EXISTS test_clock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    now_override TIMESTAMPTZ
);
"""

BOOKING_SQL = """
SELECT b.*, s.name AS space_name, s.archived_at AS space_archived_at,
       m.email AS member_email, m.display_name AS member_name
FROM bookings b JOIN spaces s ON s.id = b.space_id JOIN members m ON m.id = b.member_id
"""
# Slot-blocking (D11): confirmed, or held with the hold still running at :now.
BLOCKING = "(b.status = 'confirmed' OR (b.status = 'held' AND b.hold_expires_at > %(now)s))"


class Refusal(Exception):
    def __init__(self, status: int, code: str, message: str, ref: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.ref = status, code, message, ref


def db() -> psycopg.Connection:
    return current_app.db


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).rstrip("/")


def hhmm(value: datetime) -> str:
    return value.astimezone(BKK).strftime("%H:%M")


def local_time(value: datetime | None) -> str:
    return value.astimezone(BKK).strftime("%Y-%m-%d %H:%M") if value else ""


def iso(value: datetime | None) -> str | None:
    return value.astimezone(BKK).isoformat() if value else None


def to_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,9}", value.strip()):
        return int(value)
    return None


def jerr(status: int, code: str, message: str):
    return jsonify(error={"code": code, "message": message}), status


def go(url: str, *messages: str):
    for m in messages:
        flash(m)
    return redirect(url, 303)


def deadline(b) -> datetime:
    return b["hold_expires_at"] - PAY_MARGIN


def pay_url(session_id: str) -> str:
    return f"{env('PAYMENT_PUBLIC_URL', 'http://localhost:8002')}/pay/{session_id}"


def fetch_booking(ref: str):
    return db().execute(BOOKING_SQL + " WHERE b.reference = %s", (ref,)).fetchone()


def booking_json(b) -> dict:
    now = clock.now()
    return {
        "reference": b["reference"], "status": b["status"],
        "completed": b["status"] == "confirmed" and now >= b["end_at"],
        "space_id": b["space_id"], "space_name": b["space_name"],
        "start": iso(b["start_at"]), "end": iso(b["end_at"]), "blocks": b["blocks"],
        "party_size": b["party_size"], "note": b["note"],
        "agreed_price_satang": b["agreed_price_satang"], "currency": "THB", "coverage": b["coverage"],
        "hold_expires_at": iso(b["hold_expires_at"]), "payment_session_id": b["payment_session_id"],
        "payment_url": pay_url(b["payment_session_id"])
        if b["status"] == "held" and b["payment_session_id"] else None,
        "payment_status": b["payment_status"], "cancel_reason": b["cancel_reason"],
        "refund_reason": b["refund_reason"], "refund_amount_satang": b["refund_amount_satang"],
        "refund_status": b["refund_status"], "refund_attempt": b["refund_attempt"],
        "grant_status": b["grant_status"], "ticket_url": b["ticket_url"],
    }


# --- sync with Payment and Access (D13, D14, D19, D20). Never inside an open transaction. ---------

def fulfil(b, s, issue: bool = True) -> str:
    """D14: confirm only a payment whose reference, amount and currency match; else cancel + full refund."""
    match = (s.get("booking_reference") == b["reference"] and s.get("currency") == "THB"
             and s.get("amount_satang") == b["agreed_price_satang"])
    with db().transaction():
        row = db().execute("SELECT status FROM bookings WHERE id = %s FOR UPDATE", (b["id"],)).fetchone()
        if row["status"] != "held":
            return "unchanged"
        if match:
            db().execute("UPDATE bookings SET status = 'confirmed', payment_status = 'paid', grant_status = %s "
                         "WHERE id = %s", ("pending" if issue else "not_requested", b["id"]))
        else:
            db().execute(
                "UPDATE bookings SET status = 'cancelled', cancel_reason = 'amount_mismatch', cancelled_at = %s, "
                "payment_status = 'paid', refund_reason = 'amount_mismatch', refund_amount_satang = %s, "
                "refund_status = 'pending', refund_attempt = 1, refund_requested_at = %s WHERE id = %s",
                (clock.now(), s.get("amount_satang"), clock.now(), b["id"]))
    # ponytail: a paid session on an already expired booking (slot_unavailable) cannot happen here, because
    # a booking is expired only after a read says unpaid past expires_at (D12); add it if that ever changes.
    run_followups(b["reference"])
    return "confirmed" if match else "cancelled"


def apply_session(b, s, now) -> str:
    if s.get("payment_status") == "paid":
        return fulfil(b, s)
    if now >= b["hold_expires_at"]:
        db().execute("UPDATE bookings SET status = 'expired' WHERE id = %s AND status = 'held'", (b["id"],))
        return "expired"
    return "unchanged"


def reconcile(b) -> str:
    """D13 for one booking. Raises CallFailed when Payment gives no usable answer."""
    if b["status"] != "held":
        return "unchanged"
    now = clock.now()
    if not b["payment_session_id"]:
        if now >= b["hold_expires_at"]:
            db().execute("UPDATE bookings SET status = 'expired' WHERE id = %s AND status = 'held'", (b["id"],))
            return "expired"
        return "unchanged"
    return apply_session(b, payment_client.get_session(b["payment_session_id"]), now)


def reconcile_many(rows) -> tuple[dict, set, CallFailed | None]:
    """Reconcile held bookings; stop calling Payment after the first failure. Returns counts, unknown refs."""
    counts = dict.fromkeys(("confirmed", "expired", "cancelled", "unchanged"), 0)
    unknown, failure = set(), None
    for b in rows:
        if failure and b["payment_session_id"]:
            counts["unchanged"] += 1
            unknown.add(b["reference"])
            continue
        try:
            counts[reconcile(b)] += 1
        except CallFailed as e:
            failure = e
            counts["unchanged"] += 1
            unknown.add(b["reference"])
    return counts, unknown, failure


def issue_grant(b) -> None:
    g_ = access_client.create_grant(b["reference"], str(b["member_id"]), b["space_id"], b["space_name"],
                                    b["start_at"].astimezone(BKK), b["end_at"].astimezone(BKK))
    if g_.get("status") == "revoked":
        db().execute("UPDATE bookings SET grant_status = 'revoked' WHERE id = %s AND grant_status = 'pending'",
                     (b["id"],))
        return
    url = g_.get("ticket_url") or ""
    if not url.startswith(env("ACCESS_PUBLIC_URL", "http://localhost:8003") + "/t/"):
        log.warning("access ticket_url for %s does not start with ACCESS_PUBLIC_URL", b["reference"])
        raise CallFailed("Access", 502)
    db().execute("UPDATE bookings SET grant_status = 'issued', ticket_url = %s "
                 "WHERE id = %s AND status = 'confirmed' AND grant_status = 'pending'", (url, b["id"]))


def run_followups(ref: str, operator: bool = False, new_attempt: bool = False) -> list[str]:
    """Pending grant, revoke, refund: one attempt each (D19, D20). new_attempt: operator's attempt+1."""
    msgs = []

    def failed(e: CallFailed) -> str:
        return e.message if operator else f"{e.service} is not reachable. Please try again."

    b = fetch_booking(ref)
    if b["status"] == "confirmed" and b["grant_status"] == "pending":
        try:
            issue_grant(b)
            msgs.append("E-ticket issued")
        except CallFailed as e:
            msgs.append(failed(e))
    if b["grant_status"] == "revoke_pending":
        try:
            access_client.revoke_grant(ref)
            db().execute("UPDATE bookings SET grant_status = 'revoked' "
                         "WHERE id = %s AND grant_status = 'revoke_pending'", (b["id"],))
            msgs.append("Grant revoked")
        except CallFailed as e:
            return msgs + [failed(e)]  # the refund waits for the revoke (PUR-Q10)
    if new_attempt and b["refund_status"] == "failed":
        db().execute("UPDATE bookings SET refund_status = 'pending', refund_attempt = refund_attempt + 1 "
                     "WHERE id = %s AND refund_status = 'failed'", (b["id"],))
    b = fetch_booking(ref)
    if b["refund_status"] == "pending" and b["grant_status"] != "revoke_pending":
        n = b["refund_attempt"]
        try:
            r = payment_client.create_refund(b["payment_session_id"], ref, b["refund_amount_satang"],
                                             b["refund_reason"], n)
            outcome = "succeeded" if r.get("status") == "succeeded" else "failed"
            db().execute("UPDATE bookings SET refund_status = %s WHERE id = %s AND refund_status = 'pending' "
                         "AND refund_attempt = %s", (outcome, b["id"], n))
            msgs.append(f"Refund attempt {n} {outcome}")
        except CallFailed as e:
            msgs.append(failed(e))
    return msgs


# --- booking (PUR-R07 to PUR-R23, PUR-R39, PUR-R40) ------------------------------------------------

def held_message(b, now) -> str:
    if now >= deadline(b):
        return (f"Time to pay has run out on {b['reference']}; cancel it, "
                f"or try again from {hhmm(b['hold_expires_at'])}")
    return f"Finish or cancel your held booking {b['reference']} first"


def own_hold(member_id, now):
    return db().execute(BOOKING_SQL + " WHERE b.member_id = %(m)s AND b.status = 'held' "
                        "AND b.hold_expires_at > %(now)s ORDER BY b.id LIMIT 1",
                        {"m": member_id, "now": now}).fetchone()


def own_match(member_id, space_id, start, blocks, now):
    return db().execute(BOOKING_SQL + " WHERE b.member_id = %(m)s AND b.space_id = %(s)s AND b.start_at = %(st)s "
                        "AND b.blocks = %(bl)s AND " + BLOCKING + " ORDER BY b.id LIMIT 1",
                        {"m": member_id, "s": space_id, "st": start, "bl": blocks, "now": now}).fetchone()


def start_session(b) -> str:
    """PUR-R23: ask Payment for a session ending 2 min before the hold. Returns 'open' or 'confirmed'."""
    public = env("PUBLIC_URL", "http://localhost:8001")
    try:
        s = payment_client.create_session(
            b["reference"], b["agreed_price_satang"],
            f"{b['space_name']}, {local_time(b['start_at'])}-{hhmm(b['end_at'])}",
            f"{public}/bookings/{b['reference']}/return", f"{public}/bookings/{b['reference']}", deadline(b).astimezone(BKK))
    except CallFailed:
        raise Refusal(503, "payment_unreachable", "Payment is not reachable. Please try again.",
                      b["reference"]) from None
    db().execute("UPDATE bookings SET payment_session_id = %s WHERE id = %s AND payment_session_id IS NULL",
                 (s["id"], b["id"]))
    if s.get("payment_status") == "paid":
        fulfil(fetch_booking(b["reference"]), s)
        return "confirmed"
    if s.get("status") == "expired":
        raise Refusal(409, "payment_time_over", "Time to pay has run out; cancel this hold or book again "
                      f"from {hhmm(b['hold_expires_at'])}", b["reference"])
    return "open"


def resume(b, now) -> tuple[str, str]:
    """PUR-R21: the same space, start and blocks answers with the stored booking."""
    if b["status"] == "confirmed":
        return "already", b["reference"]
    if now >= deadline(b):
        raise Refusal(409, "payment_time_over", "Time to pay has run out; cancel this hold or book again "
                      f"from {hhmm(b['hold_expires_at'])}", b["reference"])
    start_session(b)
    return "resumed", b["reference"]


def place_booking(member, space, start: datetime, blocks, party_size, note) -> tuple[str, str]:
    """Checks in the PUR-R39 order. Returns (created|resumed|already, reference) or raises Refusal."""
    msg = shape_error(start, blocks)
    if msg:
        raise Refusal(400, "invalid_request", msg)
    now = clock.now()
    end = start + BLOCK * blocks
    lapsed = db().execute(BOOKING_SQL + " WHERE b.member_id = %s AND b.status = 'held' AND b.hold_expires_at <= %s",
                          (member["id"], now)).fetchall()
    reconcile_many(lapsed)
    match = own_match(member["id"], space["id"], start, blocks, now)
    if match:
        return resume(match, now)
    msg = window_error(start, now)
    if not msg and (not isinstance(party_size, int) or not 1 <= party_size <= space["capacity"]):
        msg = f"Party size must be 1 to {space['capacity']}"
    if not msg and note and len(note) > 500:
        msg = "Note must be at most 500 characters"
    if msg:
        raise Refusal(400, "invalid_request", msg)
    other = own_hold(member["id"], now)
    if other:
        raise Refusal(409, "held_booking_exists", held_message(other, now))
    # Pre-insert sweep (D11, D13): stale holds of this space, then the insert in one transaction.
    stale = db().execute(BOOKING_SQL + " WHERE b.space_id = %s AND b.status = 'held' AND b.hold_expires_at <= %s",
                         (space["id"], now)).fetchall()
    _, unknown, _ = reconcile_many(stale)
    params = {"s": space["id"], "st": start, "en": end, "now": now}
    overlap = "b.space_id = %(s)s AND tstzrange(b.start_at, b.end_at) && tstzrange(%(st)s, %(en)s)"
    if db().execute("SELECT 1 FROM bookings b WHERE " + overlap + " AND " + BLOCKING, params).fetchone():
        raise Refusal(409, "slot_taken", "Slot just taken")
    if unknown and db().execute("SELECT 1 FROM bookings b WHERE " + overlap + " AND b.status = 'held'",
                                params).fetchone():
        raise Refusal(503, "payment_unreachable", "Payment is not reachable. Please try again.")
    price = price_satang(space["hourly_rate_satang"], blocks)
    coverage = "free" if price == 0 else "plan" if member["plan_active"] else "pay"
    pay = coverage == "pay"
    again = None
    for _ in range(3):  # regenerate the reference on a collision (D22)
        ref = new_reference()
        try:
            with db().transaction():
                db().execute("SELECT id FROM members WHERE id = %s FOR UPDATE", (member["id"],))
                again = own_match(member["id"], space["id"], start, blocks, now)
                if not again:
                    other = own_hold(member["id"], now)
                    if other:
                        raise Refusal(409, "held_booking_exists", held_message(other, now))
                    db().execute(
                        "INSERT INTO bookings (reference, member_id, space_id, start_at, end_at, blocks, "
                        "party_size, note, agreed_price_satang, coverage, status, hold_expires_at, payment_status, "
                        "grant_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (ref, member["id"], space["id"], start, end, blocks, party_size, note or None, price,
                         coverage, "held" if pay else "confirmed", now + HOLD if pay else None,
                         "unpaid" if pay else "not_required", "not_requested" if pay else "pending"))
            break
        except UniqueViolation:
            continue
        except ExclusionViolation:
            again = own_match(member["id"], space["id"], start, blocks, now)
            if not again:
                raise Refusal(409, "slot_taken", "Slot just taken") from None
            break
    if again:
        return resume(again, now)
    b = fetch_booking(ref)
    if pay:
        start_session(b)
    else:
        run_followups(ref)  # PUR-R20: plan and free skip Payment; request the grant now
    return "created", ref


# --- cancel (PUR-R30 to PUR-R32) -------------------------------------------------------------------

def cancel_block(b, by_operator: bool, now) -> Refusal | None:
    if b["status"] == "cancelled":
        return Refusal(200, "already_cancelled", "This booking is already cancelled")
    if b["status"] == "expired":
        return Refusal(409, "hold_expired", "This hold has already expired")
    if by_operator and now >= b["end_at"]:
        return Refusal(409, "booking_ended", "This booking has ended")
    if not by_operator and now >= b["start_at"]:
        return Refusal(409, "booking_started", "This booking has started; ask the operator")
    return None


def refund_text(amount: int, price: int) -> str:
    if amount:
        return f"{money(amount)} (100%)"
    return f"{money(0)} (0%): the start is less than 24 h away"


def cancel_booking(b, by_operator: bool, shown=None, check_shown: bool = False) -> str:
    """Returns the flash text, or raises Refusal. Refusal(303, 'confirm_again') goes back to the screen."""
    now = clock.now()
    ref = b["reference"]
    block = cancel_block(b, by_operator, now)
    if block and block.status == 200:
        run_followups(ref, operator=by_operator)
        return block.message
    if block:
        raise block
    policy = refund_policy(b["coverage"], b["agreed_price_satang"], b["start_at"], now, by_operator)
    if check_shown:
        if shown is None:
            raise Refusal(303, "confirm_again", "Please confirm the refund again.")
        if policy < shown:
            raise Refusal(303, "confirm_again", f"The refund is now {refund_text(policy, 0)}. Confirm again.")
    reason = "operator_cancel" if by_operator else "member_cancel"
    if b["status"] == "held":
        paid = None
        if b["payment_session_id"]:
            try:
                paid = payment_client.expire_session(b["payment_session_id"])
            except CallFailed:
                raise Refusal(503, "payment_unreachable", "Payment is not reachable. Please try again.") from None
        if paid and paid.get("payment_status") == "paid":
            if fulfil(b, paid, issue=False) != "confirmed":
                return "Booking cancelled"
            b = fetch_booking(ref)  # confirmed without a grant: cancel it under the policy below (D18)
        elif now >= b["hold_expires_at"]:
            db().execute("UPDATE bookings SET status = 'expired' WHERE id = %s AND status = 'held'", (b["id"],))
            raise Refusal(409, "hold_expired", "This hold has already expired")
        else:
            db().execute("UPDATE bookings SET status = 'cancelled', cancel_reason = %s, cancelled_at = %s "
                         "WHERE id = %s AND status = 'held'", (reason, now, b["id"]))
            return "Booking cancelled"
    refund = policy > 0
    db().execute(
        "UPDATE bookings SET status = 'cancelled', cancel_reason = %s, cancelled_at = %s, "
        "refund_amount_satang = %s, refund_reason = %s, refund_status = %s, refund_attempt = %s, "
        "refund_requested_at = %s, grant_status = 'revoke_pending' WHERE id = %s AND status = 'confirmed'",
        (reason, now, policy, reason if refund else None, "pending" if refund else "none",
         1 if refund else None, now if refund else None, b["id"]))
    run_followups(ref, operator=by_operator)
    return "Booking cancelled"


def cancel_screen(b, by_operator: bool, unknown: bool) -> tuple[str, int]:
    """Section 5.4: the text and the shown_refund_satang of the confirm screen."""
    now = clock.now()
    policy = refund_policy(b["coverage"], b["agreed_price_satang"], b["start_at"], now, by_operator)
    if b["status"] == "confirmed":
        if b["coverage"] != "pay":
            return "No payment was taken", 0
        return f"Refund {refund_text(policy, 0)}", policy
    if not b["payment_session_id"]:
        return "Payment was not started. Nothing was charged. Cancel this hold?", 0
    after = now >= deadline(b)
    if after and not unknown:
        return "Nothing was charged. Cancel this hold?", 0
    if after:
        return f"Payment status unknown. If a payment went through, the refund is {refund_text(policy, 0)}.", policy
    if unknown:
        return ("We could not check your payment just now. If a payment completes or went through, "
                f"the refund is {refund_text(policy, 0)}."), policy
    return ("We have not received a payment for this booking. Cancelling closes the payment page. "
            f"If a payment completes first, the refund is {refund_text(policy, 0)}."), policy


# --- app -------------------------------------------------------------------------------------------

def connect(database_url: str) -> psycopg.Connection:
    try:
        conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)
    except psycopg.OperationalError as error:
        raise SystemExit(f"Could not connect to the database at DATABASE_URL.\n{error}") from None
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(8001)")  # two workers start at once
        conn.execute(SCHEMA)
        if conn.execute("SELECT count(*) AS n FROM spaces").fetchone()["n"] == 0:
            for name, capacity, rate in DEMO_SPACES:
                conn.execute("INSERT INTO spaces (name, capacity, hourly_rate_satang) VALUES (%s, %s, %s)",
                             (name, capacity, rate))
    return conn


def valid_display_name(name: str) -> bool:
    return 1 <= len(name) <= 50 and all(unicodedata.category(c)[0] in "LMN" or c in " .'-" for c in name)


def valid_email(email: str) -> bool:
    return (email.isascii() and len(email) <= 254
            and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) is not None)


SUCCESS_FLASHES = ("Registered.", "Logged out", "Booking cancelled", "Space saved", "Space archived",
                   "E-ticket issued", "Grant revoked", "Plan on ", "Plan off ")
INFO_FLASHES = ("Please log in", "Log in to book", "You already booked", "This booking is already cancelled",
                "Nothing to retry")


def flash_kind(message: str) -> str:
    """Display style of a flash (PUR-R36): success, info or error. Only the look depends on it."""
    # ponytail: go() flashes plain strings, so the look is read from the text; flash categories if this grows.
    if message.startswith(SUCCESS_FLASHES) or re.fullmatch(r"Refund attempt \d+ succeeded", message):
        return "success"
    if message.startswith(INFO_FLASHES) or re.match(r"\d+ confirmed, ", message):
        return "info"
    return "error"


def safe_next(path) -> str:
    if (isinstance(path, str) and path.startswith("/") and path[1:2] not in ("/", "\\")
            and not any(ord(c) < 32 or ord(c) == 127 for c in path)):
        return path
    return "/"


def create_app(database_url: str | None = None) -> Flask:
    secret = os.getenv("SECRET_KEY")
    if not secret:
        raise SystemExit("SECRET_KEY is required")
    app = Flask(__name__)
    app.secret_key = secret
    app.config.update(SESSION_COOKIE_NAME="purchase_session", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=os.getenv("PUBLIC_URL", "").startswith("https"))
    # ponytail: one connection per worker (gunicorn workers = 2); psycopg_pool if that becomes the limit.
    app.db = connect(database_url or os.getenv("DATABASE_URL", "postgresql://cowork:cowork@localhost:5441/cowork"))
    test_clock = os.getenv("TEST_CLOCK_ENABLED") == "true"
    clock.configure(app.db, test_clock)
    app.add_template_filter(local_time, "local_time")
    app.add_template_filter(hhmm, "hhmm")
    app.add_template_filter(money, "money")
    app.add_template_filter(duration_text, "duration")
    app.add_template_filter(fdate, "fdate")
    app.add_template_filter(hours_text, "hours")
    app.add_template_filter(flash_kind, "flash_kind")

    @app.before_request
    def load_member():
        g.member, g.login_ended = None, False
        if "member_id" not in session:
            return
        now = clock.now().timestamp()
        m = app.db.execute("SELECT * FROM members WHERE id = %s", (session["member_id"],)).fetchone()
        login_at = session.get("login_at", 0)
        if m is None or m["email"] != session.get("email") or not 0 <= now - login_at < SESSION_HOURS * 3600:
            for k in ("member_id", "email", "login_at"):
                session.pop(k, None)
            g.login_ended = True
            if request.path != "/_test/clock":
                flash("Please log in again")
            return
        g.member = m

    @app.context_processor
    def inject_current_user():
        return {"me": g.get("member"), "payment_public_url": env("PAYMENT_PUBLIC_URL", "http://localhost:8002"),
                "pay_margin": PAY_MARGIN, "one_day": timedelta(hours=24)}

    def require_member(message: str = "Please log in", keep: str | None = None):
        if g.member:
            return g.member
        if request.path.startswith("/api/"):
            resp, code = jerr(401, "unauthorized", "Please log in again" if g.login_ended else "Please log in")
            resp.status_code = code
            abort(resp)
        if keep is None and request.method == "GET":
            keep = request.full_path.rstrip("?")
        if keep:
            session["next"] = keep
        if not g.login_ended:
            flash(message)
        abort(redirect("/login", 303))

    def require_operator():
        m = require_member()
        if not m["is_operator"]:
            abort(404)
        return m

    def owned_booking(ref: str):
        m = require_member(keep=f"/bookings/{ref}" if request.method == "POST" else None)
        b = fetch_booking(ref)
        if b is None or (b["member_id"] != m["id"] and not m["is_operator"]):
            abort(404)
        return b

    def open_space(space_id: int):
        s = app.db.execute("SELECT * FROM spaces WHERE id = %s AND archived_at IS NULL", (space_id,)).fetchone()
        if s is None:
            abort(404)
        return s

    def sync(b) -> bool:
        """Reconcile a held booking and retry pending follow-ups. Returns True when Payment gave no answer."""
        unknown = False
        try:
            reconcile(b)
        except CallFailed:
            unknown = True
        run_followups(b["reference"])
        return unknown

    @app.errorhandler(404)
    def not_found(_):
        if request.path.startswith("/api/"):
            return jerr(404, "not_found", "Not found")
        return render_template("not_found.html"), 404

    @app.get("/health")
    def health():
        try:
            app.db.execute("SELECT 1")
        except psycopg.Error:
            return jsonify(status="error", error="database unreachable"), 503
        return jsonify(status="ok", revision=os.getenv("APP_REVISION", "local"))

    @app.post("/_test/clock")
    def set_test_clock():
        if not test_clock:
            abort(404)
        body = request.get_json(silent=True)
        value = body.get("now") if isinstance(body, dict) else None
        when = None
        if value is not None:
            try:
                when = datetime.fromisoformat(value)
            except (TypeError, ValueError):
                return jerr(400, "invalid_request", "now must be ISO 8601 with an offset")
            if when.tzinfo is None:
                return jerr(400, "invalid_request", "Time needs an offset, for example +07:00")
        app.db.execute("INSERT INTO test_clock (id, now_override) VALUES (1, %s) "
                       "ON CONFLICT (id) DO UPDATE SET now_override = EXCLUDED.now_override", (when,))
        return jsonify(now=iso(when))

    # --- accounts (PUR-R01 to PUR-R04, PUR-R37) ---

    @app.get("/register")
    def register_form():
        return render_template("register.html")

    @app.post("/register")
    def register():
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        if not valid_email(email):
            return go("/register", "Enter a valid email")
        if len(password) < 8:
            return go("/register", "Password must be at least 8 characters")
        if not name:
            return go("/register", "Display name is required")
        if not valid_display_name(name):
            return go("/register", "Display name must be 1 to 50 letters, digits or spaces")
        try:
            app.db.execute("INSERT INTO members (email, display_name, password_hash, is_operator) "
                           "VALUES (%s, %s, %s, %s)", (email, name, generate_password_hash(password),
                                                       email == os.getenv("OPERATOR_EMAIL", "").strip().lower()))
        except UniqueViolation:
            return go("/register", "Email already registered")
        return go("/login", "Registered. Please log in.")

    @app.get("/login")
    def login_form():
        return render_template("login.html")

    @app.post("/login")
    def login():
        email = request.form.get("email", "").strip().lower()
        m = app.db.execute("SELECT * FROM members WHERE email = %s", (email,)).fetchone()
        if m is None or not check_password_hash(m["password_hash"], request.form.get("password", "")):
            return go("/login", "Invalid email or password")
        if email == os.getenv("OPERATOR_EMAIL", "").strip().lower() and not m["is_operator"]:
            app.db.execute("UPDATE members SET is_operator = TRUE WHERE id = %s", (m["id"],))
        target = safe_next(session.pop("next", "/"))
        session.update(member_id=m["id"], email=m["email"], login_at=clock.now().timestamp())
        return redirect(target, 303)

    @app.post("/logout")
    def logout():
        session.clear()
        return go("/", "Logged out")

    # --- spaces and the grid (PUR-R13 to PUR-R19) ---

    @app.get("/")
    def index():
        spaces = app.db.execute("SELECT * FROM spaces WHERE archived_at IS NULL ORDER BY id").fetchall()
        return render_template("index.html", spaces=spaces)

    def busy_ranges(space_id, day: date, now):
        lo = datetime.combine(day, time(0), BKK)
        rows = app.db.execute("SELECT b.start_at, b.end_at FROM bookings b WHERE b.space_id = %(s)s "
                              "AND b.start_at < %(hi)s AND b.end_at > %(lo)s AND " + BLOCKING,
                              {"s": space_id, "lo": lo, "hi": lo + timedelta(days=1), "now": now}).fetchall()
        return [(r["start_at"], r["end_at"]) for r in rows]

    @app.get("/spaces/<int:space_id>")
    def space_page(space_id):
        space = open_space(space_id)
        now = clock.now()
        try:
            day = date.fromisoformat(request.args.get("date") or today(now).isoformat())
        except ValueError:
            day = None
        if day is None or not date_in_horizon(day, now):
            return go(f"/spaces/{space_id}", "Pick a date from today to 30 days ahead")
        blocks = to_int(request.args.get("blocks", "1"))
        if blocks is None or not 1 <= blocks <= 8:
            return go(f"/spaces/{space_id}", "Duration must be 1 to 8 blocks")
        price = price_satang(space["hourly_rate_satang"], blocks)
        me = g.member
        coverage = "free" if price == 0 else "plan" if me and me["plan_active"] else "pay"
        hold = own_hold(me["id"], now) if me else None
        busy = busy_ranges(space_id, day, now)
        min_day, max_day = today(now), today(now) + timedelta(days=30)
        return render_template("space.html", space=space, day=day, blocks=blocks, price=price,
                               coverage=coverage, starts=grid(day, blocks, now, busy), hold=hold, now=now,
                               slots=grid(day, 1, now, busy),  # the timeline: one row per 30-min block
                               cal=month(day, now), strip=[min_day + timedelta(days=i) for i in range(31)],
                               hold_text=held_message(hold, now) if hold else None,
                               min_day=min_day, max_day=max_day, soon=day <= today(now) + timedelta(days=1))

    @app.get("/api/spaces")
    def api_spaces():
        rows = app.db.execute("SELECT * FROM spaces WHERE archived_at IS NULL ORDER BY id").fetchall()
        return jsonify(spaces=[{"space_id": s["id"], "name": s["name"], "capacity": s["capacity"],
                                "hourly_rate_satang": s["hourly_rate_satang"],
                                "block_price_satang": price_satang(s["hourly_rate_satang"], 1),
                                "currency": "THB"} for s in rows])

    @app.get("/api/spaces/<int:space_id>/availability")
    def api_availability(space_id):
        space = open_space(space_id)
        now = clock.now()
        try:
            day = date.fromisoformat(request.args.get("date", ""))
        except ValueError:
            day = None
        if day is None or not date_in_horizon(day, now):
            return jerr(400, "invalid_request", "Pick a date from today to 30 days ahead")
        blocks = to_int(request.args.get("blocks"))
        if blocks is None or not 1 <= blocks <= 8:
            return jerr(400, "invalid_request", "Duration must be 1 to 8 blocks")
        starts = grid(day, blocks, now, busy_ranges(space_id, day, now))
        return jsonify(space_id=space_id, date=day.isoformat(), blocks=blocks, currency="THB",
                       price_satang=price_satang(space["hourly_rate_satang"], blocks),
                       starts=[{"start": iso(s["start"]), "available": s["available"], "reason": s["reason"]}
                               for s in starts])

    # --- booking ---

    @app.post("/spaces/<int:space_id>/book")
    def book_form(space_id):
        f = request.form
        back = f"/spaces/{space_id}?" + urlencode({"date": f.get("date", ""), "blocks": f.get("blocks", "")})
        member = require_member("Log in to book", keep=back)
        space = open_space(space_id)
        try:
            start = datetime.combine(date.fromisoformat(f.get("date", "")), time.fromisoformat(f["start"]), BKK)
        except (KeyError, ValueError):
            return go(back, "Pick a start time")
        try:
            kind, ref = place_booking(member, space, start, to_int(f.get("blocks")), to_int(f.get("party_size")),
                                      f.get("note", "").strip() or None)
        except Refusal as r:
            msg = "Pick a start on the half hour" if r.message == "Start on :00 or :30" else r.message
            return go(f"/bookings/{r.ref}" if r.ref else back, msg)
        if kind == "already":
            return go(f"/bookings/{ref}", "You already booked this slot")
        b = fetch_booking(ref)
        if b["status"] == "held" and b["payment_session_id"]:
            return redirect(pay_url(b["payment_session_id"]), 303)
        return redirect(f"/bookings/{ref}", 303)

    @app.post("/api/bookings")
    def api_book():
        member = require_member()
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jerr(400, "invalid_request", "Send a JSON object")
        space_id = to_int(body.get("space_id")) if not isinstance(body.get("space_id"), str) else None
        if space_id is None:
            return jerr(400, "invalid_request", "space_id must be an integer")
        space = open_space(space_id)
        try:
            start = datetime.fromisoformat(body.get("start"))
        except (TypeError, ValueError):
            return jerr(400, "invalid_request", "Time needs an offset, for example +07:00")
        if start.tzinfo is None:
            return jerr(400, "invalid_request", "Time needs an offset, for example +07:00")
        note = body.get("note")
        if note is not None and not isinstance(note, str):
            return jerr(400, "invalid_request", "Note must be at most 500 characters")
        blocks, party = body.get("blocks"), body.get("party_size")
        try:
            kind, ref = place_booking(member, space, start, blocks if type(blocks) is int else None,
                                      party if type(party) is int else None, note or None)
        except Refusal as r:
            return jerr(r.status, r.code, r.message)
        return jsonify(booking_json(fetch_booking(ref))), 201 if kind == "created" else 200

    @app.get("/bookings/<ref>")
    def booking_page(ref):
        b = owned_booking(ref)
        unknown = sync(b)
        b = fetch_booking(ref)
        now = clock.now()
        own = b["member_id"] == g.member["id"]
        return render_template("booking.html", b=b, now=now, own=own, unknown=unknown,
                               deadline=deadline(b) if b["hold_expires_at"] else None,
                               seconds_left=int((deadline(b) - now).total_seconds())
                               if b["hold_expires_at"] else 0, pay_window=int((HOLD - PAY_MARGIN).total_seconds()),
                               book_again=book_again_url(b, now), full_refund_by=b["start_at"] - timedelta(hours=24))

    def book_again_url(b, now) -> str:
        if b["space_archived_at"]:
            return "/"
        day = b["start_at"].astimezone(BKK).date()
        if date_in_horizon(day, now):
            return f"/spaces/{b['space_id']}?" + urlencode({"date": day.isoformat(), "blocks": b["blocks"]})
        return f"/spaces/{b['space_id']}"

    @app.get("/bookings/<ref>/return")
    def booking_return(ref):
        if g.member is None:
            session["next"] = f"/bookings/{ref}/return"
            return go("/login") if g.login_ended else go("/login", "Please log in again")
        b = owned_booking(ref)
        sync(b)
        return redirect(f"/bookings/{ref}", 303)

    @app.get("/api/bookings/<ref>")
    def api_booking(ref):
        b = owned_booking(ref)
        sync(b)
        return jsonify(booking_json(fetch_booking(ref)))

    def my_rows(member_id):
        return app.db.execute(BOOKING_SQL + " WHERE b.member_id = %s ORDER BY b.start_at", (member_id,)).fetchall()

    @app.get("/api/bookings/mine")
    def api_mine():
        m = require_member()
        reconcile_many([b for b in my_rows(m["id"]) if b["status"] == "held"])
        return jsonify(bookings=[booking_json(b) for b in my_rows(m["id"])])

    @app.get("/bookings/mine")
    def my_bookings():
        m = require_member()
        _, unknown, _ = reconcile_many([b for b in my_rows(m["id"]) if b["status"] == "held"])
        now = clock.now()
        rows = my_rows(m["id"])
        upcoming = [b for b in rows if b["end_at"] > now]
        past = sorted((b for b in rows if b["end_at"] <= now), key=lambda b: b["start_at"], reverse=True)
        hold = own_hold(m["id"], now)
        return render_template("my_bookings.html", upcoming=upcoming, past=past, now=now, unknown=unknown,
                               hold=hold, hold_text=held_message(hold, now) if hold else None,
                               view="past" if request.args.get("view") == "past" else "upcoming")

    # --- cancel and retry ---

    @app.get("/bookings/<ref>/cancel")
    def cancel_form(ref):
        b = owned_booking(ref)
        by_operator = bool(g.member["is_operator"])
        unknown = sync(b) if b["status"] == "held" else False
        b = fetch_booking(ref)
        block = cancel_block(b, by_operator, clock.now())
        if block:
            return go(f"/bookings/{ref}", block.message)
        text, shown = cancel_screen(b, by_operator, unknown)
        return render_template("cancel.html", b=b, text=text, shown=shown, by_operator=by_operator,
                               return_to=request.args.get("return_to") == "booking")

    def do_cancel_form(ref, by_operator: bool, done_url: str):
        b = owned_booking(ref)
        raw = request.form.get("shown_refund_satang", "")
        shown = int(raw) if re.fullmatch(r"\d{1,18}", raw.strip()) else None
        try:
            msg = cancel_booking(b, by_operator, shown, check_shown=True)
        except Refusal as r:
            return go(f"/bookings/{ref}/cancel" if r.code == "confirm_again" else f"/bookings/{ref}", r.message)
        return go(done_url if msg == "Booking cancelled" else f"/bookings/{ref}", msg)

    @app.post("/bookings/<ref>/cancel")
    def cancel_post(ref):
        return do_cancel_form(ref, bool(g.member and g.member["is_operator"]), f"/bookings/{ref}")

    @app.post("/operator/bookings/<ref>/cancel")
    def operator_cancel_post(ref):
        require_operator()
        back = f"/bookings/{ref}" if request.form.get("return_to") == "booking" else "/operator/bookings"
        return do_cancel_form(ref, True, back)

    @app.post("/api/bookings/<ref>/cancel")
    def api_cancel(ref):
        b = owned_booking(ref)
        if request.get_data() and not isinstance(request.get_json(silent=True), dict):
            return jerr(400, "invalid_request", "Send a JSON object")
        try:
            cancel_booking(b, bool(g.member["is_operator"]))
        except Refusal as r:
            return jerr(r.status, r.code, r.message)
        return jsonify(booking_json(fetch_booking(ref)))

    @app.post("/bookings/<ref>/retry")
    def retry(ref):
        b = owned_booking(ref)
        pending = (b["grant_status"] in ("pending", "revoke_pending") and b["status"] in ("confirmed", "cancelled")
                   or b["refund_status"] == "pending")
        msgs = run_followups(ref) if pending else ["Nothing to retry"]
        return go(f"/bookings/{ref}", *(msgs or ["Nothing to retry"]))

    def back_to(ref=None) -> str:
        if request.form.get("return_to") == "booking" and ref:
            return f"/bookings/{ref}"
        return "/dashboard" if request.form.get("return_to") == "dashboard" else "/operator/bookings"

    @app.post("/operator/bookings/<ref>/retry")
    def operator_retry(ref):
        require_operator()
        b = fetch_booking(ref) or abort(404)
        pending = (b["grant_status"] in ("pending", "revoke_pending") and b["status"] in ("confirmed", "cancelled")
                   or b["refund_status"] in ("pending", "failed"))
        msgs = run_followups(ref, operator=True, new_attempt=True) if pending else []
        return go(back_to(ref), *(msgs or ["Nothing to retry"]))

    def counts_text(c) -> str:
        return f"{c['confirmed']} confirmed, {c['expired']} expired, {c['cancelled']} cancelled, {c['unchanged']} unchanged"

    @app.post("/operator/bookings/<ref>/reconcile")
    def operator_reconcile(ref):
        require_operator()
        b = fetch_booking(ref) or abort(404)
        c, _, failure = reconcile_many([b] if b["status"] == "held" else [])
        if b["status"] != "held":
            c["unchanged"] = 1
        return go(back_to(ref), counts_text(c), *([failure.message] if failure else []))

    @app.post("/operator/bookings/reconcile")
    def operator_reconcile_all():
        require_operator()
        rows = app.db.execute(BOOKING_SQL + " WHERE b.status = 'held' ORDER BY b.id").fetchall()
        c, _, failure = reconcile_many(rows)
        return go(back_to(), counts_text(c), *([failure.message] if failure else []))

    # --- operator pages (PUR-R06, PUR-R15, PUR-R16, PUR-R19, PUR-R34) ---

    @app.get("/operator/bookings")
    def operator_bookings():
        require_operator()
        rows = app.db.execute(BOOKING_SQL + " ORDER BY (b.refund_status IN ('pending', 'failed')) DESC, "
                              "b.refund_requested_at ASC NULLS LAST, b.start_at DESC").fetchall()
        now = clock.now()
        for b in rows:
            flags = []
            if b["status"] == "confirmed" and b["grant_status"] == "pending":
                flags.append("being_prepared")
            if b["grant_status"] == "revoke_pending":
                flags.append("revocation_pending")
            if b["refund_status"] == "pending":
                flags.append("refund_pending")
            if b["refund_status"] == "failed":
                flags.append("refund_failed")
            b["flags"] = flags
        return render_template("operator_bookings.html", rows=rows, now=now)

    def space_error(form, space_id=None) -> str | None:
        name = form.get("name", "").strip()
        if not name:
            return "Name is required"
        if app.db.execute("SELECT 1 FROM spaces WHERE lower(name) = lower(%s) AND archived_at IS NULL "
                          "AND id IS DISTINCT FROM %s", (name, space_id)).fetchone():
            return "Name already used"
        capacity = to_int(form.get("capacity"))
        if capacity is None or not 1 <= capacity <= 1000:
            return "Capacity must be a whole number from 1 to 1,000"
        rate = to_int(form.get("hourly_rate"))
        if rate is None or not (rate == 0 or 20 <= rate <= 10000):
            return "Rate must be 0 or 20 to 10,000 THB per hour"
        return None

    @app.get("/operator/spaces")
    def operator_spaces():
        require_operator()
        rows = app.db.execute("SELECT * FROM spaces ORDER BY archived_at IS NOT NULL, id").fetchall()
        return render_template("operator_spaces.html", rows=rows)

    @app.post("/operator/spaces")
    def operator_space_create():
        require_operator()
        error = space_error(request.form)
        if error:
            return go("/operator/spaces", error)
        app.db.execute("INSERT INTO spaces (name, capacity, hourly_rate_satang) VALUES (%s, %s, %s)",
                       (request.form["name"].strip(), to_int(request.form["capacity"]),
                        to_int(request.form["hourly_rate"]) * 100))
        return go("/operator/spaces", "Space saved")

    @app.post("/operator/spaces/<int:space_id>")
    def operator_space_edit(space_id):
        require_operator()
        open_space(space_id)
        error = space_error(request.form, space_id)
        if error:
            return go("/operator/spaces", error)
        app.db.execute("UPDATE spaces SET name = %s, capacity = %s, hourly_rate_satang = %s WHERE id = %s",
                       (request.form["name"].strip(), to_int(request.form["capacity"]),
                        to_int(request.form["hourly_rate"]) * 100, space_id))
        return go("/operator/spaces", "Space saved")

    @app.post("/operator/spaces/<int:space_id>/archive")
    def operator_space_archive(space_id):
        require_operator()
        s = app.db.execute("SELECT * FROM spaces WHERE id = %s", (space_id,)).fetchone() or abort(404)
        if s["archived_at"] is None:
            now = clock.now()
            live = app.db.execute("SELECT reference, status FROM bookings WHERE space_id = %s "
                                  "AND status IN ('held', 'confirmed') AND end_at > %s ORDER BY start_at",
                                  (space_id, now)).fetchall()
            if live:
                refs = ", ".join(r["reference"] + (" (held)" if r["status"] == "held" else "") for r in live)
                return go("/operator/spaces", f"Cancel its upcoming bookings first: {refs}")
            app.db.execute("UPDATE spaces SET archived_at = %s WHERE id = %s AND archived_at IS NULL",
                           (now, space_id))
        return go("/operator/spaces", "Space archived")

    @app.get("/operator/members")
    def operator_members():
        require_operator()
        rows = app.db.execute("SELECT * FROM members ORDER BY id").fetchall()
        return render_template("operator_members.html", rows=rows)

    @app.post("/operator/members/<int:member_id>/plan")
    def operator_plan(member_id):
        require_operator()
        m = app.db.execute("SELECT * FROM members WHERE id = %s", (member_id,)).fetchone() or abort(404)
        value = request.form.get("plan_active")
        if value not in ("true", "false"):
            return go("/operator/members", "Plan must be true or false")
        on = value == "true"
        app.db.execute("UPDATE members SET plan_active = %s WHERE id = %s", (on, member_id))
        msg = f"Plan {'on' if on else 'off'} for {m['email']}"
        if not on:
            refs = [r["reference"] for r in app.db.execute(
                "SELECT reference FROM bookings WHERE member_id = %s AND coverage = 'plan' AND status = 'confirmed' "
                "AND end_at > %s ORDER BY start_at", (member_id, clock.now())).fetchall()]
            if refs:
                msg += "; upcoming plan bookings: " + ", ".join(refs)
        return go("/operator/members", msg)

    @app.get("/dashboard")
    def dashboard():
        require_operator()
        now = clock.now()
        lo = datetime.combine(today(now) - timedelta(days=6), time(0), BKK)
        period = {"lo": lo, "hi": lo + timedelta(days=7)}
        where = "FROM bookings WHERE start_at >= %(lo)s AND start_at < %(hi)s"
        status = {r["status"]: r["n"] for r in app.db.execute(
            "SELECT status, count(*) AS n " + where + " GROUP BY status", period).fetchall()}
        hours = {r["coverage"]: r["blocks"] / 2 for r in app.db.execute(
            "SELECT coverage, sum(blocks) AS blocks " + where + " AND status = 'confirmed' GROUP BY coverage",
            period).fetchall()}
        members = app.db.execute("SELECT count(DISTINCT member_id) AS n " + where + " AND status = 'confirmed'",
                                 period).fetchone()["n"]
        spaces = app.db.execute("SELECT count(*) AS n FROM spaces WHERE archived_at IS NULL").fetchone()["n"]
        booked = sum(hours.values())
        utilization = booked / (12 * spaces * 7) if spaces else 0.0
        return render_template(
            "dashboard.html", status={k: status.get(k, 0) for k in ("held", "confirmed", "expired", "cancelled")},
            hours={k: hours_text(hours.get(k, 0)) for k in ("pay", "plan", "free")}, members=members,
            utilization=utilization, period_from=lo.date(), period_to=today(now))

    return app

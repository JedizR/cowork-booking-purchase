import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from psycopg.errors import DeadlockDetected, ExclusionViolation, UniqueViolation
from psycopg.rows import dict_row
from werkzeug.security import check_password_hash, generate_password_hash

from purchase import calculate_booking_price_cents

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://cowork:cowork@localhost:5441/cowork"
)
# Signs the login session cookie. Fine for local/dev; a real deployment
# must set a real SECRET_KEY (see issue #47), or every restart logs
# everyone out and, worse, an unset default would be a known, public key.
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-not-for-production")


def get_connection(database_url: str) -> psycopg.Connection:
    try:
        conn = psycopg.connect(database_url, row_factory=dict_row, autocommit=True)
    except psycopg.OperationalError as error:
        # A raw psycopg traceback here is the first thing a new contributor
        # sees if Postgres isn't running yet - fail fast with a clear pointer
        # instead. See the Configuration section in README.md.
        raise SystemExit(
            f"Could not connect to the database at DATABASE_URL={database_url!r}\n"
            f"{error}\n"
            "Is Postgres running? Try: docker compose up db -d"
        ) from None
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS spaces (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                capacity INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            "ALTER TABLE spaces ADD COLUMN IF NOT EXISTS "
            "price_cents INTEGER NOT NULL DEFAULT 0"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bookings (
                id SERIAL PRIMARY KEY,
                space_id INTEGER NOT NULL REFERENCES spaces (id),
                member TEXT NOT NULL,
                paid BOOLEAN NOT NULL
            )
            """
        )
        # Mocked subscriptions, keyed by the trimmed lower-case member name.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                member TEXT PRIMARY KEY,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # First step of #120 (real accounts): just registration for now.
        # Storing only a hash, never the password itself.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Added after the table already existed, so ALTER instead of editing
        # CREATE TABLE above - existing databases get the new columns too.
        cur.execute(
            "ALTER TABLE bookings "
            "ADD COLUMN IF NOT EXISTS start_time TIMESTAMPTZ, "
            "ADD COLUMN IF NOT EXISTS end_time TIMESTAMPTZ"
        )
        # Price charged at booking time, so a later price change can't rewrite past revenue.
        cur.execute(
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS amount_cents INTEGER"
        )
        # Links a booking to the account that was logged in when it was made
        # (#134, the first concrete step of #86). NULL for a guest booking
        # made while logged out, and for every booking made before this.
        cur.execute(
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS "
            "user_id INTEGER REFERENCES users (id)"
        )
        # Last 4 digits only (#119) - never the full card number or CVC.
        # NULL until the booking is actually paid.
        cur.execute(
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS card_last4 TEXT"
        )
        # When the booking was made (not when the space is used), so the
        # dashboard can show growth over time. Bookings made before this
        # column existed get the time the column was added - the best we
        # have, and it keeps the column NOT NULL.
        cur.execute(
            "ALTER TABLE bookings ADD COLUMN IF NOT EXISTS "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        )
        # Bookings from before that column existed get the space's current price (best we have).
        cur.execute(
            "UPDATE bookings SET amount_cents = s.price_cents "
            "FROM spaces s "
            "WHERE s.id = bookings.space_id AND bookings.amount_cents IS NULL"
        )
        # Belt-and-suspenders against double-booking: the app already checks
        # for overlaps before inserting, but that check-then-insert isn't
        # atomic, so two simultaneous requests could both pass the check.
        # This constraint makes Postgres itself reject the second insert.
        cur.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'no_overlapping_bookings'
                ) THEN
                    ALTER TABLE bookings
                    ADD CONSTRAINT no_overlapping_bookings
                    EXCLUDE USING gist (
                        space_id WITH =,
                        tstzrange(start_time, end_time) WITH &&
                    );
                END IF;
            END $$;
            """
        )
    return conn


def parse_time(value) -> datetime | None:
    """ISO 8601 with a timezone, e.g. 2026-09-25T09:00:00+07:00."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def parse_window(args) -> tuple[tuple | None, str | None]:
    """Optional ?start_time=&end_time= as (window, error); window is None if absent."""
    raw_start, raw_end = args.get("start_time"), args.get("end_time")
    if raw_start is None and raw_end is None:
        return None, None
    start_time, end_time = parse_time(raw_start), parse_time(raw_end)
    if start_time is None or end_time is None:
        return None, (
            "start_time and end_time must be given together "
            "(ISO 8601 with timezone, e.g. 2026-09-25T09:00:00Z)"
        )
    if end_time <= start_time:
        return None, "end_time must be after start_time"
    return (start_time, end_time), None


def booked_space_ids(cur, window) -> set:
    """Spaces booked during the window, or booked right now if no window."""
    if window is None:
        cur.execute(
            "SELECT DISTINCT space_id FROM bookings "
            "WHERE start_time <= now() AND end_time > now()"
        )
    else:
        start_time, end_time = window
        # Same overlap rule as booking creation: back-to-back is not a clash.
        cur.execute(
            "SELECT DISTINCT space_id FROM bookings "
            "WHERE start_time < %s AND end_time > %s",
            (end_time, start_time),
        )
    return {row["space_id"] for row in cur.fetchall()}


LOCAL_TZ = timezone(timedelta(hours=7))  # Bangkok, no daylight saving


def parse_form_time(value) -> datetime | None:
    """The browser's datetime-local field sends no timezone (2026-09-25T09:00),
    so times typed into the booking form are read as Bangkok time."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def is_valid_name(name) -> bool:
    return isinstance(name, str) and name.strip() != ""


def is_valid_capacity(capacity) -> bool:
    # bool is a subclass of int in Python, so rule out true/false
    return (
        isinstance(capacity, int)
        and not isinstance(capacity, bool)
        and capacity >= 1
    )


def is_valid_price(price) -> bool:
    return isinstance(price, int) and not isinstance(price, bool) and price >= 0


# Deliberately simple: good enough to catch a typo, not full RFC 5322.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email) -> bool:
    return isinstance(email, str) and EMAIL_RE.match(email.strip()) is not None


def is_valid_password(password) -> bool:
    return isinstance(password, str) and len(password) >= 8






def member_key(name: str) -> str:
    return name.strip().lower()


def is_subscribed(cur, member) -> bool:
    if not isinstance(member, str):
        return False
    cur.execute(
        "SELECT 1 FROM subscriptions WHERE member = %s AND active",
        (member_key(member),),
    )
    return cur.fetchone() is not None


def local_time(value: datetime) -> str:
    """For the HTML pages: Bangkok time, no seconds or offset clutter."""
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def booking_to_json(row: dict) -> dict:
    return {
        **row,
        "start_time": row["start_time"].astimezone(timezone.utc).isoformat(),
        "end_time": row["end_time"].astimezone(timezone.utc).isoformat(),
        "created_at": row["created_at"].astimezone(timezone.utc).isoformat(),
    }

def compute_metrics(cur) -> dict:
    cur.execute("SELECT COUNT(*) AS count FROM spaces")
    total_spaces = cur.fetchone()["count"]

    cur.execute(
        "SELECT COUNT(*) AS total, "
        "COUNT(*) FILTER (WHERE paid) AS paid, "
        "COUNT(*) FILTER (WHERE NOT paid) AS unpaid "
        "FROM bookings"
    )
    booking_counts = cur.fetchone()

    cur.execute("SELECT COUNT(DISTINCT member) AS count FROM bookings")
    total_members = cur.fetchone()["count"]

    cur.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM bookings WHERE paid"
    )
    revenue_cents = cur.fetchone()["total"]

    # Booked hours over the next 7 days, clipped to that window, across all
    # spaces - same overlap rule used everywhere else (starts before the
    # window ends AND ends after the window starts).
    cur.execute(
        "SELECT COALESCE(SUM(EXTRACT(EPOCH FROM ("
        "LEAST(end_time, now() + interval '7 days') - GREATEST(start_time, now())"
        ")) / 3600.0), 0) AS hours "
        "FROM bookings "
        "WHERE start_time < now() + interval '7 days' AND end_time > now()"
    )
    booked_hours = float(cur.fetchone()["hours"])
    available_hours = total_spaces * 7 * 24
    utilization = booked_hours / available_hours if available_hours > 0 else 0.0

    cur.execute(
        "SELECT COUNT(*) AS count FROM ("
        "SELECT member FROM bookings GROUP BY member HAVING COUNT(*) > 1"
        ") repeat_members"
    )
    repeat_members = cur.fetchone()["count"]
    repeat_member_rate = repeat_members / total_members if total_members > 0 else 0.0

    payment_conversion = (
        booking_counts["paid"] / booking_counts["total"]
        if booking_counts["total"] > 0
        else 0.0
    )

    avg_revenue_cents_per_paid_booking = (
        round(revenue_cents / booking_counts["paid"])
        if booking_counts["paid"] > 0
        else 0
    )

    cur.execute(
        "SELECT s.id, s.name, "
        "COALESCE(SUM(b.amount_cents) FILTER (WHERE b.paid), 0) AS revenue_cents "
        "FROM spaces s LEFT JOIN bookings b ON b.space_id = s.id "
        "GROUP BY s.id, s.name ORDER BY s.id"
    )
    revenue_by_space = cur.fetchall()

    return {
        "spaces": total_spaces,
        "bookings": booking_counts["total"],
        "paid_bookings": booking_counts["paid"],
        "unpaid_bookings": booking_counts["unpaid"],
        "members": total_members,
        "revenue_cents": revenue_cents,
        "utilization": utilization,
        "repeat_member_rate": repeat_member_rate,
        "payment_conversion": payment_conversion,
        "avg_revenue_cents_per_paid_booking": avg_revenue_cents_per_paid_booking,
        "revenue_by_space": revenue_by_space,
    }

def reset_tables(conn: psycopg.Connection) -> None:
    """Wipe all rows and restart ids. Only used for tests and an opt-in
    local reset (RESET_DB_ON_START=true) - off by default, so a real
    deployment's data survives an app restart."""
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE bookings, spaces, subscriptions, users "
            "RESTART IDENTITY CASCADE"
        )

def seed_starter_space(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM spaces")
        if cur.fetchone()["count"] == 0:
            cur.execute(
                "INSERT INTO spaces (name, capacity, price_cents) "
                "VALUES (%s, %s, %s)",
                ("Founders Desk", 1, 2500),  # $25.00 per hour
            )

def create_app(
    database_url: str = DATABASE_URL, reset_on_start: bool | None = None
) -> Flask:
    if reset_on_start is None:
        reset_on_start = os.getenv("RESET_DB_ON_START", "false").lower() == "true"

    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.db = get_connection(database_url)
    if reset_on_start:
        reset_tables(app.db)
    seed_starter_space(app.db)

    # HTML pages live in templates/ (all extending base.html) and the look
    # in static/style.css. Jinja escapes every {{ value }} automatically.
    app.add_template_filter(local_time, "local_time")
    app.add_template_filter(lambda cents: f"${cents / 100:.2f}", "money")
    app.add_template_filter(lambda share: f"{share * 100:.1f}%", "percent")

    @app.context_processor
    def inject_current_user():
        """Every template gets the logged-in email (or None) for the nav."""
        user_id = session.get("user_id")
        if user_id is None:
            return {"current_user_email": None}

        with app.db.cursor() as cur:
            cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
        if user is None:
            session.pop("user_id", None)  # stale session, e.g. after a DB reset
            return {"current_user_email": None}
        return {"current_user_email": user["email"]}

    @app.get("/")
    def index():
        with app.db.cursor() as cur:
            cur.execute("SELECT id, name, capacity, price_cents FROM spaces")
            rows = cur.fetchall()
            cur.execute(
                "SELECT DISTINCT space_id FROM bookings "
                "WHERE start_time <= now() AND end_time > now()"
            )
            booked_ids = {row["space_id"] for row in cur.fetchall()}
            # Everything still to come, so members can see which times are
            # already taken before picking one.
            cur.execute(
                "SELECT space_id, start_time, end_time FROM bookings "
                "WHERE end_time > now() ORDER BY start_time"
            )
            upcoming = {}
            for row in cur.fetchall():
                upcoming.setdefault(row["space_id"], []).append(row)

        # error is set by the redirect when a form booking fails (see
        # book_from_form); a successful one goes to the confirmation page.
        return render_template(
            "index.html",
            spaces=rows,
            booked_ids=booked_ids,
            upcoming=upcoming,
            error=request.args.get("error"),
        )

    def register_user(email, password):
        """Shared by the JSON API and the HTML register form.
        Returns (payload, status) - {"id": ..., "email": ...}, or an error."""
        if not is_valid_email(email):
            return {"error": "enter a valid email address"}, 400
        if not is_valid_password(password):
            return {"error": "password must be at least 8 characters"}, 400

        email = email.strip().lower()
        password_hash = generate_password_hash(password)

        with app.db.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s) "
                    "RETURNING id, email",
                    (email, password_hash),
                )
            except UniqueViolation:
                return {"error": "email is already registered"}, 409
            user = cur.fetchone()

        return user, 201

    @app.get("/register")
    def register_form():
        return render_template(
            "register.html",
            registered=request.args.get("registered"),
            error=request.args.get("error"),
        )

    @app.post("/register")
    def register():
        if request.is_json:
            body = request.get_json(silent=True) or {}
            payload, status = register_user(body.get("email"), body.get("password"))
            return jsonify(payload), status

        payload, status = register_user(
            request.form.get("email"), request.form.get("password")
        )
        if status >= 400:
            return redirect(url_for("register_form", error=payload["error"]))
        return redirect(url_for("login_form", message="Registered! Please log in."))
    
    def login_user(email, password):
        """Shared by the JSON API and the HTML login form.
        Returns (payload, status) - {"id": ..., "email": ...}, or an error.
        Wrong password and unknown email give the identical error, so a
        failed attempt can't be used to find out which emails are registered."""
        invalid = {"error": "invalid email or password"}, 401
        if not isinstance(email, str) or not isinstance(password, str):
            return invalid

        with app.db.cursor() as cur:
            cur.execute(
                "SELECT id, email, password_hash FROM users WHERE email = %s",
                (email.strip().lower(),),
            )
            user = cur.fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            return invalid

        session["user_id"] = user["id"]
        return {"id": user["id"], "email": user["email"]}, 200

    @app.get("/login")
    def login_form():
        return render_template(
            "login.html",
            error=request.args.get("error"),
            message=request.args.get("message"),
        )
    
    @app.post("/login")
    def login():
        if request.is_json:
            body = request.get_json(silent=True) or {}
            payload, status = login_user(body.get("email"), body.get("password"))
            return jsonify(payload), status

        payload, status = login_user(
            request.form.get("email"), request.form.get("password")
        )
        if status >= 400:
            return redirect(url_for("login_form", error=payload["error"]))
        return redirect(url_for("index"))

    @app.post("/logout")
    def logout():
        # Idempotent: logging out when already logged out just does nothing.
        session.pop("user_id", None)
        if request.is_json:
            return jsonify(status="ok")
        return redirect(url_for("index"))

    @app.get("/health")
    def health():
        try:
            with app.db.cursor() as cur:
                cur.execute("SELECT 1")
        except psycopg.Error:
            return jsonify(status="error", error="database unreachable"), 503
        
        return jsonify(
            status="ok",
            revision=os.getenv("APP_REVISION", "local"),
        )

    @app.get("/spaces")
    def list_spaces():
        window, error = parse_window(request.args)
        if error:
            return jsonify(error=error), 400

        with app.db.cursor() as cur:
            cur.execute("SELECT id, name, capacity, price_cents FROM spaces")
            rows = cur.fetchall()
            booked_ids = booked_space_ids(cur, window)

        spaces = [
            {**row, "available": row["id"] not in booked_ids} for row in rows
        ]
        return jsonify(spaces=spaces)

    @app.post("/spaces")
    def create_space():
        body = request.get_json(silent=True) or {}
        name = body.get("name")
        capacity = body.get("capacity")
        price_cents = body.get("price_cents", 0)

        if name is None or capacity is None:
            return jsonify(error="name and capacity are required"), 400
        if not is_valid_name(name):
            return jsonify(error="name must not be empty"), 400
        if not is_valid_capacity(capacity):
            return jsonify(
                error="capacity must be a whole number of at least 1"
            ), 400
        name = name.strip()
        if not is_valid_price(price_cents):
            return jsonify(
                error="price_cents must be a non-negative integer"
            ), 400

        with app.db.cursor() as cur:
            cur.execute(
                "INSERT INTO spaces (name, capacity, price_cents) VALUES (%s, %s, %s) "
                "RETURNING id",
                (name, capacity, price_cents),
            )
            new_id = cur.fetchone()["id"]

        return jsonify(id=new_id, name=name, capacity=capacity, price_cents=price_cents), 201

    @app.get("/spaces/<int:space_id>")
    def get_space(space_id):
        window, error = parse_window(request.args)
        if error:
            return jsonify(error=error), 400

        with app.db.cursor() as cur:
            cur.execute(
                "SELECT id, name, capacity, price_cents FROM spaces WHERE id = %s",
                (space_id,),
            )
            space = cur.fetchone()
            if space is None:
                return jsonify(error="space not found"), 404

            booked = space_id in booked_space_ids(cur, window)

        return jsonify({**space, "available": not booked})

    @app.patch("/spaces/<int:space_id>")
    def update_space(space_id):
        body = request.get_json(silent=True) or {}
        if not any(field in body for field in ("name", "capacity", "price_cents")):
            return jsonify(
                error="provide name, capacity and/or price_cents to update"
            ), 400

        with app.db.cursor() as cur:
            cur.execute("SELECT id FROM spaces WHERE id = %s", (space_id,))
            if cur.fetchone() is None:
                return jsonify(error="space not found"), 404

            name = body.get("name")
            capacity = body.get("capacity")
            price_cents = body.get("price_cents")
            if "name" in body and not is_valid_name(name):
                return jsonify(error="name must not be empty"), 400
            if "capacity" in body and not is_valid_capacity(capacity):
                return jsonify(
                    error="capacity must be a whole number of at least 1"
                ), 400
            if "price_cents" in body and not is_valid_price(price_cents):
                return jsonify(
                    error="price_cents must be a non-negative integer"
                ), 400
            if name is not None:
                name = name.strip()

            # COALESCE keeps the current value for fields not in the request
            cur.execute(
                "UPDATE spaces "
                "SET name = COALESCE(%s, name), "
                "capacity = COALESCE(%s, capacity), "
                "price_cents = COALESCE(%s, price_cents) "
                "WHERE id = %s "
                "RETURNING id, name, capacity, price_cents",
                (name, capacity, price_cents, space_id),
            )
            space = cur.fetchone()

        return jsonify(space)

    @app.delete("/spaces/<int:space_id>")
    def delete_space(space_id):
        with app.db.cursor() as cur:
            cur.execute("SELECT id FROM spaces WHERE id = %s", (space_id,))
            space = cur.fetchone()
            if space is None:
                return jsonify(error="space not found"), 404

            cur.execute(
                "SELECT id FROM bookings WHERE space_id = %s LIMIT 1",
                (space_id,),
            )
            if cur.fetchone() is not None:
                return jsonify(
                    error="space has bookings, cancel them first"
                ), 409

            cur.execute("DELETE FROM spaces WHERE id = %s", (space_id,))

        return "", 204

    @app.get("/spaces/<int:space_id>/bookings")
    def list_space_bookings(space_id):
        with app.db.cursor() as cur:
            cur.execute("SELECT id FROM spaces WHERE id = %s", (space_id,))
            if cur.fetchone() is None:
                return jsonify(error="space not found"), 404

            cur.execute(
                "SELECT id, space_id, member, paid, start_time, end_time, amount_cents, user_id, card_last4, created_at "
                "FROM bookings WHERE space_id = %s ORDER BY start_time",
                (space_id,),
            )
            rows = cur.fetchall()

        return jsonify(bookings=[booking_to_json(row) for row in rows])

    def book_space(space_id, member, start_time, end_time, party_size):
        """Shared by the JSON API and the HTML booking form.
        Returns (payload, status) - the booking, or an {"error": ...}."""
        with app.db.cursor() as cur:
            cur.execute(
                "SELECT id, capacity, price_cents FROM spaces WHERE id = %s",
                (space_id,),
            )
            space = cur.fetchone()
            if space is None:
                return {"error": "space not found"}, 404

            if end_time <= start_time:
                return {"error": "end_time must be after start_time"}, 400
            # bool is a subclass of int in Python, so rule out true/false
            if (
                not isinstance(party_size, int)
                or isinstance(party_size, bool)
                or party_size < 1
            ):
                return {
                    "error": "party_size must be a whole number of at least 1"
                }, 400
            if party_size > space["capacity"]:
                return {
                    "error": f"party_size {party_size} exceeds this space's "
                    f"capacity of {space['capacity']}"
                }, 400

            # Overlap = starts before the other ends AND ends after the
            # other starts. Back-to-back bookings (10-11, 11-12) are allowed.
            cur.execute(
                "SELECT id FROM bookings "
                "WHERE space_id = %s AND start_time < %s AND end_time > %s",
                (space_id, end_time, start_time),
            )
            if cur.fetchone() is not None:
                return {"error": "space is already booked for that time"}, 409

            # Unpaid until POST /bookings/<id>/pay is called - unless the
            # member subscribes: then it is paid at once and costs nothing extra.
            subscribed = is_subscribed(cur, member)
            # Linked to the account if one is logged in; NULL for a guest.
            user_id = session.get("user_id")
            try:
                cur.execute(
                    "INSERT INTO bookings "
                    "(space_id, member, paid, start_time, end_time, "
                    "amount_cents, user_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING id, space_id, member, paid, start_time, end_time, "
                    "amount_cents, user_id, card_last4, created_at",
                    (
                        space_id,
                        member,
                        subscribed,
                        start_time,
                        end_time,
                        0
                        if subscribed
                        else calculate_booking_price_cents(
                            space["price_cents"], start_time, end_time
                        ),
                        user_id,
                    ),
                )
            except (DeadlockDetected, ExclusionViolation):
                # The pre-check above already caught this in the common
                # case; this only fires when two requests raced past it.
                return {"error": "space is already booked for that time"}, 409
            row = cur.fetchone()

        return booking_to_json(row), 201

    @app.post("/spaces/<int:space_id>/bookings")
    def create_booking(space_id):
        body = request.get_json(silent=True) or {}
        start_time = parse_time(body.get("start_time"))
        end_time = parse_time(body.get("end_time"))

        if start_time is None or end_time is None:
            return jsonify(
                error="start_time and end_time are required "
                "(ISO 8601 with timezone)"
            ), 400

        payload, status = book_space(
            space_id,
            body.get("member", "guest"),
            start_time,
            end_time,
            body.get("party_size", 1),
        )
        return jsonify(payload), status

    @app.post("/spaces/<int:space_id>/book")
    def book_from_form(space_id):
        """The homepage form posts here, then we send the browser back to
        the space list - with a message, since a form can't read JSON."""
        member = request.form.get("member", "").strip() or "guest"
        start_time = parse_form_time(request.form.get("start_time"))
        end_time = parse_form_time(request.form.get("end_time"))

        if start_time is None or end_time is None:
            return redirect(url_for("index", error="fill in a start and end time"))

        payload, status = book_space(space_id, member, start_time, end_time, 1)
        if status >= 400:
            return redirect(url_for("index", error=payload["error"]))

        return redirect(url_for("booking_confirmation", booking_id=payload["id"]))

    @app.get("/bookings/<int:booking_id>/confirmation")
    def booking_confirmation(booking_id):
        """What a member sees after booking: the details, a Pay button and,
        once paid, an Unlock button that shows the access code."""
        with app.db.cursor() as cur:
            cur.execute(
                "SELECT b.id, b.member, b.paid, b.start_time, b.end_time, "
                "b.amount_cents, b.card_last4, s.name AS space_name "
                "FROM bookings b JOIN spaces s ON s.id = b.space_id "
                "WHERE b.id = %s",
                (booking_id,),
            )
            booking = cur.fetchone()

        if booking is None:
            return render_template("booking_not_found.html"), 404

        return render_template(
            "confirmation.html",
            booking=booking,
            code=request.args.get("code"),
            error=request.args.get("error"),
        )



    @app.get("/bookings/mine")
    def my_bookings():
        """What a logged-in member sees: every booking they made, with a
        link to each one's confirmation page (Pay or Unlock, whichever
        applies)."""
        if session.get("user_id") is None:
            return redirect(url_for("login_form"))

        with app.db.cursor() as cur:
            cur.execute(
                "SELECT b.id, b.paid, b.start_time, b.end_time, b.amount_cents, "
                "s.name AS space_name "
                "FROM bookings b JOIN spaces s ON s.id = b.space_id "
                "WHERE b.user_id = %s ORDER BY b.start_time",
                (session["user_id"],),
            )
            rows = cur.fetchall()

        return render_template("my_bookings.html", bookings=rows)

    @app.get("/bookings")
    def list_bookings():
        with app.db.cursor() as cur:
            cur.execute(
                "SELECT id, space_id, member, paid, start_time, end_time, amount_cents, user_id, card_last4, created_at "
                "FROM bookings ORDER BY start_time"
            )
            rows = cur.fetchall()

        return jsonify(bookings=[booking_to_json(row) for row in rows])

    @app.get("/bookings/<int:booking_id>")
    def find_booking(booking_id):
        with app.db.cursor() as cur:
            cur.execute(
                "SELECT id, space_id, member, paid, start_time, end_time, amount_cents, user_id, card_last4, created_at "
                "FROM bookings WHERE id = %s",
                (booking_id,),
            )
            row = cur.fetchone()

        if row is None:
            return jsonify(error="booking not found"), 404

        return jsonify(booking_to_json(row))

    @app.delete("/bookings/<int:booking_id>")
    def cancel_booking(booking_id):
        with app.db.cursor() as cur:
            cur.execute(
                "DELETE FROM bookings WHERE id = %s "
                "RETURNING id, space_id, member, paid, start_time, end_time, amount_cents, user_id, card_last4, created_at",
                (booking_id,),
            )
            row = cur.fetchone()

        if row is None:
            return jsonify(error="booking not found"), 404

        return jsonify(booking_to_json(row))





    @app.post("/members/<name>/subscribe")
    def subscribe_member(name):
        # Mocked, like payment: no provider, always succeeds. Subscribing
        # again is a no-op, so a retried request can't break anything.
        member = member_key(name)
        if not member:
            return jsonify(error="member name must not be blank"), 400

        with app.db.cursor() as cur:
            cur.execute(
                "INSERT INTO subscriptions (member) VALUES (%s) "
                "ON CONFLICT (member) DO UPDATE SET active = TRUE "
                "RETURNING member, active, started_at",
                (member,),
            )
            row = cur.fetchone()

        return jsonify(
            member=row["member"],
            active=row["active"],
            started_at=row["started_at"].astimezone(timezone.utc).isoformat(),
        )

    @app.get("/metrics")
    def metrics():
        with app.db.cursor() as cur:
            data = compute_metrics(cur)

        return jsonify(**data)

    @app.get("/dashboard")
    def dashboard():
        with app.db.cursor() as cur:
            data = compute_metrics(cur)

        # Bar length is relative to the top-earning space (that one is 100%).
        top = max((row["revenue_cents"] for row in data["revenue_by_space"]), default=0)
        bars = [
            {
                "name": row["name"],
                "revenue_cents": row["revenue_cents"],
                "width": round(row["revenue_cents"] / top * 100) if top else 0,
            }
            for row in data["revenue_by_space"]
        ]

        return render_template("dashboard.html", metrics=data, bars=bars)
    
    return app
app = create_app()

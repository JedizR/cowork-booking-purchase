import threading
from datetime import datetime, timedelta, timezone

import pytest

from app import create_app

NOW = datetime.now(timezone.utc)

def make_client():
    return create_app(reset_on_start=True).test_client()

def slot(start_hours, end_hours):
    """Booking times relative to now, e.g. slot(-1, 1) is happening right now."""
    return {
        "start_time": (NOW + timedelta(hours=start_hours)).isoformat(),
        "end_time": (NOW + timedelta(hours=end_hours)).isoformat(),
    }

# A mocked, obviously-fake card - always valid, never a real payment.
VALID_CARD = {"card_number": "4242424242424242", "expiry": "12/30", "cvc": "123"}

def test_homepage_lists_spaces_with_availability():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )
    client.post("/spaces/2/bookings", json={"member": "member-b", **slot(-1, 1)})

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Founders Desk" in body
    assert "free right now" in body
    assert "Meeting Room A" in body
    assert "$15.00" in body
    assert "booked right now" in body

BANGKOK = timezone(timedelta(hours=7))

def form_slot(start_hours, end_hours):
    """What the browser's datetime-local fields send: a local (Bangkok)
    wall-clock time with no timezone on it."""
    local_now = NOW.astimezone(BANGKOK)
    return {
        "start_time": (local_now + timedelta(hours=start_hours)).strftime(
            "%Y-%m-%dT%H:%M"
        ),
        "end_time": (local_now + timedelta(hours=end_hours)).strftime(
            "%Y-%m-%dT%H:%M"
        ),
    }

def test_homepage_shows_a_booking_form_for_every_space():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})
    client.post("/spaces/2/bookings", json={"member": "member-b", **slot(-1, 1)})

    body = client.get("/").get_data(as_text=True)

    assert 'name="member"' in body
    # space 2 is booked right now, but you can still book it for later
    assert '<form method="post" action="/spaces/1/book">' in body
    assert '<form method="post" action="/spaces/2/book">' in body

def test_homepage_lists_the_upcoming_booked_times_of_a_space():
    client = make_client()
    client.post(
        "/spaces/1/bookings",
        json={
            "member": "member-b",
            "start_time": "2030-01-01T10:00:00+07:00",
            "end_time": "2030-01-01T11:00:00+07:00",
        },
    )

    body = client.get("/").get_data(as_text=True)

    assert "Already booked:" in body
    assert "2030-01-01 10:00 to 2030-01-01 11:00" in body

def test_homepage_says_when_a_space_has_no_bookings_coming_up():
    client = make_client()

    body = client.get("/").get_data(as_text=True)

    assert "No bookings coming up." in body

def test_can_book_a_later_slot_while_the_space_is_booked_right_now():
    client = make_client()
    client.post("/spaces/1/book", data={"member": "member-a", **form_slot(-1, 1)})

    response = client.post(
        "/spaces/1/book", data={"member": "member-b", **form_slot(4, 5)}
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/bookings/2/confirmation"

def test_booking_an_overlapping_slot_shows_the_already_booked_message():
    client = make_client()
    client.post("/spaces/1/book", data={"member": "member-a", **form_slot(1, 3)})

    response = client.post(
        "/spaces/1/book", data={"member": "member-b", **form_slot(2, 4)}
    )

    body = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "space is already booked for that time" in body

def test_booking_through_the_form_makes_the_space_unavailable():
    client = make_client()

    response = client.post(
        "/spaces/1/book", data={"member": "member-a", **form_slot(-1, 1)}
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/bookings/1/confirmation"

    body = client.get("/").get_data(as_text=True)
    assert "booked right now" in body
    # still bookable for a later slot, so the form stays
    assert '<form method="post" action="/spaces/1/book">' in body

    booking = client.get("/bookings/1").get_json()
    assert booking["member"] == "member-a"
    assert booking["paid"] is False



def test_confirmation_page_for_unknown_booking_returns_404():
    client = make_client()

    response = client.get("/bookings/999/confirmation")

    assert response.status_code == 404
    assert "Booking not found" in response.get_data(as_text=True)

def test_confirmation_page_shows_times_in_bangkok_time():
    client = make_client()
    client.post(
        "/spaces/1/book",
        data={
            "member": "member-a",
            "start_time": "2026-09-25T09:00",
            "end_time": "2026-09-25T10:00",
        },
    )

    page = client.get("/bookings/1/confirmation").get_data(as_text=True)

    assert "2026-09-25 09:00" in page
    assert "2026-09-25 10:00" in page


def test_three_hours_cost_three_times_one_hour():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )

    one_hour = client.post(
        "/spaces/2/bookings", json={"member": "member-a", **slot(1, 2)}
    ).get_json()
    three_hours = client.post(
        "/spaces/2/bookings", json={"member": "member-b", **slot(4, 7)}
    ).get_json()

    assert one_hour["amount_cents"] == 1500
    assert three_hours["amount_cents"] == 4500

def test_half_an_hour_costs_half_the_hourly_rate():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )

    booking = client.post(
        "/spaces/2/bookings", json={"member": "member-a", **slot(1, 1.5)}
    ).get_json()

    assert booking["amount_cents"] == 750

def test_an_uneven_duration_is_rounded_to_whole_cents():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1000},
    )

    # 20 minutes at $10.00/hour is 333.33 cents
    booking = client.post(
        "/spaces/2/bookings",
        json={"member": "member-a", **slot(1, 1 + 1 / 3)},
    ).get_json()

    assert booking["amount_cents"] == 333

def test_homepage_shows_the_price_as_an_hourly_rate():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )

    body = client.get("/").get_data(as_text=True)

    assert "$15.00 per hour" in body


def test_a_price_change_after_booking_does_not_change_the_amount_shown():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6, "price_cents": 500})
    created = client.post(
        "/spaces/2/bookings", json={"member": "member-a", **slot(1, 2)}
    ).get_json()

    client.patch("/spaces/2", json={"price_cents": 2000})

    assert client.get(f"/bookings/{created['id']}").get_json()["amount_cents"] == 500

def test_a_subscribers_booking_shows_zero_amount_on_the_confirmation_page():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500})
    client.post("/members/member-a/subscribe")

    client.post("/spaces/2/book", data={"member": "member-a", **form_slot(1, 2)})
    page = client.get("/bookings/1/confirmation").get_data(as_text=True)

    assert "Paid. Total: $0.00" in page

def test_form_times_are_read_as_bangkok_time():
    client = make_client()

    client.post(
        "/spaces/1/book",
        data={
            "member": "member-a",
            "start_time": "2026-09-25T09:00",
            "end_time": "2026-09-25T10:00",
        },
    )

    booking = client.get("/bookings/1").get_json()
    # 09:00 in Bangkok (UTC+7) is 02:00 UTC
    assert booking["start_time"] == "2026-09-25T02:00:00+00:00"
    assert booking["end_time"] == "2026-09-25T03:00:00+00:00"

def test_form_booking_a_taken_slot_shows_the_error():
    client = make_client()
    client.post("/spaces/1/book", data={"member": "member-a", **form_slot(1, 3)})

    response = client.post(
        "/spaces/1/book", data={"member": "member-b", **form_slot(2, 4)}
    )

    assert response.status_code == 302
    body = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "space is already booked for that time" in body

def test_form_without_times_shows_an_error():
    client = make_client()

    response = client.post("/spaces/1/book", data={"member": "member-a"})

    body = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "fill in a start and end time" in body

def test_homepage_escapes_space_names():
    client = make_client()
    client.post(
        "/spaces", json={"name": "<script>alert(1)</script>", "capacity": 1}
    )

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body

def test_health_reports_running_revision(monkeypatch):
    monkeypatch.setenv("APP_REVISION", "test-revision")
    client = make_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "revision": "test-revision",
        "status": "ok",
    }

def test_list_spaces_returns_the_seed_space():
    client = make_client()

    response = client.get("/spaces")

    assert response.status_code == 200
    assert response.get_json() == {
        "spaces": [
            {
                "id": 1, 
                "name": "Founders Desk",
                "capacity": 1,
                "price_cents": 2500,
                "available": True
             }
        ]
    }

def test_create_space_adds_a_new_space():
    client = make_client()

    response = client.post(
        "/spaces", json={"name": "Meeting Room A", "capacity": 6}
    )

    assert response.status_code == 201
    assert response.get_json() == {
        "id": 2,
        "name": "Meeting Room A",
        "capacity": 6,
        "price_cents": 0,
    }

    listed = client.get("/spaces").get_json()["spaces"]
    assert {
        "id": 2,
        "name": "Meeting Room A",
        "capacity": 6,
        "price_cents": 0,
        "available": True,
    } in listed

def test_create_space_with_a_price_stores_it():
    client = make_client()

    response = client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )

    assert response.status_code == 201
    assert response.get_json() == {
        "id": 2,
        "name": "Meeting Room A",
        "capacity": 6,
        "price_cents": 1500,
    }

def test_create_space_with_negative_price_is_rejected():
    client = make_client()

    response = client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": -100},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "price_cents must be a non-negative integer"
    }

def test_create_space_with_a_boolean_price_is_rejected():
    client = make_client()

    for price in [True, False]:
        response = client.post(
            "/spaces",
            json={"name": "Meeting Room A", "capacity": 6, "price_cents": price},
        )

        assert response.status_code == 400
        assert response.get_json() == {
            "error": "price_cents must be a non-negative integer"
        }

def test_create_space_without_capacity_is_rejected():
    client = make_client()

    response = client.post("/spaces", json={"name": "No Capacity Room"})

    assert response.status_code == 400
    assert response.get_json() == {"error": "name and capacity are required"}

def test_create_space_with_empty_name_is_rejected():
    client = make_client()

    for name in ["", "   ", 42]:
        response = client.post("/spaces", json={"name": name, "capacity": 6})

        assert response.status_code == 400
        assert response.get_json() == {"error": "name must not be empty"}

def test_create_space_with_invalid_capacity_is_rejected():
    client = make_client()

    for capacity in [0, -5, "6", 2.5, True]:
        response = client.post(
            "/spaces", json={"name": "Meeting Room A", "capacity": capacity}
        )

        assert response.status_code == 400
        assert response.get_json() == {
            "error": "capacity must be a whole number of at least 1"
        }

    # nothing got created - only the seed space is there
    assert len(client.get("/spaces").get_json()["spaces"]) == 1

def test_create_space_trims_spaces_around_the_name():
    client = make_client()

    response = client.post(
        "/spaces", json={"name": "  Meeting Room A  ", "capacity": 6}
    )

    assert response.status_code == 201
    assert response.get_json()["name"] == "Meeting Room A"


def test_create_booking_for_unknown_space_returns_404():
    client = make_client()

    response = client.post(
        "/spaces/999/bookings", json={"member": "member-a", **slot(1, 2)}
    )

    #404 to reject booking a space that doesn't exist
    assert response.status_code == 404
    assert response.get_json() == {"error": "space not found"}

def test_create_booking_without_times_is_rejected():
    client = make_client()

    response = client.post("/spaces/1/bookings", json={"member": "member-a"})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "start_time and end_time are required (ISO 8601 with timezone)"
    }

def test_create_booking_ending_before_it_starts_is_rejected():
    client = make_client()

    response = client.post(
        "/spaces/1/bookings", json={"member": "member-a", **slot(2, 1)}
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "end_time must be after start_time"}

def test_overlapping_booking_is_rejected():
    client = make_client()

    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(1, 3)})
    response = client.post(
        "/spaces/1/bookings", json={"member": "member-b", **slot(2, 4)}
    )

    assert response.status_code == 409
    assert response.get_json() == {"error": "space is already booked for that time"}

def test_concurrent_bookings_for_the_same_slot_only_one_succeeds():
    # Two separate app instances = two separate real DB connections, like
    # two simultaneous requests would get in production. This is what
    # actually exercises the race (see issue #50) rather than just the
    # single-connection check-then-insert code path.
    client_a = make_client()
    client_b = create_app(reset_on_start=False).test_client()

    booking = {"member": "racer", **slot(1, 2)}
    results = [None, None]
    start_barrier = threading.Barrier(2)

    def book(client, index):
        start_barrier.wait()
        results[index] = client.post("/spaces/1/bookings", json=booking)

    threads = [
        threading.Thread(target=book, args=(client_a, 0)),
        threading.Thread(target=book, args=(client_b, 1)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    statuses = sorted(r.status_code for r in results)
    assert statuses == [201, 409]

def test_back_to_back_bookings_are_allowed():
    client = make_client()

    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)})
    response = client.post(
        "/spaces/1/bookings", json={"member": "member-b", **slot(2, 3)}
    )

    assert response.status_code == 201

def test_space_booked_right_now_shows_as_unavailable():
    client = make_client()

    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(-1, 1)})
    response = client.get("/spaces")

    spaces = response.get_json()["spaces"]
    founders_desk = next(s for s in spaces if s["id"] == 1)
    assert founders_desk["available"] is False

def test_space_booked_only_later_is_still_available_now():
    client = make_client()

    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(24, 25)})
    response = client.get("/spaces")

    spaces = response.get_json()["spaces"]
    founders_desk = next(s for s in spaces if s["id"] == 1)
    assert founders_desk["available"] is True

def test_find_booking_returns_the_booking():
    client = make_client()
    created = client.post(
        "/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)}
    ).get_json()

    response = client.get(f"/bookings/{created['id']}")

    assert response.status_code == 200
    body = response.get_json()
    body.pop("created_at")
    assert body == {
        "id": 1,
        "space_id": 1,
        "member": "member-a",
        "paid": False,
        "amount_cents": 2500,
        "user_id": None,
        "card_last4": None,
        **slot(1, 2),
    }


def test_find_unknown_booking_returns_404():
    client = make_client()

    response = client.get("/bookings/999")

    assert response.status_code == 404
    assert response.get_json() == {"error": "booking not found"}

def test_cancel_booking_makes_space_available_again():
    client = make_client()
    created = client.post(
        "/spaces/1/bookings", json={"member": "member-a", **slot(-1, 1)}
    ).get_json()

    response = client.delete(f"/bookings/{created['id']}")

    assert response.status_code == 200
    assert response.get_json() == created

    spaces = client.get("/spaces").get_json()["spaces"]
    founders_desk = next(s for s in spaces if s["id"] == 1)
    assert founders_desk["available"] is True

    assert client.get(f"/bookings/{created['id']}").status_code == 404

def test_cancel_unknown_booking_returns_404():
    client = make_client()

    response = client.delete("/bookings/999")

    assert response.status_code == 404
    assert response.get_json() == {"error": "booking not found"}



















def test_registering_creates_an_account():
    client = make_client()

    response = client.post(
        "/register", json={"email": "A@Example.com", "password": "hunter22"}
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body == {"id": 1, "email": "a@example.com"}
    assert "password" not in body
    assert "password_hash" not in body

def test_registering_does_not_store_the_plain_password():
    client = make_client()
    app = create_app(reset_on_start=False)
    client.post("/register", json={"email": "a@example.com", "password": "hunter22"})

    with app.db.cursor() as cur:
        cur.execute("SELECT password_hash FROM users WHERE email = 'a@example.com'")
        stored = cur.fetchone()["password_hash"]

    assert stored != "hunter22"
    assert "hunter22" not in stored

def test_registering_with_a_duplicate_email_is_rejected():
    client = make_client()
    client.post("/register", json={"email": "a@example.com", "password": "hunter22"})

    # case shouldn't matter either
    response = client.post(
        "/register", json={"email": "A@Example.com", "password": "different"}
    )

    assert response.status_code == 409
    assert response.get_json() == {"error": "email is already registered"}

def test_registering_with_a_bad_email_is_rejected():
    client = make_client()

    for email in ["not-an-email", "missing-domain@", "@missing-local.com", "", None, 42]:
        response = client.post(
            "/register", json={"email": email, "password": "hunter22"}
        )

        assert response.status_code == 400
        assert response.get_json() == {"error": "enter a valid email address"}

def test_registering_with_a_short_password_is_rejected():
    client = make_client()

    for password in ["short", "", None, 12345678]:
        response = client.post(
            "/register", json={"email": "a@example.com", "password": password}
        )

        assert response.status_code == 400
        assert response.get_json() == {
            "error": "password must be at least 8 characters"
        }

    # nothing got created
    assert client.get("/register").status_code == 200

def test_register_form_creates_an_account_and_redirects_with_a_message():
    client = make_client()

    response = client.post(
        "/register", data={"email": "a@example.com", "password": "hunter22"}
    )

    assert response.status_code == 302
    body = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Registered!" in body

def test_register_form_with_invalid_input_shows_the_error():
    client = make_client()

    response = client.post(
        "/register", data={"email": "not-an-email", "password": "hunter22"}
    )

    body = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "enter a valid email address" in body

def test_homepage_links_to_register():
    client = make_client()

    body = client.get("/").get_data(as_text=True)

    assert 'href="/register"' in body

def register(client, email="a@example.com", password="hunter22"):
    return client.post("/register", json={"email": email, "password": password})

def test_login_with_the_right_password_succeeds():
    client = make_client()
    register(client)

    response = client.post(
        "/login", json={"email": "A@Example.com", "password": "hunter22"}
    )

    assert response.status_code == 200
    assert response.get_json() == {"id": 1, "email": "a@example.com"}

def test_login_with_the_wrong_password_is_rejected():
    client = make_client()
    register(client)

    response = client.post(
        "/login", json={"email": "a@example.com", "password": "wrong-password"}
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid email or password"}

def test_login_with_an_unknown_email_gives_the_identical_error():
    client = make_client()
    register(client)

    known = client.post(
        "/login", json={"email": "a@example.com", "password": "wrong-password"}
    )
    unknown = client.post(
        "/login", json={"email": "nobody@example.com", "password": "hunter22"}
    )

    # a wrong password and an unregistered email must look the same,
    # otherwise a login attempt can be used to find out who has an account
    assert known.status_code == unknown.status_code == 401
    assert known.get_json() == unknown.get_json()

def test_logout_ends_the_session():
    client = make_client()
    register(client)
    client.post("/login", json={"email": "a@example.com", "password": "hunter22"})
    assert "Logged in as a@example.com" in client.get("/").get_data(as_text=True)

    response = client.post("/logout", json={})

    assert response.status_code == 200
    assert "Logged in as" not in client.get("/").get_data(as_text=True)
    assert 'href="/login"' in client.get("/").get_data(as_text=True)

def test_logout_when_not_logged_in_is_a_no_op():
    client = make_client()

    response = client.post("/logout", json={})

    assert response.status_code == 200

def test_login_form_logs_in_and_redirects_to_the_homepage():
    client = make_client()
    register(client)

    response = client.post(
        "/login", data={"email": "a@example.com", "password": "hunter22"}
    )

    assert response.status_code == 302
    body = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Logged in as a@example.com" in body

def test_login_form_with_wrong_password_shows_the_error():
    client = make_client()
    register(client)

    response = client.post(
        "/login", data={"email": "a@example.com", "password": "wrong-password"}
    )

    body = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "invalid email or password" in body

def test_logout_button_on_the_homepage_actually_logs_out():
    client = make_client()
    register(client)
    client.post("/login", json={"email": "a@example.com", "password": "hunter22"})

    response = client.post("/logout")
    body = client.get(response.headers["Location"]).get_data(as_text=True)

    assert "Logged in as" not in body

def login(client, email="a@example.com", password="hunter22"):
    return client.post("/login", json={"email": email, "password": password})

def test_a_booking_made_while_logged_in_is_linked_to_the_account():
    client = make_client()
    register(client)
    user = login(client).get_json()

    created = client.post(
        "/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)}
    ).get_json()

    assert created["user_id"] == user["id"]
    assert client.get(f"/bookings/{created['id']}").get_json()["user_id"] == user["id"]

def test_a_guest_booking_has_no_user_id():
    client = make_client()

    created = client.post(
        "/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)}
    ).get_json()

    assert created["user_id"] is None

def test_logging_out_before_booking_leaves_it_unlinked():
    client = make_client()
    register(client)
    login(client)
    client.post("/logout", json={})

    created = client.post(
        "/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)}
    ).get_json()

    assert created["user_id"] is None

def test_a_form_booking_while_logged_in_is_linked_to_the_account():
    client = make_client()
    register(client)
    user = login(client).get_json()

    client.post("/spaces/1/book", data={"member": "member-a", **form_slot(1, 2)})

    assert client.get("/bookings/1").get_json()["user_id"] == user["id"]


def test_logged_out_visitors_are_sent_to_the_login_page():
    client = make_client()

    response = client.get("/bookings/mine")

    assert response.status_code == 302
    assert response.headers["Location"] == "/login"

def test_my_bookings_page_lists_only_the_logged_in_users_bookings():
    client = make_client()
    client.post(
        "/spaces", json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500}
    )
    register(client, email="a@example.com")
    register(client, email="b@example.com")

    login(client, email="a@example.com")
    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)})
    client.post("/logout", json={})

    login(client, email="b@example.com")
    client.post("/spaces/2/bookings", json={"member": "member-b", **slot(3, 4)})

    body = client.get("/bookings/mine").get_data(as_text=True)

    assert "Meeting Room A" in body
    assert "Founders Desk" not in body  # that one is member-a's, not member-b's



def test_my_bookings_page_when_there_are_none_says_so():
    client = make_client()
    register(client)
    login(client)

    body = client.get("/bookings/mine").get_data(as_text=True)

    assert "No bookings yet." in body

def test_homepage_links_to_my_bookings_only_when_logged_in():
    client = make_client()

    assert 'href="/bookings/mine"' not in client.get("/").get_data(as_text=True)

    register(client)
    login(client)

    assert 'href="/bookings/mine"' in client.get("/").get_data(as_text=True)

def test_subscribing_returns_an_active_subscription():
    client = make_client()

    response = client.post("/members/member-a/subscribe")

    assert response.status_code == 200
    body = response.get_json()
    assert body["member"] == "member-a"
    assert body["active"] is True
    assert body["started_at"]

def test_subscribing_twice_keeps_the_original_start():
    client = make_client()
    first = client.post("/members/member-a/subscribe").get_json()

    second = client.post("/members/member-a/subscribe")

    assert second.status_code == 200
    assert second.get_json() == first

def test_a_blank_member_name_cannot_subscribe():
    client = make_client()

    response = client.post("/members/%20/subscribe")

    assert response.status_code == 400
    assert response.get_json() == {"error": "member name must not be blank"}



def test_subscription_matches_the_member_name_ignoring_case_and_spaces():
    client = make_client()
    client.post("/members/%20Annabel%20/subscribe")

    lower = client.post(
        "/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)}
    ).get_json()
    shouting = client.post(
        "/spaces/1/bookings", json={"member": "  MEMBER A ", **slot(3, 4)}
    ).get_json()

    assert lower["paid"] is True
    assert shouting["paid"] is True

def test_a_subscribers_booking_adds_no_per_booking_revenue():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )
    client.post("/members/member-a/subscribe")

    client.post("/spaces/2/bookings", json={"member": "member-a", **slot(1, 2)})
    metrics = client.get("/metrics").get_json()

    assert metrics["paid_bookings"] == 1
    assert metrics["revenue_cents"] == 0

def test_form_booking_by_a_subscriber_is_paid():
    client = make_client()
    client.post("/members/member-a/subscribe")

    client.post("/spaces/1/book", data={"member": "member-a", **form_slot(1, 2)})

    assert client.get("/bookings/1").get_json()["paid"] is True

def test_metrics_reports_spaces_bookings_and_members():
    client = make_client()

    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})
    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)})
    client.post("/spaces/2/bookings", json={"member": "member-b", **slot(1, 2)})

    response = client.get("/metrics")

    assert response.status_code == 200
    body = response.get_json()
    assert body["spaces"] == 2
    assert body["bookings"] == 2
    assert body["paid_bookings"] == 0
    assert body["unpaid_bookings"] == 2
    assert body["members"] == 2
    assert body["revenue_cents"] == 0


def test_metrics_with_no_bookings_reports_zero_for_each_count():
    client = make_client()

    body = client.get("/metrics").get_json()

    assert body["bookings"] == 0
    assert body["paid_bookings"] == 0
    assert body["unpaid_bookings"] == 0


def test_utilization_over_the_next_7_days():
    client = make_client()
    # Founders Desk (the only space) is booked for a clean 24 hours,
    # fully inside the 7-day (168h) window.
    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(1, 25)})

    body = client.get("/metrics").get_json()

    assert body["utilization"] == pytest.approx(24 / 168)

def test_utilization_ignores_bookings_outside_the_next_7_days():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(200, 201)})

    body = client.get("/metrics").get_json()

    assert body["utilization"] == 0.0

def test_utilization_is_zero_with_no_spaces():
    client = make_client()
    client.delete("/spaces/1")  # the only space, unbooked, so deletable

    body = client.get("/metrics").get_json()

    assert body["spaces"] == 0
    assert body["utilization"] == 0.0

def test_repeat_member_rate():
    client = make_client()
    client.post("/spaces", json={"name": "Room B", "capacity": 4})
    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)})
    client.post("/spaces/2/bookings", json={"member": "member-a", **slot(3, 4)})
    client.post("/spaces/2/bookings", json={"member": "member-b", **slot(5, 6)})

    body = client.get("/metrics").get_json()

    assert body["members"] == 2
    assert body["repeat_member_rate"] == pytest.approx(1 / 2)  # only member-a repeats

def test_repeat_member_rate_is_zero_with_no_bookings():
    client = make_client()

    body = client.get("/metrics").get_json()

    assert body["repeat_member_rate"] == 0.0




def test_average_revenue_per_paid_booking_is_zero_with_no_paid_bookings():
    client = make_client()

    body = client.get("/metrics").get_json()

    assert body["avg_revenue_cents_per_paid_booking"] == 0


def test_revenue_by_space_includes_a_space_with_no_bookings():
    client = make_client()

    body = client.get("/metrics").get_json()

    assert body["revenue_by_space"] == [
        {"id": 1, "name": "Founders Desk", "revenue_cents": 0}
    ]


def test_startup_fills_in_the_amount_on_bookings_made_before_the_column_existed():
    app = create_app(reset_on_start=True)
    client = app.test_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 1500},
    )
    with app.db.cursor() as cur:
        # a paid booking from before amount_cents existed, so it has no amount
        cur.execute(
            "INSERT INTO bookings (space_id, member, paid, start_time, end_time) "
            "VALUES (2, 'old-member', TRUE, "
            "now() + interval '1 day', now() + interval '2 days')"
        )
        cur.execute("SELECT amount_cents FROM bookings")
        assert cur.fetchone()["amount_cents"] is None

    create_app(reset_on_start=False)  # the next app start runs the backfill

    with app.db.cursor() as cur:
        cur.execute("SELECT amount_cents FROM bookings")
        assert cur.fetchone()["amount_cents"] == 1500
    assert client.get("/metrics").get_json()["revenue_cents"] == 1500

def test_health_reports_error_when_database_is_unreachable():
    app = create_app(reset_on_start=True)
    app.db.close()  # simulate a lost/broken database connection
    client = app.test_client()

    response = client.get("/health")

    assert response.status_code == 503
    assert response.get_json() == {
        "status": "error",
        "error": "database unreachable",
    }

def test_startup_fails_fast_with_a_clear_message_for_a_bad_database_url():
    bad_url = "postgresql://cowork:cowork@localhost:1/cowork"

    with pytest.raises(SystemExit) as exc_info:
        create_app(database_url=bad_url)

    message = str(exc_info.value)
    assert bad_url in message
    assert "docker compose up db -d" in message



def test_dashboard_with_no_bookings_shows_zero_paid_and_unpaid():
    client = make_client()

    body = client.get("/dashboard").get_data(as_text=True)

    assert "Paid bookings: 0" in body
    assert "Unpaid bookings: 0" in body

def test_dashboard_describes_the_product_and_notes_the_data_source():
    client = make_client()

    body = client.get("/dashboard").get_data(as_text=True)

    assert "space booking system" in body
    assert "test and load-test data" in body



def test_dashboard_with_no_spaces_shows_no_bar_chart_crash():
    client = make_client()
    client.delete("/spaces/1")

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "No spaces yet." in response.get_data(as_text=True)

def test_delete_unbooked_space_removes_it():
    client = make_client()
    created = client.post(
        "/spaces", json={"name": "Temp Room", "capacity": 3}
    ).get_json()

    response = client.delete(f"/spaces/{created['id']}")

    assert response.status_code == 204
    listed = client.get("/spaces").get_json()["spaces"]
    assert all(s["id"] != created["id"] for s in listed)

def test_delete_space_with_bookings_is_rejected():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)})

    response = client.delete("/spaces/1")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "space has bookings, cancel them first"
    }

def test_delete_unknown_space_returns_404():
    client = make_client()

    response = client.delete("/spaces/999")

    assert response.status_code == 404
    assert response.get_json() == {"error": "space not found"}

def test_get_space_returns_its_data():
    client = make_client()

    response = client.get("/spaces/1")

    assert response.status_code == 200
    assert response.get_json() == {
        "id": 1,
        "name": "Founders Desk",
        "capacity": 1,
        "price_cents": 2500,
        "available": True,
    }

def test_get_unknown_space_returns_404():
    client = make_client()

    response = client.get("/spaces/999")

    assert response.status_code == 404
    assert response.get_json() == {"error": "space not found"}

def test_list_spaces_for_a_time_window_reflects_bookings_in_that_window():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(48, 50)})

    # free right now, so the default view says available
    assert client.get("/spaces").get_json()["spaces"][0]["available"] is True

    clash = client.get("/spaces", query_string=slot(47, 49)).get_json()
    free = client.get("/spaces", query_string=slot(60, 61)).get_json()

    assert clash["spaces"][0]["available"] is False
    assert free["spaces"][0]["available"] is True

def test_availability_window_replaces_the_right_now_check():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(-1, 1)})

    assert client.get("/spaces").get_json()["spaces"][0]["available"] is False

    later = client.get("/spaces", query_string=slot(5, 6)).get_json()

    assert later["spaces"][0]["available"] is True

def test_availability_window_touching_a_booking_is_still_free():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(-1, 1)})
    client.post("/spaces/1/bookings", json={"member": "member-b", **slot(3, 4)})

    # starts exactly when the first booking ends
    after = client.get("/spaces", query_string=slot(1, 2)).get_json()
    # ends exactly when the second booking starts
    before = client.get("/spaces", query_string=slot(2, 3)).get_json()

    assert after["spaces"][0]["available"] is True
    assert before["spaces"][0]["available"] is True

def test_get_space_for_a_time_window_reflects_bookings_in_that_window():
    client = make_client()
    client.post("/spaces/1/bookings", json={"member": "member-a", **slot(48, 50)})

    clash = client.get("/spaces/1", query_string=slot(49, 51))
    free = client.get("/spaces/1", query_string=slot(60, 61))

    assert clash.status_code == 200
    assert clash.get_json()["available"] is False
    assert free.get_json()["available"] is True

def test_availability_window_must_have_both_times_in_order():
    client = make_client()
    together = (
        "start_time and end_time must be given together "
        "(ISO 8601 with timezone, e.g. 2026-09-25T09:00:00Z)"
    )
    bad_windows = [
        ({"start_time": slot(1, 2)["start_time"]}, together),
        ({"start_time": "tomorrow", "end_time": "later"}, together),
        (
            {"start_time": "2026-09-25T09:00:00", "end_time": "2026-09-25T10:00:00"},
            together,  # no timezone
        ),
        (slot(2, 1), "end_time must be after start_time"),
    ]

    for path in ["/spaces", "/spaces/1"]:
        for query, message in bad_windows:
            response = client.get(path, query_string=query)

            assert response.status_code == 400
            assert response.get_json() == {"error": message}

def test_list_bookings_for_a_space_returns_all_of_them():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})
    later = client.post(
        "/spaces/1/bookings", json={"member": "member-b", **slot(3, 4)}
    ).get_json()
    earlier = client.post(
        "/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)}
    ).get_json()
    client.post("/spaces/2/bookings", json={"member": "member-c", **slot(1, 2)})

    response = client.get("/spaces/1/bookings")

    # only space 1's bookings, earliest first
    assert response.status_code == 200
    assert response.get_json() == {"bookings": [earlier, later]}

def test_list_bookings_for_space_without_bookings_is_empty():
    client = make_client()

    response = client.get("/spaces/1/bookings")

    assert response.status_code == 200
    assert response.get_json() == {"bookings": []}

def test_list_bookings_for_unknown_space_returns_404():
    client = make_client()

    response = client.get("/spaces/999/bookings")

    assert response.status_code == 404
    assert response.get_json() == {"error": "space not found"}

def test_booking_more_people_than_capacity_is_rejected():
    client = make_client()

    # Founders Desk (space 1) has capacity 1
    response = client.post(
        "/spaces/1/bookings",
        json={"member": "member-a", "party_size": 2, **slot(1, 2)},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "party_size 2 exceeds this space's capacity of 1"
    }
    assert client.get("/spaces/1/bookings").get_json() == {"bookings": []}

def test_booking_up_to_capacity_is_allowed():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})

    response = client.post(
        "/spaces/2/bookings",
        json={"member": "member-a", "party_size": 6, **slot(1, 2)},
    )

    assert response.status_code == 201

def test_booking_with_invalid_party_size_is_rejected():
    client = make_client()

    for party_size in [0, -3, "two", 1.5, True]:
        response = client.post(
            "/spaces/1/bookings",
            json={"member": "member-a", "party_size": party_size, **slot(1, 2)},
        )

        assert response.status_code == 400
        assert response.get_json() == {
            "error": "party_size must be a whole number of at least 1"
        }
def test_list_bookings_returns_bookings_across_all_spaces():
    client = make_client()
    client.post("/spaces", json={"name": "Meeting Room A", "capacity": 6})
    later = client.post(
        "/spaces/1/bookings", json={"member": "member-b", **slot(3, 4)}
    ).get_json()
    earlier = client.post(
        "/spaces/2/bookings", json={"member": "member-a", **slot(1, 2)}
    ).get_json()

    response = client.get("/bookings")

    # bookings from both spaces, earliest first
    assert response.status_code == 200
    assert response.get_json() == {"bookings": [earlier, later]}

def test_list_bookings_when_there_are_none_is_empty():
    client = make_client()

    response = client.get("/bookings")

    assert response.status_code == 200
    assert response.get_json() == {"bookings": []}

def test_update_space_name_shows_up_in_list():
    client = make_client()

    response = client.patch("/spaces/1", json={"name": "Founders Desk (Window)"})

    assert response.status_code == 200
    assert response.get_json() == {
        "id": 1,
        "name": "Founders Desk (Window)",
        "capacity": 1,
        "price_cents": 2500,
    }
    spaces = client.get("/spaces").get_json()["spaces"]
    assert spaces[0]["name"] == "Founders Desk (Window)"
    assert spaces[0]["capacity"] == 1  # untouched

def test_update_space_capacity_only_keeps_the_name():
    client = make_client()

    response = client.patch("/spaces/1", json={"capacity": 4})

    assert response.status_code == 200
    assert response.get_json()["name"] == "Founders Desk"
    assert response.get_json()["capacity"] == 4

def test_update_space_keeps_its_bookings():
    client = make_client()
    booking = client.post(
        "/spaces/1/bookings", json={"member": "member-a", **slot(1, 2)}
    ).get_json()

    client.patch("/spaces/1", json={"name": "Renamed Desk"})

    assert client.get("/spaces/1/bookings").get_json() == {"bookings": [booking]}

def test_update_space_price_shows_up_in_list():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 500},
    )

    response = client.patch("/spaces/2", json={"price_cents": 2000})

    assert response.status_code == 200
    assert response.get_json() == {
        "id": 2,
        "name": "Meeting Room A",
        "capacity": 6,
        "price_cents": 2000,
    }
    listed = client.get("/spaces").get_json()["spaces"]
    assert next(s for s in listed if s["id"] == 2)["price_cents"] == 2000

    # 0 is a valid price: the space becomes free
    free = client.patch("/spaces/2", json={"price_cents": 0})
    assert free.get_json()["price_cents"] == 0

def test_update_space_name_only_keeps_the_price():
    client = make_client()
    client.post(
        "/spaces",
        json={"name": "Meeting Room A", "capacity": 6, "price_cents": 500},
    )

    response = client.patch("/spaces/2", json={"name": "Meeting Room B"})

    assert response.get_json()["price_cents"] == 500


def test_update_unknown_space_returns_404():
    client = make_client()

    response = client.patch("/spaces/999", json={"name": "Ghost Room"})

    assert response.status_code == 404
    assert response.get_json() == {"error": "space not found"}

def test_update_space_with_invalid_values_is_rejected():
    client = make_client()

    price_error = "price_cents must be a non-negative integer"
    cases = [
        ({}, "provide name, capacity and/or price_cents to update"),
        ({"name": "   "}, "name must not be empty"),
        ({"name": None}, "name must not be empty"),
        ({"capacity": 0}, "capacity must be a whole number of at least 1"),
        ({"capacity": "4"}, "capacity must be a whole number of at least 1"),
        ({"price_cents": -1}, price_error),
        ({"price_cents": "5"}, price_error),
        ({"price_cents": 2.5}, price_error),
        ({"price_cents": None}, price_error),
        ({"price_cents": True}, price_error),
    ]
    for body, error in cases:
        response = client.patch("/spaces/1", json=body)

        assert response.status_code == 400
        assert response.get_json() == {"error": error}

    # nothing changed
    space = client.get("/spaces/1").get_json()
    assert space["name"] == "Founders Desk"
    assert space["price_cents"] == 2500


def test_homepage_links_to_dashboard():
    client = make_client()

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'href="/dashboard"' in body

def test_dashboard_links_back_to_homepage():
    client = make_client()

    response = client.get("/dashboard")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'href="/"' in body
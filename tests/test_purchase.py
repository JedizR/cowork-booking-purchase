"""Purchase rules, each test named after the rule it proves (cowork-booking-docs RULES.md)."""
import re

from conftest import book_json, member, set_clock

PRICE = 45000  # Meeting Room A, THB 300 per hour, 3 blocks


def operator(app):
    return member(app, "operator@example.com", "Operator")


def test_pur_r01_register_rejects_duplicate_email(app):
    member(app)
    c = app.test_client()
    r = c.post("/register", data={"email": "A@example.com", "display_name": "Member A", "password": "x" * 8},
               follow_redirects=True)
    assert "Email already registered" in r.get_data(as_text=True)
    r = c.post("/register", data={"email": "b@example.com", "display_name": "B", "password": "short"},
               follow_redirects=True)
    assert "Password must be at least 8 characters" in r.get_data(as_text=True)


def test_pur_r02_login_has_one_uniform_error(app):
    member(app)
    c = app.test_client()
    for email, pw in (("a@example.com", "wrong-password"), ("nobody@example.com", "correct-horse")):
        r = c.post("/login", data={"email": email, "password": pw}, follow_redirects=True)
        assert "Invalid email or password" in r.get_data(as_text=True)


def test_pur_r03_session_ends_12_hours_after_login(app):
    a = member(app)
    assert a.get("/bookings/mine").status_code == 200
    set_clock(a, "2026-10-05T22:00:00+07:00")
    r = a.get("/api/bookings/mine")
    assert r.status_code == 401 and r.get_json()["error"]["message"] == "Please log in again"
    assert a.get("/bookings/mine").headers["Location"] == "/login"


def test_pur_r03_cookie_is_httponly_and_samesite_lax(app):
    c = app.test_client()
    c.post("/register", data={"email": "a@example.com", "display_name": "Member A", "password": "correct-horse"})
    r = c.post("/login", data={"email": "a@example.com", "password": "correct-horse"})
    cookie = r.headers["Set-Cookie"]
    assert cookie.startswith("purchase_session=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_pur_r04_operator_promoted_by_operator_email(app):
    op = operator(app)
    assert op.get("/operator/bookings").status_code == 200
    assert "Payment totals" in op.get("/").get_data(as_text=True)


def test_pur_r05_anonymous_booking_goes_to_login_and_keeps_the_page(app):
    c = app.test_client()
    r = c.post("/spaces/1/book", data={"date": "2026-10-07", "start": "09:00", "blocks": "3", "party_size": "2"})
    assert r.status_code == 303 and r.headers["Location"] == "/login"
    c.post("/register", data={"email": "a@example.com", "display_name": "Member A", "password": "correct-horse"})
    r = c.post("/login", data={"email": "a@example.com", "password": "correct-horse"})
    assert r.headers["Location"] == "/spaces/1?date=2026-10-07&blocks=3"


def test_pur_r05_non_owner_gets_404(app):
    a, b = member(app), member(app, "b@example.com", "Member B")
    ref = book_json(a).get_json()["reference"]
    assert b.get(f"/bookings/{ref}").status_code == 404
    assert b.get(f"/api/bookings/{ref}").get_json()["error"]["code"] == "not_found"
    assert b.post(f"/api/bookings/{ref}/cancel", json={}).status_code == 404
    assert a.get(f"/api/bookings/{ref}").status_code == 200


def test_pur_r06_operator_pages_are_404_for_members(app):
    a = member(app)
    for path in ("/operator/bookings", "/operator/spaces", "/operator/members", "/dashboard"):
        assert a.get(path).status_code == 404


def test_pur_r07_json_time_needs_an_offset(app):
    a = member(app)
    r = book_json(a, start="2026-10-07T09:00:00")
    assert r.status_code == 400 and r.get_json()["error"]["message"] == "Time needs an offset, for example +07:00"


def test_pur_r08_r09_shape_checks(app):
    a = member(app)
    assert book_json(a, start="2026-10-07T09:15:00+07:00").get_json()["error"]["message"] == "Start on :00 or :30"
    assert book_json(a, start="2026-10-07T19:30:00+07:00", blocks=2).get_json()["error"]["message"] == \
        "Outside opening hours 08:00-20:00"
    assert book_json(a, blocks=9).get_json()["error"]["message"] == "Duration must be 1 to 8 blocks"
    r = a.post("/spaces/1/book", data={"date": "2026-10-07", "start": "09:15", "blocks": "1", "party_size": "1"},
               follow_redirects=True)
    assert "Pick a start on the half hour" in r.get_data(as_text=True)


def test_pur_r10_notice_and_horizon(app):
    a = member(app)
    assert book_json(a, start="2026-10-05T10:30:00+07:00", blocks=1).get_json()["error"]["message"] == \
        "Book at least 60 minutes ahead"
    assert book_json(a, start="2026-11-05T09:00:00+07:00").get_json()["error"]["message"] == \
        "Book at most 30 days ahead"


def test_pur_r11_r12_adjacent_allowed_overlap_taken(app):
    a, b = member(app), member(app, "b@example.com", "Member B")
    assert book_json(a).status_code == 201  # 09:00-10:30
    r = book_json(b, start="2026-10-07T10:00:00+07:00", blocks=2)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "slot_taken"
    assert book_json(b, start="2026-10-07T10:30:00+07:00", blocks=2).status_code == 201


def test_pur_r13_grid_reasons(app):
    a = member(app)
    book_json(a)  # 09:00-10:30 held
    starts = a.get("/api/spaces/1/availability?date=2026-10-07&blocks=3").get_json()["starts"]
    by_time = {s["start"][11:16]: s for s in starts}
    assert len(starts) == 24
    assert by_time["08:00"]["reason"] == "Booked" and by_time["10:30"]["available"]
    assert by_time["19:00"]["reason"] == "Runs past 20:00"
    today = a.get("/api/spaces/1/availability?date=2026-10-05&blocks=1").get_json()["starts"]
    assert today[0]["reason"] == "Too soon"
    html = a.get("/spaces/1?date=2026-10-07&blocks=3").get_data(as_text=True)
    assert 'value="19:00" disabled title="Runs past 20:00"' in html and "10:30-12:00" in html


def test_pur_r15_space_values_checked(app):
    op = operator(app)
    r = op.post("/operator/spaces", data={"name": "X", "capacity": "5000", "hourly_rate": "100"},
                follow_redirects=True)
    assert "Capacity must be a whole number from 1 to 1,000" in r.get_data(as_text=True)
    r = op.post("/operator/spaces", data={"name": "X", "capacity": "4", "hourly_rate": "10"}, follow_redirects=True)
    assert "Rate must be 0 or 20 to 10,000 THB per hour" in r.get_data(as_text=True)


def test_pur_r16_archive_waits_for_upcoming_bookings(app, pay):
    op, a = operator(app), member(app)
    ref = book_json(a).get_json()["reference"]
    r = op.post("/operator/spaces/1/archive", follow_redirects=True)
    assert f"Cancel its upcoming bookings first: {ref} (held)" in r.get_data(as_text=True)
    a.post(f"/api/bookings/{ref}/cancel", json={})
    assert "Space archived" in op.post("/operator/spaces/1/archive", follow_redirects=True).get_data(as_text=True)
    assert [s["space_id"] for s in a.get("/api/spaces").get_json()["spaces"]] == [2, 3, 4]
    assert a.get(f"/api/bookings/{ref}").status_code == 200  # history kept


def test_pur_r17_price_fixed_at_creation(app):
    op, a = operator(app), member(app)
    ref = book_json(a).get_json()["reference"]
    op.post("/operator/spaces/1", data={"name": "Meeting Room A", "capacity": "6", "hourly_rate": "1000"})
    assert a.get(f"/api/bookings/{ref}").get_json()["agreed_price_satang"] == PRICE


def test_pur_r18_money_shown_as_thb(app):
    html = app.test_client().get("/").get_data(as_text=True)
    assert "THB 300.00 per hour, THB 150.00 per 30 min" in html and "THB 1,000.00 per hour" in html


def test_pur_r19_r20_plan_skips_payment(app, pay, axs):
    op, b = operator(app), member(app, "b@example.com", "Member B")
    page = op.get("/operator/members").get_data(as_text=True)
    action = re.search(r'action="([^"]+)" data-member-email="b@example.com"', page).group(1)
    assert "Plan on for b@example.com" in op.post(action, data={"plan_active": "true"},
                                                  follow_redirects=True).get_data(as_text=True)
    r = book_json(b, start="2026-10-07T10:30:00+07:00", blocks=2, party_size=2)
    body = r.get_json()
    assert r.status_code == 201 and (body["status"], body["coverage"], body["agreed_price_satang"]) == \
        ("confirmed", "plan", 30000)
    assert (body["payment_status"], body["grant_status"], body["hold_expires_at"]) == ("not_required", "issued", None)
    assert pay.calls == [] and axs.calls == ["grant"]
    assert "Confirmed. Covered by your plan. No payment was taken." in \
        b.get(f"/bookings/{body['reference']}").get_data(as_text=True)


def test_pur_r20_free_space_confirms_at_once(app, pay):
    a = member(app)
    r = a.post("/spaces/4/book", data={"date": "2026-10-07", "start": "09:00", "blocks": "2", "party_size": "3"})
    ref = r.headers["Location"].rsplit("/", 1)[1]
    body = a.get(f"/api/bookings/{ref}").get_json()
    assert (body["coverage"], body["status"], body["agreed_price_satang"]) == ("free", "confirmed", 0)
    assert pay.calls == []
    r = a.post("/spaces/4/book", data={"date": "2026-10-07", "start": "09:00", "blocks": "2", "party_size": "3"},
               follow_redirects=True)
    assert "You already booked this slot" in r.get_data(as_text=True)


def test_pur_r21_r23_double_submit_resumes_hold_and_session_ends_before_hold(app, pay):
    a = member(app)
    first = book_json(a).get_json()
    assert first["hold_expires_at"] == "2026-10-05T10:15:00+07:00"
    assert pay.sessions[first["payment_session_id"]]["expires_at"] == "2026-10-05T10:13:00+07:00"
    r = a.post("/spaces/1/book", data={"date": "2026-10-07", "start": "09:00", "blocks": "3", "party_size": "1"})
    assert r.status_code == 303 and r.headers["Location"] == f"http://localhost:8002/pay/{first['payment_session_id']}"
    assert len(pay.sessions) == 1 and len(a.get("/api/bookings/mine").get_json()["bookings"]) == 1


def test_pur_r23_payment_unreachable_keeps_hold_without_session(app, pay):
    a = member(app)
    pay.down = True
    r = book_json(a)
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "payment_unreachable"
    pay.down = False
    again = book_json(a)  # the same request resumes it and creates the session
    assert again.status_code == 200 and again.get_json()["payment_url"]


def test_pur_r24_lost_redirect_confirmed_on_next_read(app, pay, axs):
    a = member(app)
    ref = book_json(a).get_json()["reference"]
    pay.pay(ref)
    set_clock(a, "2026-10-05T10:16:00+07:00")  # past the hold: still confirmed, not expired (D14)
    body = a.get(f"/api/bookings/{ref}").get_json()
    assert (body["status"], body["payment_status"], body["grant_status"]) == ("confirmed", "paid", "issued")
    assert body["ticket_url"].startswith("http://localhost:8003/t/")


def test_pur_r24_hold_expiry_frees_the_slot(app, pay):
    a, b = member(app), member(app, "b@example.com", "Member B")
    ref = book_json(a).get_json()["reference"]
    set_clock(a, "2026-10-05T10:16:00+07:00")
    r = book_json(b)  # the pre-insert sweep expires the lapsed hold
    assert r.status_code == 201
    assert a.get(f"/api/bookings/{ref}").get_json()["status"] == "expired"


def test_pur_r25_amount_mismatch_cancels_and_refunds_in_full(app, pay, axs):
    a = member(app)
    ref = book_json(a).get_json()["reference"]
    pay.pay(ref, amount=40000)
    body = a.get(f"/api/bookings/{ref}").get_json()
    assert (body["status"], body["cancel_reason"], body["refund_reason"]) == \
        ("cancelled", "amount_mismatch", "amount_mismatch")
    assert (body["refund_amount_satang"], body["refund_status"]) == (40000, "succeeded")
    assert "grant" not in axs.calls


def test_pur_r26_grant_pending_while_access_down_then_retried(app, pay, axs):
    a = member(app)
    ref = book_json(a).get_json()["reference"]
    pay.pay(ref)
    axs.down = True
    assert a.get(f"/api/bookings/{ref}").get_json()["grant_status"] == "pending"
    assert "Your e-ticket is being prepared" in a.get(f"/bookings/{ref}").get_data(as_text=True)
    axs.down = False
    r = a.post(f"/bookings/{ref}/retry", follow_redirects=True)
    assert "E-ticket issued" in r.get_data(as_text=True)
    assert a.get(f"/api/bookings/{ref}").get_json()["grant_status"] == "issued"


def _cancel(client, ref, prefix=""):
    html = client.get(f"/bookings/{ref}/cancel").get_data(as_text=True)
    shown = int(re.search(r'name="shown_refund_satang" value="(\d+)"', html).group(1))
    return shown, client.post(f"{prefix}/bookings/{ref}/cancel", data={"shown_refund_satang": shown})


def test_pur_r30_r32_member_cancel_24h_ahead_refunds_in_full(app, pay, axs):
    a = member(app)
    ref = book_json(a).get_json()["reference"]
    pay.pay(ref)
    a.get(f"/bookings/{ref}/return?session_id=x")
    shown, r = _cancel(a, ref)
    assert shown == PRICE and r.headers["Location"] == f"/bookings/{ref}"
    body = a.get(f"/api/bookings/{ref}").get_json()
    assert (body["status"], body["cancel_reason"], body["refund_amount_satang"]) == ("cancelled", "member_cancel", PRICE)
    assert (body["refund_status"], body["refund_attempt"], body["grant_status"]) == ("succeeded", 1, "revoked")
    assert axs.calls == ["grant", "revoke"] and pay.calls.count("refund") == 1
    assert "This booking is already cancelled" in a.post(
        f"/bookings/{ref}/cancel", data={"shown_refund_satang": PRICE}, follow_redirects=True).get_data(as_text=True)
    assert pay.calls.count("refund") == 1


def test_pur_r30_member_cancel_under_24h_refunds_nothing(app, pay):
    a = member(app)
    ref = book_json(a, start="2026-10-06T09:00:00+07:00").get_json()["reference"]
    pay.pay(ref)
    shown, _ = _cancel(a, ref)
    body = a.get(f"/api/bookings/{ref}").get_json()
    assert shown == 0 and (body["refund_amount_satang"], body["refund_status"]) == (0, "none")
    assert "refund" not in pay.calls


def test_pur_r30_refund_dropped_since_screen_asks_again(app, pay):
    a = member(app)
    ref = book_json(a, start="2026-10-06T11:00:00+07:00").get_json()["reference"]
    pay.pay(ref)
    html = a.get(f"/bookings/{ref}/cancel").get_data(as_text=True)
    assert 'value="45000"' in html
    set_clock(a, "2026-10-05T11:30:00+07:00")  # the 24 h line passes
    r = a.post(f"/bookings/{ref}/cancel", data={"shown_refund_satang": "45000"})
    assert r.headers["Location"] == f"/bookings/{ref}/cancel"
    assert a.get(f"/api/bookings/{ref}").get_json()["status"] == "confirmed"


def test_pur_r30_operator_cancel_always_refunds_in_full(app, pay):
    op, a = operator(app), member(app)
    ref = book_json(a, start="2026-10-06T09:00:00+07:00").get_json()["reference"]
    pay.pay(ref)
    shown, r = _cancel(op, ref, "/operator")
    assert shown == PRICE and r.headers["Location"] == "/operator/bookings"
    body = a.get(f"/api/bookings/{ref}").get_json()
    assert (body["cancel_reason"], body["refund_amount_satang"], body["refund_status"]) == \
        ("operator_cancel", PRICE, "succeeded")


def test_pur_r31_held_cancel_racing_payment_refunds(app, pay, axs):
    a = member(app)
    ref = book_json(a).get_json()["reference"]
    pay.pay(ref)  # nothing in Purchase reads it before the cancel
    r = a.post(f"/api/bookings/{ref}/cancel", json={})
    body = r.get_json()
    assert r.status_code == 200 and (body["status"], body["refund_amount_satang"], body["refund_status"]) == \
        ("cancelled", PRICE, "succeeded")
    assert (body["grant_status"], body["ticket_url"]) == ("revoked", None)
    assert axs.calls == ["revoke"]


def test_pur_r31_held_cancel_unpaid_and_payment_unreachable(app, pay):
    a = member(app)
    ref = book_json(a).get_json()["reference"]
    pay.down = True
    r = a.post(f"/api/bookings/{ref}/cancel", json={})
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "payment_unreachable"
    pay.down = False
    body = a.post(f"/api/bookings/{ref}/cancel", json={}).get_json()
    assert (body["status"], body["payment_status"], body["refund_status"]) == ("cancelled", "unpaid", "none")


def test_pur_r33_only_operator_starts_the_next_refund_attempt(app, pay):
    op, c = operator(app), member(app, "c@example.com", "Member C")
    pay.first_refund_fails = True
    ref = book_json(c).get_json()["reference"]
    pay.pay(ref)
    _cancel(c, ref)
    assert (c.get(f"/api/bookings/{ref}").get_json()["refund_status"]) == "failed"
    assert "Refund failed. The operator will follow up." in c.get(f"/bookings/{ref}").get_data(as_text=True)
    assert 'data-flags="refund_failed"' in op.get("/operator/bookings").get_data(as_text=True)
    c.post(f"/bookings/{ref}/retry")
    assert c.post(f"/operator/bookings/{ref}/retry").status_code == 404
    assert c.get(f"/api/bookings/{ref}").get_json()["refund_attempt"] == 1
    assert "Refund attempt 2 succeeded" in op.post(f"/operator/bookings/{ref}/retry",
                                                   follow_redirects=True).get_data(as_text=True)
    assert "Nothing to retry" in op.post(f"/operator/bookings/{ref}/retry", follow_redirects=True).get_data(as_text=True)


def test_pur_r34_dashboard_markers(app, pay):
    op, a = operator(app), member(app)
    html = op.get("/dashboard").get_data(as_text=True)
    assert 'data-utilization="0.0000"' in html and 'data-members="0"' in html
    ref = book_json(a, start="2026-10-05T12:00:00+07:00", blocks=3).get_json()["reference"]
    pay.pay(ref)
    a.get(f"/bookings/{ref}")
    html = op.get("/dashboard").get_data(as_text=True)
    assert 'data-status-count-confirmed="1"' in html and 'data-hours-pay="1.5"' in html
    assert 'data-members="1"' in html and 'data-utilization="0.0045"' in html  # 1.5 / (12 * 4 * 7)


def test_pur_r36_url_text_is_never_shown(app):
    html = app.test_client().get("/login?error=<b>pwned</b>&message=hello").get_data(as_text=True)
    assert "pwned" not in html and "hello" not in html


def test_pur_r37_logout_is_a_post(app):
    a = member(app)
    assert a.get("/logout").status_code == 405
    assert a.post("/logout").headers["Location"] == "/"
    assert a.get("/bookings/mine").status_code == 303


def test_pur_r38_test_clock_404_unless_enabled(app, monkeypatch):
    c = app.test_client()
    assert c.post("/_test/clock", json={"now": "2026-10-05T10:00:00"}).status_code == 400
    import clock
    monkeypatch.setattr(clock, "enabled", False)
    other = __import__("app").create_app()
    try:
        monkeypatch.setenv("TEST_CLOCK_ENABLED", "false")
        disabled = __import__("app").create_app()
        assert disabled.test_client().post("/_test/clock", json={"now": None}).status_code == 404
        disabled.db.close()
    finally:
        other.db.close()
        monkeypatch.setenv("TEST_CLOCK_ENABLED", "true")
        clock.configure(app.db, True)


def test_pur_r29_r39_reference_format_and_one_hold_per_member(app):
    a = member(app)
    ref = book_json(a).get_json()["reference"]
    assert re.fullmatch(r"BK-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{6}", ref)
    r = book_json(a, space_id=2, party_size=1)
    assert r.status_code == 409 and r.get_json()["error"]["message"] == f"Finish or cancel your held booking {ref} first"


def test_pur_r40_no_resume_after_the_payment_deadline(app, pay):
    a = member(app)
    book_json(a)
    set_clock(a, "2026-10-05T10:13:00+07:00")
    r = book_json(a)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "payment_time_over"
    assert pay.calls == ["create"]


def test_health_reports_revision(app):
    assert app.test_client().get("/health").get_json() == {"status": "ok", "revision": "local"}

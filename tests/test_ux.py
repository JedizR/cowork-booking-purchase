"""The server side of the redesigned pages: the timeline's per-slot state (PUR-R13 at one block), the horizon
calendar (PUR-R10), flash looks (PUR-R36) and the list filters."""
import re
from datetime import date, datetime

from app import flash_kind
from conftest import book_json, member, set_clock
from purchase import BKK, month


def slot(html, hhmm):
    """(classes, disabled) of the timeline button for one 30-min block."""
    m = re.search(r'<button type="button" class="(slot[^"]*)" data-i="\d+" data-time="' + hhmm + r'"([^>]*)>', html)
    return m.group(1), "disabled" in m.group(2)


def test_pur_r13_timeline_marks_each_block_free_or_with_its_reason(app):
    a = member(app)
    book_json(a)  # 09:00-10:30 held, slot-blocking
    html = a.get("/spaces/1?date=2026-10-07").get_data(as_text=True)
    assert html.count('<button type="button" class="slot') == 24 and "timeline-end" in html
    assert slot(html, "08:30") == ("slot", False)  # ends where the booking starts (PUR-R11)
    for t in ("09:00", "09:30", "10:00"):
        assert slot(html, t) == ("slot is-booked", True)
    assert slot(html, "10:30") == ("slot", False) and slot(html, "19:30") == ("slot", False)  # 1 block ends 20:00
    assert 'aria-label="09:00 to 09:30, booked"' in html and "Runs past 20:00</span>" not in html
    today = a.get("/spaces/1?date=2026-10-05").get_data(as_text=True)  # clock 10:00: before 11:00 is too soon
    assert slot(today, "10:30") == ("slot is-unavailable", True) and slot(today, "11:00") == ("slot", False)


def test_pur_r13_day_without_a_free_block_offers_the_next_day(app):
    a = member(app)
    set_clock(a, "2026-10-05T19:00:00+07:00")
    html = a.get("/spaces/1?date=2026-10-05").get_data(as_text=True)
    assert "No free time on this date" in html and 'class="slot' not in html
    assert 'href="/spaces/1?date=2026-10-06"' in html


def test_pur_r10_calendar_offers_only_the_horizon():
    now = datetime(2026, 10, 5, 10, 0, tzinfo=BKK)
    oct_ = month(date(2026, 10, 7), now)
    days = [c for c in oct_["days"] if c]
    assert oct_["days"][:3] == [None] * 3 and len(oct_["days"]) % 7 == 0  # 1 Oct 2026 is a Thursday
    assert [c["bookable"] for c in days[3:5]] == [False, True] and all(c["bookable"] for c in days[4:])
    assert (oct_["prev"], oct_["next"]) == (None, date(2026, 11, 1))
    nov = month(date(2026, 11, 1), now)
    assert [c["date"].day for c in nov["days"] if c and c["bookable"]] == [1, 2, 3, 4]
    assert (nov["prev"], nov["next"]) == (date(2026, 10, 5), None)
    mar = month(date(2027, 3, 1), datetime(2027, 1, 31, 9, 0, tzinfo=BKK))  # a horizon over three months
    assert mar["prev"] == date(2027, 2, 1) and [c["date"].day for c in mar["days"] if c and c["bookable"]] == [1, 2]


def test_pur_r10_calendar_links_keep_the_blocks(app):
    html = app.test_client().get("/spaces/1?date=2026-10-07&blocks=3").get_data(as_text=True)
    assert 'href="/spaces/1?date=2026-10-08&amp;blocks=3"' in html and 'data-default-blocks="3"' in html
    assert 'class="cal-day is-disabled" aria-disabled="true">4<' in html  # yesterday


def test_pur_r36_flash_look_follows_the_outcome():
    assert [flash_kind(m) for m in ("Booking cancelled", "Refund attempt 2 succeeded", "Plan on for b@example.com",
                                    "Nothing to retry", "1 confirmed, 0 expired, 0 cancelled, 0 unchanged",
                                    "Slot just taken", "Refund attempt 1 failed")] == \
        ["success", "success", "success", "info", "info", "error", "error"]


def test_my_bookings_splits_upcoming_and_past(app):
    a = member(app)
    ref = book_json(a, start="2026-10-05T12:00:00+07:00", blocks=1).get_json()["reference"]
    assert ref in a.get("/bookings/mine").get_data(as_text=True)
    set_clock(a, "2026-10-05T13:00:00+07:00")
    assert ref not in a.get("/bookings/mine").get_data(as_text=True)
    assert ref in a.get("/bookings/mine?view=past").get_data(as_text=True)


def test_operator_bookings_filter_and_search(app):
    op, a, b = member(app, "operator@example.com", "Operator"), member(app), member(app, "b@example.com", "B")
    ref = book_json(a).get_json()["reference"]
    other = book_json(b, space_id=4, party_size=1).get_json()["reference"]  # free: confirmed at once
    assert ref in op.get("/operator/bookings?status=held").get_data(as_text=True)
    html = op.get("/operator/bookings?status=confirmed").get_data(as_text=True)
    assert other in html and f'data-booking-reference="{ref}"' not in html
    html = op.get(f"/operator/bookings?q={ref.lower()}").get_data(as_text=True)
    assert f'data-booking-reference="{ref}"' in html and f'data-booking-reference="{other}"' not in html
    assert "No bookings match" in op.get("/operator/bookings?q=BK-NONE").get_data(as_text=True)
    edit = op.get("/operator/spaces?edit=1").get_data(as_text=True)
    assert 'action="/operator/spaces/1"' in edit and 'value="Meeting Room A"' in edit


def order(html):
    return re.findall(r'data-booking-reference="(BK-[A-Z0-9]{6})"', html)


def test_operator_list_puts_flagged_rows_first_and_not_settled_refunds(app, pay, axs):
    """A refunded cancel no longer needs a person; a ticket still being prepared does (PUR-R32, DESIGN Tables)."""
    op, a, b, c = (member(app, "operator@example.com", "Op"), member(app), member(app, "b@example.com", "B"),
                   member(app, "c@example.com", "C"))
    refunded = book_json(a).get_json()["reference"]
    pay.pay(refunded)
    a.get(f"/api/bookings/{refunded}")
    assert a.post(f"/api/bookings/{refunded}/cancel", json={}).get_json()["refund_status"] == "succeeded"
    plain = book_json(b, space_id=4, start="2026-10-07T12:00:00+07:00", party_size=1).get_json()["reference"]
    axs.down = True
    preparing = book_json(c, space_id=4, start="2026-10-08T09:00:00+07:00", party_size=1).get_json()["reference"]
    rows = order(op.get("/operator/bookings?when=any").get_data(as_text=True))  # unflagged: newest start first
    assert rows == [preparing, plain, refunded]
    assert order(op.get("/operator/bookings?q=c@EXAMPLE.com").get_data(as_text=True)) == [preparing]  # by email


def test_pur_r41_booking_page_shows_the_live_ticket_code(app, axs):
    a = member(app)
    ref = book_json(a, space_id=4, party_size=1).get_json()["reference"]
    assert "M4TR-8WCE" in a.get(f"/bookings/{ref}").get_data(as_text=True)
    axs.down = True
    html = a.get(f"/bookings/{ref}").get_data(as_text=True)
    assert "Ticket code unavailable right now" in html and "View e-ticket" in html and "M4TR-8WCE" not in html
    axs.down = False
    a.post(f"/api/bookings/{ref}/cancel", json={})
    assert "M4TR-8WCE" not in a.get(f"/bookings/{ref}").get_data(as_text=True)


def test_archive_asks_first_and_lists_what_blocks_it(app):
    op, a = member(app, "operator@example.com", "Op"), member(app)
    ref = book_json(a).get_json()["reference"]
    html = op.get("/operator/spaces/1/archive").get_data(as_text=True)
    assert ref in html and 'action="/operator/spaces/1/archive"' not in html  # blocked: no button (PUR-R16)
    html = op.get("/operator/spaces/2/archive").get_data(as_text=True)
    assert 'action="/operator/spaces/2/archive"' in html and "Archive room" in html
    assert len(op.get("/api/spaces").get_json()["spaces"]) == 4  # the GET changed nothing (PUR-R37)
    assert a.get("/operator/spaces/2/archive").status_code == 404


def test_forms_keep_the_typed_email_never_the_password(app):
    c = app.test_client()
    c.post("/register", data={"email": "new@example.com", "display_name": "New", "password": "short"})
    html = c.get("/register").get_data(as_text=True)
    assert 'value="new@example.com"' in html and 'value="New"' in html and "short" not in html
    c.post("/register", data={"email": "new@example.com", "display_name": "New", "password": "long-enough"})
    assert 'value="new@example.com"' in c.get("/login").get_data(as_text=True)
    c.post("/login", data={"email": "new@example.com", "password": "wrong-password"})
    html = c.get("/login").get_data(as_text=True)
    assert "Invalid email or password" in html and 'value="new@example.com"' in html
    assert 'value=""' in c.get("/login").get_data(as_text=True)  # shown once, like the flash


def test_my_bookings_upcoming_leaves_out_cancelled(app):
    a = member(app)
    ref = book_json(a, space_id=4, party_size=1).get_json()["reference"]
    a.post(f"/api/bookings/{ref}/cancel", json={})
    assert ref not in a.get("/bookings/mine").get_data(as_text=True)
    assert ref in a.get("/bookings/mine?view=past").get_data(as_text=True)


def test_add_to_calendar_reads_the_stored_booking(app):
    a = member(app)
    ref = book_json(a, space_id=4, party_size=1).get_json()["reference"]
    r = a.get(f"/bookings/{ref}/calendar.ics")
    body = r.get_data(as_text=True)
    assert r.mimetype == "text/calendar" and "DTSTART:20261007T020000Z\r\n" in body and "DTEND:20261007T033000Z" in body
    assert f"UID:{ref}@cowork-booking" in body and "SUMMARY:Community Table" in body


def words(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_pur_r23_payment_description_is_the_room_and_the_human_bangkok_time(app, pay):
    a = member(app)
    sid = book_json(a).get_json()["payment_session_id"]  # Meeting Room A, Wed 7 Oct 09:00, 3 blocks
    assert pay.sessions[sid]["description"] == "Meeting Room A · Wed 7 Oct · 09:00–10:30"  # no name, no email


def test_times_read_one_way_on_the_cancel_screen_and_the_booking_page(app, pay):
    op, a = member(app, "operator@example.com", "Op"), member(app)
    ref = book_json(a).get_json()["reference"]
    screen = words(a.get(f"/bookings/{ref}/cancel").get_data(as_text=True))
    assert f"Cancel {ref}, Meeting Room A, Wed 7 Oct · 09:00–10:30" in screen
    pay.pay(ref)
    assert "Cancel by Tue 6 Oct, 09:00 for a full refund." in words(a.get(f"/bookings/{ref}").get_data(as_text=True))
    set_clock(a, "2026-10-05T11:30:00+07:00")
    a.post(f"/api/bookings/{ref}/cancel", json={})
    assert "Mon 5 Oct, 11:30" in words(op.get("/operator/bookings?when=any").get_data(as_text=True))


def test_cancelled_page_says_cancelled_once_and_plan_is_never_a_total(app, pay):
    a, b = member(app), member(app, "b@example.com", "B")
    ref = book_json(a, space_id=4, party_size=1).get_json()["reference"]  # free: confirmed at once
    page = words(a.post(f"/bookings/{ref}/cancel", data={"shown_refund_satang": "0"}, follow_redirects=True)
                 .get_data(as_text=True))
    assert page.count("Cancelled") == 1 and "Booking cancelled" in page  # the card line, plus the flash
    app.db.execute("UPDATE members SET plan_active = true WHERE email = 'b@example.com'")
    ref = book_json(b, party_size=2).get_json()["reference"]
    page = words(b.get(f"/bookings/{ref}").get_data(as_text=True))
    assert "Covered by your plan · no payment" in page and "Total" not in page and "You pay" not in page


def test_pur_r34_dashboard_leads_with_today_and_coming_up(app):
    op, a = member(app, "operator@example.com", "Op"), member(app)
    html = words(op.get("/dashboard").get_data(as_text=True))
    assert "No bookings today." in html and "Nothing booked after today yet." in html
    assert "No booking started in these 7 days" in html  # a line in the tile, not a bare 0
    now = book_json(a, space_id=4, start="2026-10-05T12:00:00+07:00", blocks=2, party_size=1).get_json()["reference"]
    later = book_json(a, space_id=4, start="2026-10-07T09:00:00+07:00", party_size=1).get_json()["reference"]
    html = op.get("/dashboard").get_data(as_text=True)
    today_card, coming_card = html.split(">Coming up<")
    assert now in today_card and later not in today_card and later in coming_card

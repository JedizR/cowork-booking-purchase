"""Builds the app on a clean schema per test, with Payment and Access stubbed at their client modules.
The stub answers follow cowork-booking-payment/CONTRACT.md and cowork-booking-access/CONTRACT.md."""
import os

import psycopg
import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:55461/postgres")
os.environ.update(SECRET_KEY="test-secret-key-0123456789abcdef", TEST_CLOCK_ENABLED="true",
                  OPERATOR_EMAIL="operator@example.com", PUBLIC_URL="http://localhost:8001",
                  PAYMENT_PUBLIC_URL="http://localhost:8002", ACCESS_PUBLIC_URL="http://localhost:8003")

import app as app_module  # noqa: E402
from payment_client import CallFailed  # noqa: E402

NOW = "2026-10-05T10:00:00+07:00"
PASSWORD = "correct-horse"


class FakePayment:
    def __init__(self):
        self.sessions, self.refunds, self.calls = {}, {}, []
        self.down, self.first_refund_fails = False, False

    def _call(self, name):
        self.calls.append(name)
        if self.down:
            raise CallFailed("Payment")

    def create_session(self, booking_reference, amount_satang, description, success_url, cancel_url, expires_at):
        self._call("create")
        for s in self.sessions.values():
            if s["booking_reference"] == booking_reference:
                return dict(s)
        sid = f"ps_{len(self.sessions) + 1:022d}"
        self.sessions[sid] = {"id": sid, "url": f"http://localhost:8002/pay/{sid}", "status": "open",
                              "payment_status": "unpaid", "amount_satang": amount_satang, "currency": "THB",
                              "booking_reference": booking_reference, "expires_at": expires_at.isoformat()}
        return dict(self.sessions[sid])

    def get_session(self, sid):
        self._call("get")
        return dict(self.sessions[sid])

    def expire_session(self, sid):
        self._call("expire")
        if self.sessions[sid]["status"] == "open":
            self.sessions[sid]["status"] = "expired"
        return dict(self.sessions[sid])

    def create_refund(self, payment_session_id, booking_reference, amount_satang, reason, attempt):
        self._call("refund")
        key = (payment_session_id, attempt)
        if key not in self.refunds:
            failed = self.first_refund_fails and not any(k[0] == payment_session_id for k in self.refunds)
            self.refunds[key] = {"id": f"re_{len(self.refunds)}", "payment_session_id": payment_session_id,
                                 "booking_reference": booking_reference, "amount_satang": amount_satang,
                                 "reason": reason, "attempt": attempt,
                                 "status": "failed" if failed else "succeeded"}
        return dict(self.refunds[key])

    def pay(self, ref, amount=None):
        s = next(s for s in self.sessions.values() if s["booking_reference"] == ref)
        s.update(status="complete", payment_status="paid")
        if amount is not None:
            s["amount_satang"] = amount  # D14: a mismatch the real Payment cannot produce


class FakeAccess:
    def __init__(self):
        self.grants, self.calls, self.down = {}, [], False

    def _call(self, name):
        self.calls.append(name)
        if self.down:
            raise CallFailed("Access")

    def create_grant(self, booking_reference, member_ref, space_id, space_name, valid_from, valid_until):
        self._call("grant")
        g = self.grants.setdefault(booking_reference, {
            "grant_id": f"gr_{len(self.grants):022d}", "booking_reference": booking_reference, "status": "issued",
            "ticket_code": "M4TR-8WCE", "ticket_url": f"http://localhost:8003/t/tok{len(self.grants):019d}",
            "valid_from": valid_from.isoformat(), "valid_until": valid_until.isoformat()})
        return dict(g)

    def revoke_grant(self, booking_reference):
        self._call("revoke")
        g = self.grants.setdefault(booking_reference, {"booking_reference": booking_reference,
                                                       "ticket_code": None, "ticket_url": None})
        g["status"] = "revoked"
        return dict(g)


@pytest.fixture
def stubs(monkeypatch):
    pay, axs = FakePayment(), FakeAccess()
    for name in ("create_session", "get_session", "expire_session", "create_refund"):
        monkeypatch.setattr(app_module.payment_client, name, getattr(pay, name))
    for name in ("create_grant", "revoke_grant"):
        monkeypatch.setattr(app_module.access_client, name, getattr(axs, name))
    return pay, axs


@pytest.fixture
def app(stubs):
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    application = app_module.create_app(url)
    application.config["TESTING"] = True
    application.test_client().post("/_test/clock", json={"now": NOW})
    yield application
    application.db.close()


@pytest.fixture
def pay(stubs):
    return stubs[0]


@pytest.fixture
def axs(stubs):
    return stubs[1]


def member(app, email="a@example.com", name="Member A"):
    c = app.test_client()
    r = c.post("/register", data={"email": email, "display_name": name, "password": PASSWORD})
    assert r.status_code == 303 and r.headers["Location"] == "/login"
    r = c.post("/login", data={"email": email, "password": PASSWORD})
    assert r.status_code == 303 and r.headers["Location"] == "/"
    return c


def set_clock(client, iso):
    assert client.post("/_test/clock", json={"now": iso}).status_code == 200


def book_json(client, start="2026-10-07T09:00:00+07:00", blocks=3, space_id=1, party_size=4, **extra):
    return client.post("/api/bookings", json={"space_id": space_id, "start": start, "blocks": blocks,
                                              "party_size": party_size, **extra})


def flashes(client, path):
    return client.get(path).get_data(as_text=True)

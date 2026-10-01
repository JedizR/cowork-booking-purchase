"""Every call Purchase makes to Payment (PUR-R35). Bearer token, timeout=5, internal URL."""
import logging
import os

import requests

log = logging.getLogger(__name__)


class CallFailed(Exception):
    """status None: no answer or 5xx (unreachable). Another 4xx: a config or Purchase defect."""

    def __init__(self, service: str, status: int | None = None):
        self.service, self.status = service, status
        super().__init__(self.message)

    @property
    def message(self) -> str:
        if self.status is None:
            return f"{self.service} is not reachable. Please try again."
        return (f"{self.service} refused the call ({self.status}): "
                "check the service settings and the Purchase log.")


def _call(method: str, path: str, body: dict | None = None, ref: str = "") -> dict:
    base = os.getenv("PAYMENT_INTERNAL_URL", "http://localhost:8002").rstrip("/")
    headers = {"Authorization": f"Bearer {os.getenv('PAYMENT_API_TOKEN', '')}"}
    try:
        r = requests.request(method, base + path, json=body, headers=headers, timeout=5)
    except requests.RequestException:
        raise CallFailed("Payment") from None
    if r.status_code >= 500:
        raise CallFailed("Payment")
    if r.status_code >= 400:
        log.warning("payment %s %s for %s answered %s", method, path.split("/")[1], ref, r.status_code)
        raise CallFailed("Payment", r.status_code)
    return r.json()


def create_session(booking_reference, amount_satang, description, success_url, cancel_url, expires_at) -> dict:
    return _call("POST", "/payment-sessions", {
        "booking_reference": booking_reference, "amount_satang": amount_satang, "currency": "THB",
        "description": description, "success_url": success_url, "cancel_url": cancel_url,
        "expires_at": expires_at.isoformat()}, booking_reference)


def get_session(session_id: str) -> dict:
    return _call("GET", f"/payment-sessions/{session_id}")


def expire_session(session_id: str) -> dict:
    return _call("POST", f"/payment-sessions/{session_id}/expire")


def create_refund(payment_session_id, booking_reference, amount_satang, reason, attempt) -> dict:
    return _call("POST", "/refunds", {
        "payment_session_id": payment_session_id, "booking_reference": booking_reference,
        "amount_satang": amount_satang, "reason": reason, "attempt": attempt}, booking_reference)

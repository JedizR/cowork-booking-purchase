"""Every call Purchase makes to Access (PUR-R35). Bearer token, timeout=5, internal URL."""
import logging
import os

import requests

from payment_client import CallFailed

log = logging.getLogger(__name__)


def _call(method: str, path: str, body: dict | None = None, ref: str = "") -> dict:
    base = os.getenv("ACCESS_INTERNAL_URL", "http://localhost:8003").rstrip("/")
    headers = {"Authorization": f"Bearer {os.getenv('ACCESS_API_TOKEN', '')}"}
    try:
        r = requests.request(method, base + path, json=body, headers=headers, timeout=5)
    except requests.RequestException:
        raise CallFailed("Access") from None
    if r.status_code >= 500:
        raise CallFailed("Access")
    if r.status_code >= 400:
        log.warning("access %s %s for %s answered %s", method, path.split("/")[1], ref, r.status_code)
        raise CallFailed("Access", r.status_code)
    return r.json()


def create_grant(booking_reference, member_ref, space_id, space_name, valid_from, valid_until) -> dict:
    return _call("POST", "/grants", {
        "booking_reference": booking_reference, "member_ref": member_ref, "space_id": space_id,
        "space_name": space_name, "valid_from": valid_from.isoformat(),
        "valid_until": valid_until.isoformat()}, booking_reference)


def revoke_grant(booking_reference: str) -> dict:
    return _call("POST", f"/grants/{booking_reference}/revoke", ref=booking_reference)

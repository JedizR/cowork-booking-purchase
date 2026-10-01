"""Purchase rules that need no database: price, blocks, grid, refund policy, references."""
import secrets
from datetime import date, datetime, time, timedelta, timezone

BKK = timezone(timedelta(hours=7))  # fixed UTC+7, no zoneinfo (D2)
BLOCK = timedelta(minutes=30)
OPEN, CLOSE = time(8, 0), time(20, 0)  # D3
NOTICE = timedelta(minutes=60)  # D5
HORIZON_DAYS = 30
HOLD = timedelta(minutes=15)  # D11
PAY_MARGIN = timedelta(minutes=2)  # D12
ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"  # D22


def price_satang(hourly_rate_satang: int, blocks: int) -> int:
    """D8: round_half_up(rate x blocks / 2), fixed at creation (PUR-R17)."""
    return (hourly_rate_satang * blocks + 1) // 2


def new_reference() -> str:
    return "BK-" + "".join(secrets.choice(ALPHABET) for _ in range(6))


def today(now: datetime) -> date:
    return now.astimezone(BKK).date()


def start_of(day: date, hhmm: time) -> datetime:
    return datetime.combine(day, hhmm, BKK)


def shape_error(start: datetime, blocks) -> str | None:
    """PUR-R08, PUR-R09: duration, half-hour start, opening hours."""
    if not isinstance(blocks, int) or isinstance(blocks, bool) or not 1 <= blocks <= 8:
        return "Duration must be 1 to 8 blocks"
    local = start.astimezone(BKK)
    if local.minute not in (0, 30) or local.second or local.microsecond:
        return "Start on :00 or :30"
    end = local + BLOCK * blocks
    if local.time() < OPEN or end > start_of(local.date(), CLOSE):
        return "Outside opening hours 08:00-20:00"
    return None


def window_error(start: datetime, now: datetime) -> str | None:
    """PUR-R10: notice and horizon."""
    if start < now + NOTICE:
        return "Book at least 60 minutes ahead"
    if start.astimezone(BKK).date() > today(now) + timedelta(days=HORIZON_DAYS):
        return "Book at most 30 days ahead"
    return None


def date_in_horizon(day: date, now: datetime) -> bool:
    return today(now) <= day <= today(now) + timedelta(days=HORIZON_DAYS)


def grid(day: date, blocks: int, now: datetime, busy: list[tuple[datetime, datetime]]) -> list[dict]:
    """PUR-R13: 24 starts 08:00-19:30; first reason wins: Too soon, Runs past 20:00, Booked."""
    rows, close = [], start_of(day, CLOSE)
    for i in range(24):
        start = start_of(day, OPEN) + BLOCK * i
        end = start + BLOCK * blocks
        reason = None
        if start < now + NOTICE:
            reason = "Too soon"
        elif end > close:
            reason = "Runs past 20:00"
        elif any(b_start < end and start < b_end for b_start, b_end in busy):
            reason = "Booked"
        rows.append({"start": start, "end": end, "available": reason is None, "reason": reason})
    return rows


def refund_policy(coverage: str, price: int, start: datetime, now: datetime, by_operator: bool) -> int:
    """PUR-R30 / D18: plan and free refund 0; operator 100%; Member 100% at 24 h or more, else 0."""
    if coverage != "pay":
        return 0
    if by_operator or start - now >= timedelta(hours=24):
        return price
    return 0


def money(satang: int | None) -> str:
    """PUR-R18: "THB 1,234.50"."""
    satang = satang or 0
    return f"THB {satang // 100:,}.{satang % 100:02d}"


def hours_text(hours: float) -> str:
    return f"{hours:.1f}".rstrip("0").rstrip(".")


def duration_text(blocks: int) -> str:
    h, m = divmod(blocks * 30, 60)
    return " ".join(p for p in (f"{h} h" if h else "", f"{m} min" if m else "") if p)


if __name__ == "__main__":
    assert price_satang(30000, 3) == 45000 and price_satang(2000, 1) == 1000
    assert money(123450) == "THB 1,234.50" and hours_text(1.5) == "1.5" and hours_text(2) == "2"
    print("ok")

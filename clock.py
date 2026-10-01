"""The only source of time (D27). With TEST_CLOCK_ENABLED=true, POST /_test/clock can pin it."""
from datetime import datetime, timezone

enabled = False
db = None  # the worker's connection, set by create_app


def configure(conn, is_enabled: bool) -> None:
    global db, enabled
    db, enabled = conn, is_enabled


def now() -> datetime:
    if enabled and db is not None:
        row = db.execute("SELECT now_override FROM test_clock WHERE id = 1").fetchone()
        if row and row["now_override"] is not None:
            return row["now_override"]
    return datetime.now(timezone.utc)

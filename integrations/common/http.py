import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def retry_after_seconds(value: str | None) -> int:
    """Accept seconds or HTTP dates within the worker retry budget."""
    try:
        seconds = int(value or "30")
    except ValueError:
        try:
            seconds = math.ceil((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except ValueError, TypeError, OverflowError:
            seconds = 30
    return min(86400, max(1, seconds))

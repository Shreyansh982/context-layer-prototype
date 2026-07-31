"""Small, dependency-free retry helper for transient infrastructure calls."""

import time
import re
from collections.abc import Callable
from typing import TypeVar

from app.config import settings
from app.errors import DependencyUnavailable
from app.performance import timed

T = TypeVar("T")


def retry_external(operation: str, callback: Callable[[], T]) -> T:
    """Retry a bounded number of times and expose one safe application error."""
    last_error: Exception | None = None
    operation_key = re.sub(r"[^a-z0-9]+", "_", operation.lower()).strip("_")
    for attempt in range(settings.EXTERNAL_RETRY_ATTEMPTS + 1):
        try:
            with timed(f"retry.{operation_key}.attempt"):
                return callback()
        except Exception as error:  # SDKs expose different transient error hierarchies.
            last_error = error
            if attempt == settings.EXTERNAL_RETRY_ATTEMPTS:
                break
            with timed(f"retry.{operation_key}.backoff"):
                time.sleep(settings.EXTERNAL_RETRY_BASE_SECONDS * (2 ** attempt))
    raise DependencyUnavailable(f"{operation} is temporarily unavailable") from last_error

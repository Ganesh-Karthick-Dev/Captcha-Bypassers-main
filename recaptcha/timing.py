import logging
import time
from contextlib import contextmanager

logger = logging.getLogger("recaptcha.timing")


@contextmanager
def timed(label: str, **extra):
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        details = " ".join(
            f"{key}={value}"
            for key, value in extra.items()
            if value is not None
        )
        logger.info(
            "[TIMING] %-35s %.3fs%s",
            label,
            elapsed,
            f" | {details}" if details else "",
        )

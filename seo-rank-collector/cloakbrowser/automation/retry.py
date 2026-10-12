"""Error classification and bounded retry policies for autonomous automation."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, Tuple

logger = logging.getLogger("cloakbrowser.automation.retry")


class ErrorCategory(str, Enum):
    """Classification of errors encountered during browser automation."""
    TRANSIENT = "transient"
    RECOVERABLE = "recoverable"
    NON_RECOVERABLE = "non_recoverable"


@dataclass
class RetryAttempt:
    attempt: int
    reason: str
    elapsed_time: float
    recovery_action: str
    error: Optional[str] = None


@dataclass
class RetryPolicy:
    """Configurable, bounded retry policy with exponential backoff."""

    max_attempts: int = 3
    initial_delay: float = 1.0
    backoff_factor: float = 2.0
    max_delay: float = 10.0
    exponential_backoff: bool = True
    history: List[RetryAttempt] = field(default_factory=list)

    def get_delay(self, attempt: int) -> float:
        if not self.exponential_backoff:
            return min(self.initial_delay, self.max_delay)
        delay = self.initial_delay * (self.backoff_factor ** (attempt - 1))
        return min(delay, self.max_delay)

    def record(self, attempt: int, reason: str, elapsed_time: float, recovery_action: str, error: Optional[str] = None) -> None:
        rec = RetryAttempt(
            attempt=attempt,
            reason=reason,
            elapsed_time=elapsed_time,
            recovery_action=recovery_action,
            error=error,
        )
        self.history.append(rec)
        logger.warning(
            "[retry] Attempt %d/%d: reason=%r, recovery_action=%s (elapsed=%.2fs)",
            attempt,
            self.max_attempts,
            reason,
            recovery_action,
            elapsed_time,
        )


def classify_error(exc: BaseException) -> ErrorCategory:
    """Classify an exception into TRANSIENT, RECOVERABLE, or NON_RECOVERABLE."""
    msg = str(exc).lower()

    # Non-recoverable errors
    if any(k in msg for k in [
        "unsupportedhumanizeselector",
        "invalid credentials",
        "authentication failure",
        "unauthorized",
        "invalid configuration",
        "challengeunresolved",
    ]):
        return ErrorCategory.NON_RECOVERABLE

    # Transient network/frame errors
    if any(k in msg for k in [
        "timeout",
        "navigation timeout",
        "target closed",
        "target crashed",
        "detached frame",
        "frame was detached",
        "execution context was destroyed",
        "net::err",
        "connection reset",
    ]):
        return ErrorCategory.TRANSIENT

    # Recoverable DOM / actionability errors
    if any(k in msg for k in [
        "elementnotattached",
        "elementnotvisible",
        "elementnotstable",
        "elementnotreceivingevents",
        "elementtargetchanged",
        "covered by",
        "pointer_events",
    ]):
        return ErrorCategory.RECOVERABLE

    return ErrorCategory.RECOVERABLE

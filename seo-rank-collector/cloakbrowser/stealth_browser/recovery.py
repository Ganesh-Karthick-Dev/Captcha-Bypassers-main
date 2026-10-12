"""Resilient workflow recovery engine with circuit breaker and failure categorization."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("cloakbrowser.stealth_browser.recovery")


class FailureType(str, Enum):
    BROWSER_CRASH = "BROWSER_CRASH"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    FATAL_ERROR = "FATAL_ERROR"


class RecoveryAction(str, Enum):
    RESTART_BROWSER = "RESTART_BROWSER"
    RE_AUTHENTICATE = "RE_AUTHENTICATE"
    BACKOFF = "BACKOFF"
    CHECKPOINT = "CHECKPOINT"
    ABORT = "ABORT"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    reset_timeout_seconds: float = 60.0
    failure_count: int = 0
    last_failure_time: float = 0.0
    is_open: bool = False

    def record_failure(self) -> bool:
        """Record a failure and check if circuit should trip open."""
        now = time.time()
        if now - self.last_failure_time > self.reset_timeout_seconds:
            self.failure_count = 0
            self.is_open = False

        self.failure_count += 1
        self.last_failure_time = now

        if self.failure_count >= self.failure_threshold:
            self.is_open = True
            logger.error("[circuit-breaker] Tripped open! %d failures reached.", self.failure_count)
            return True
        return False

    def record_success(self) -> None:
        self.failure_count = 0
        self.is_open = False


class RecoveryEngine:
    """Classifies workflow errors and executes bounded recovery actions."""

    def __init__(self, max_retries: int = 3, failure_threshold: int = 5):
        self.max_retries = max_retries
        self.retry_counts: Dict[str, int] = {}
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.failure_threshold = failure_threshold

    def get_circuit_breaker(self, domain: str) -> CircuitBreaker:
        if domain not in self.circuit_breakers:
            self.circuit_breakers[domain] = CircuitBreaker(failure_threshold=self.failure_threshold)
        return self.circuit_breakers[domain]

    def classify_error(self, error: Exception) -> FailureType:
        msg = str(error).lower()
        if "target closed" in msg or "crashed" in msg or "browser has been closed" in msg:
            return FailureType.BROWSER_CRASH
        elif "session" in msg or "expired" in msg or "401" in msg:
            return FailureType.SESSION_EXPIRED
        elif "429" in msg or "rate limit" in msg or "too many requests" in msg:
            return FailureType.RATE_LIMITED
        elif "timeout" in msg or "timed out" in msg:
            return FailureType.TIMEOUT
        return FailureType.FATAL_ERROR

    def determine_action(self, domain: str, failure: FailureType) -> RecoveryAction:
        cb = self.get_circuit_breaker(domain)
        if cb.is_open:
            logger.error("[recovery] Circuit breaker is open for %s; aborting", domain)
            return RecoveryAction.ABORT

        current_retries = self.retry_counts.get(domain, 0)
        if current_retries >= self.max_retries:
            logger.warning("[recovery] Max retries (%d) exceeded for %s", self.max_retries, domain)
            cb.record_failure()
            return RecoveryAction.ABORT

        self.retry_counts[domain] = current_retries + 1

        if failure == FailureType.BROWSER_CRASH:
            return RecoveryAction.RESTART_BROWSER
        elif failure == FailureType.SESSION_EXPIRED:
            return RecoveryAction.RE_AUTHENTICATE
        elif failure == FailureType.RATE_LIMITED:
            return RecoveryAction.BACKOFF
        elif failure == FailureType.TIMEOUT:
            return RecoveryAction.CHECKPOINT

        return RecoveryAction.ABORT

    def reset_domain(self, domain: str) -> None:
        self.retry_counts[domain] = 0
        if domain in self.circuit_breakers:
            self.circuit_breakers[domain].record_success()

"""Proxy health monitoring and circuit breaker."""

from __future__ import annotations

import logging
import time
from typing import Optional

from .identity import ProxyIdentity

logger = logging.getLogger("cloakbrowser.proxy.health")


class ProxyHealthMonitor:
    """Monitors latency, failure rates, and manages circuit breaker parking."""

    def __init__(
        self,
        max_consecutive_failures: int = 3,
        park_duration_seconds: float = 60.0,
    ):
        self.max_consecutive_failures = max_consecutive_failures
        self.park_duration_seconds = park_duration_seconds

    def record_success(self, identity: ProxyIdentity, latency_seconds: float = 0.0) -> None:
        identity.last_used = time.monotonic()
        identity.consecutive_failures = 0
        identity.is_parked = False
        # Exponential moving average for health score
        identity.health = min(1.0, identity.health * 0.9 + 0.1)

    def record_failure(self, identity: ProxyIdentity, error_reason: str) -> None:
        identity.last_used = time.monotonic()
        identity.failures += 1
        identity.consecutive_failures += 1
        identity.health = max(0.05, identity.health * 0.7)

        logger.warning(
            "[proxy-health] Proxy %s recorded failure #%d: %s",
            identity.proxy,
            identity.consecutive_failures,
            error_reason,
        )

        if identity.consecutive_failures >= self.max_consecutive_failures:
            identity.is_parked = True
            identity.parked_until = time.monotonic() + self.park_duration_seconds
            logger.error(
                "[proxy-health] Circuit breaker tripped for %s; parked for %.1fs",
                identity.proxy,
                self.park_duration_seconds,
            )

"""ProxyIdentity dataclass separating proxy endpoints from geographic metadata."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProxyIdentity:
    """Represents a proxy network endpoint and its associated geographic identity."""

    proxy: str
    region: Optional[str] = None
    timezone: Optional[str] = None
    locale: Optional[str] = None
    health: float = 1.0
    failures: int = 0
    consecutive_failures: int = 0
    last_used: float = field(default_factory=time.monotonic)
    is_parked: bool = False
    parked_until: float = 0.0

    @property
    def is_available(self) -> bool:
        """Check if proxy is healthy and not currently in a circuit-breaker park."""
        if self.is_parked and time.monotonic() < self.parked_until:
            return False
        return True

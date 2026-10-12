"""ProxyPool supporting multiple rotation strategies and circuit-breaker health management."""

from __future__ import annotations

import logging
import random
from enum import Enum
from typing import List, Optional, Union

from .affinity import ProxyAffinityManager
from .health import ProxyHealthMonitor
from .identity import ProxyIdentity

logger = logging.getLogger("cloakbrowser.proxy.pool")


class RotationStrategy(str, Enum):
    ROUND_ROBIN = "round_robin"
    RANDOM = "random"
    FAILURE_WEIGHTED = "failure_weighted"


class ProxyPool:
    """Manages a pool of proxies with health monitoring and rotation strategies."""

    def __init__(
        self,
        proxies: Optional[List[Union[str, ProxyIdentity]]] = None,
        strategy: RotationStrategy = RotationStrategy.ROUND_ROBIN,
        health_monitor: Optional[ProxyHealthMonitor] = None,
        affinity_manager: Optional[ProxyAffinityManager] = None,
    ):
        self.strategy = strategy
        self.health_monitor = health_monitor or ProxyHealthMonitor()
        self.affinity_manager = affinity_manager or ProxyAffinityManager()
        self._identities: List[ProxyIdentity] = []
        self._rr_index: int = 0

        if proxies:
            for p in proxies:
                self.add_proxy(p)

    def add_proxy(self, proxy_or_identity: Union[str, ProxyIdentity]) -> ProxyIdentity:
        if isinstance(proxy_or_identity, str):
            identity = ProxyIdentity(proxy=proxy_or_identity)
        else:
            identity = proxy_or_identity
        self._identities.append(identity)
        return identity

    def get_proxy(self, session_id: Optional[str] = None) -> ProxyIdentity:
        """Select a healthy proxy according to strategy and session affinity."""
        if not self._identities:
            raise RuntimeError("ProxyPool is empty")

        # 1. Check sticky session affinity
        if session_id:
            aff = self.affinity_manager.get_affinity(session_id)
            if aff is not None:
                return aff

        # 2. Filter available (non-parked) proxies
        available = [p for p in self._identities if p.is_available]
        if not available:
            logger.warning("[proxy-pool] All proxies parked; falling back to least recently failed")
            available = sorted(self._identities, key=lambda x: x.consecutive_failures)

        selected: ProxyIdentity
        if self.strategy == RotationStrategy.RANDOM:
            selected = random.choice(available)
        elif self.strategy == RotationStrategy.FAILURE_WEIGHTED:
            weights = [max(0.1, p.health) for p in available]
            selected = random.choices(available, weights=weights, k=1)[0]
        else:  # ROUND_ROBIN
            selected = available[self._rr_index % len(available)]
            self._rr_index = (self._rr_index + 1) % len(available)

        if session_id:
            self.affinity_manager.set_affinity(session_id, selected)

        return selected

    def record_success(self, identity: ProxyIdentity, latency: float = 0.0) -> None:
        self.health_monitor.record_success(identity, latency)

    def record_failure(self, identity: ProxyIdentity, error: str) -> None:
        self.health_monitor.record_failure(identity, error)

"""Proxy infrastructure: identity, pool management, health monitoring, and affinity."""

from .identity import ProxyIdentity
from .health import ProxyHealthMonitor
from .affinity import ProxyAffinityManager
from .pool import ProxyPool, RotationStrategy

__all__ = [
    "ProxyIdentity",
    "ProxyHealthMonitor",
    "ProxyAffinityManager",
    "ProxyPool",
    "RotationStrategy",
]

"""Sticky proxy session affinity manager."""

from __future__ import annotations

import logging
from typing import Dict, Optional

from .identity import ProxyIdentity

logger = logging.getLogger("cloakbrowser.proxy.affinity")


class ProxyAffinityManager:
    """Maintains consistent proxy identity across persistent browser sessions."""

    def __init__(self):
        self._affinity_map: Dict[str, ProxyIdentity] = {}

    def get_affinity(self, session_id: str) -> Optional[ProxyIdentity]:
        identity = self._affinity_map.get(session_id)
        if identity and identity.is_available:
            return identity
        return None

    def set_affinity(self, session_id: str, identity: ProxyIdentity) -> None:
        self._affinity_map[session_id] = identity
        logger.debug("[proxy-affinity] Bound session %s to proxy %s", session_id, identity.proxy)

    def clear_affinity(self, session_id: str) -> None:
        self._affinity_map.pop(session_id, None)

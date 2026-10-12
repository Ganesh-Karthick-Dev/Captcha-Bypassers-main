"""Challenge status models and lifecycle states.

Encapsulates observed security challenges (reCAPTCHA, Turnstile, Cloudflare Managed,
hCaptcha) and their lifecycle states without engaging in unauthorized circumvention.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class ChallengeType(str, Enum):
    """Categorized security challenge systems."""
    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V3 = "recaptcha_v3"
    TURNSTILE = "turnstile"
    CLOUDFLARE_MANAGED = "cloudflare_managed"
    HCAPTCHA = "hcaptcha"
    UNKNOWN = "unknown"


class ChallengeState(str, Enum):
    """Lifecycle state of an observed challenge."""
    NOT_PRESENT = "not_present"
    DETECTED = "detected"
    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass
class ChallengeStatus:
    """Snapshot of a detected challenge widget and its verification state."""

    challenge_type: ChallengeType = ChallengeType.UNKNOWN
    state: ChallengeState = ChallengeState.NOT_PRESENT
    frame_reference: Any = None
    detected_at: float = field(default_factory=time.monotonic)
    resolved_at: Optional[float] = None
    token: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def present(self) -> bool:
        """True if a challenge is currently detected and awaiting resolution."""
        return self.state in (ChallengeState.DETECTED, ChallengeState.PENDING)

    @property
    def has_challenge(self) -> bool:
        return self.present

    @property
    def resolved(self) -> bool:
        """True if the challenge has been verified as successfully completed."""
        return self.state == ChallengeState.RESOLVED


"""Challenge lifecycle error classes."""

from __future__ import annotations

from typing import Any, Dict, Optional


class ChallengeUnresolvedError(RuntimeError):
    """Raised when an observed challenge cannot be resolved through authorized mechanisms."""

    def __init__(self, message: str, challenge_type: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.challenge_type = challenge_type
        self.metadata = metadata or {}


class ChallengeTimeoutError(ChallengeUnresolvedError):
    """Raised when waiting for challenge resolution exceeds the allocated deadline."""


class ChallengeExpiredError(ChallengeUnresolvedError):
    """Raised when a challenge or verification token has expired."""

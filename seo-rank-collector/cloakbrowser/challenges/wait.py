"""Challenge wait and verification routines.

Provides controlled polling to verify when a challenge has transitioned to RESOLVED,
or fails deterministically with a ChallengeTimeoutError.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from .detector import detect_challenge, detect_challenge_async
from .recovery import ChallengeTimeoutError, ChallengeUnresolvedError
from .status import ChallengeState, ChallengeStatus

logger = logging.getLogger("cloakbrowser.challenges.wait")


def wait_for_challenge_resolution(
    page: Any,
    timeout: float = 60.0,
    poll_interval: float = 0.5,
    raise_on_timeout: bool = True,
) -> bool:
    """Wait for an active challenge to reach RESOLVED state (sync).

    Returns True if resolved within timeout.
    If timeout expires:
      - raises ChallengeTimeoutError if raise_on_timeout is True
      - returns False if raise_on_timeout is False
    """
    deadline = time.monotonic() + timeout
    last_status: Optional[ChallengeStatus] = None

    while time.monotonic() < deadline:
        status = detect_challenge(page)
        last_status = status

        if status.state == ChallengeState.RESOLVED:
            logger.info("[challenge-wait] Challenge %s successfully resolved", status.challenge_type.value)
            return True

        if status.state == ChallengeState.NOT_PRESENT:
            # If no challenge detected at all, considered clear
            return True

        time.sleep(poll_interval)

    msg = f"Challenge {last_status.challenge_type.value if last_status else 'unknown'} did not resolve within {timeout:.1f}s"
    logger.warning("[challenge-wait] %s", msg)
    if raise_on_timeout:
        raise ChallengeTimeoutError(
            msg,
            challenge_type=last_status.challenge_type.value if last_status else None,
            metadata=last_status.metadata if last_status else {},
        )
    return False


async def wait_for_challenge_resolution_async(
    page: Any,
    timeout: float = 60.0,
    poll_interval: float = 0.5,
    raise_on_timeout: bool = True,
) -> bool:
    """Wait for an active challenge to reach RESOLVED state (async)."""
    deadline = time.monotonic() + timeout
    last_status: Optional[ChallengeStatus] = None

    while time.monotonic() < deadline:
        status = await detect_challenge_async(page)
        last_status = status

        if status.state == ChallengeState.RESOLVED:
            return True

        if status.state == ChallengeState.NOT_PRESENT:
            return True

        await asyncio.sleep(poll_interval)

    msg = f"Challenge {last_status.challenge_type.value if last_status else 'unknown'} did not resolve within {timeout:.1f}s"
    if raise_on_timeout:
        raise ChallengeTimeoutError(
            msg,
            challenge_type=last_status.challenge_type.value if last_status else None,
            metadata=last_status.metadata if last_status else {},
        )
    return False

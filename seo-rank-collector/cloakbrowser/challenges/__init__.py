"""Challenge awareness, lifecycle detection, and authorized testing module."""

from .status import ChallengeState, ChallengeStatus, ChallengeType
from .detector import detect_challenge, detect_challenge_async
from .wait import wait_for_challenge_resolution, wait_for_challenge_resolution_async
from .test_adapter import (
    TestChallengeAdapter,
    OFFICIAL_RECAPTCHA_TEST_KEY,
    OFFICIAL_TURNSTILE_PASS_KEY,
    OFFICIAL_TURNSTILE_BLOCK_KEY,
)
from .recovery import (
    ChallengeUnresolvedError,
    ChallengeTimeoutError,
    ChallengeExpiredError,
)

__all__ = [
    "ChallengeType",
    "ChallengeState",
    "ChallengeStatus",
    "detect_challenge",
    "detect_challenge_async",
    "wait_for_challenge_resolution",
    "wait_for_challenge_resolution_async",
    "TestChallengeAdapter",
    "OFFICIAL_RECAPTCHA_TEST_KEY",
    "OFFICIAL_TURNSTILE_PASS_KEY",
    "OFFICIAL_TURNSTILE_BLOCK_KEY",
    "ChallengeUnresolvedError",
    "ChallengeTimeoutError",
    "ChallengeExpiredError",
]

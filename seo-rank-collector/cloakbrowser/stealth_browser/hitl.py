"""Human-in-the-Loop (HITL) interactive verification checkpointing."""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from cloakbrowser.challenges.status import ChallengeStatus
from .state_verifier import ApplicationStateVerifier
from .verification import VerificationProvider, VerificationResult

logger = logging.getLogger(__name__)


class CheckpointStatus(str, Enum):
    WAITING = "WAITING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


@dataclass
class VerificationCheckpoint:
    domain: str
    challenge_type: str
    created_at: float
    workflow_id: str
    status: CheckpointStatus = CheckpointStatus.WAITING


class InteractiveVerificationProvider(VerificationProvider):
    """Provider that yields control to an authorized human operator (or headed browser),
    monitoring for state clearance via ApplicationStateVerifier before resuming.
    """

    __test__ = False

    def __init__(
        self,
        timeout_seconds: float = 120.0,
        poll_interval: float = 2.0,
        state_verifier: Optional[ApplicationStateVerifier] = None,
        notification_callback: Optional[Callable[[VerificationCheckpoint], None]] = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.poll_interval = poll_interval
        self.verifier = state_verifier or ApplicationStateVerifier()
        self.notification_callback = notification_callback
        self.current_checkpoint: Optional[VerificationCheckpoint] = None

    def verify(self, page: Any, challenge: ChallengeStatus) -> VerificationResult:
        domain = getattr(page, "url", "unknown_domain")
        self.current_checkpoint = VerificationCheckpoint(
            domain=domain,
            challenge_type=challenge.challenge_type.value,
            created_at=time.time(),
            workflow_id=f"wf-{int(time.time())}",
            status=CheckpointStatus.WAITING,
        )

        logger.info(
            "[hitl-checkpoint] Interactive verification required for %s (%s). Checkpoint created.",
            domain,
            challenge.challenge_type.value,
        )

        if self.notification_callback:
            try:
                self.notification_callback(self.current_checkpoint)
            except Exception as e:
                logger.warning("[hitl-checkpoint] Notification callback error: %s", e)

        self.current_checkpoint.status = CheckpointStatus.IN_PROGRESS
        start_time = time.time()

        while time.time() - start_time < self.timeout_seconds:
            # Check if operator or environment cleared the challenge and app is ready
            if self.verifier.challenge_cleared(page):
                logger.info("[hitl-checkpoint] Challenge confirmed cleared by state verifier.")
                self.current_checkpoint.status = CheckpointStatus.COMPLETED
                return VerificationResult.SUCCESS

            time.sleep(self.poll_interval)

        logger.warning(
            "[hitl-checkpoint] Checkpoint timed out after %s seconds.", self.timeout_seconds
        )
        self.current_checkpoint.status = CheckpointStatus.TIMEOUT
        return VerificationResult.TIMEOUT

    async def verify_async(self, page: Any, challenge: ChallengeStatus) -> VerificationResult:
        import asyncio

        domain = getattr(page, "url", "unknown_domain")
        self.current_checkpoint = VerificationCheckpoint(
            domain=domain,
            challenge_type=challenge.challenge_type.value,
            created_at=time.time(),
            workflow_id=f"wf-{int(time.time())}",
            status=CheckpointStatus.WAITING,
        )

        logger.info(
            "[hitl-checkpoint-async] Interactive verification checkpoint waiting for %s (%s).",
            domain,
            challenge.challenge_type.value,
        )

        if self.notification_callback:
            try:
                self.notification_callback(self.current_checkpoint)
            except Exception as e:
                logger.warning("[hitl-checkpoint-async] Notification callback error: %s", e)

        self.current_checkpoint.status = CheckpointStatus.IN_PROGRESS
        start_time = time.time()

        while time.time() - start_time < self.timeout_seconds:
            if await self.verifier.challenge_cleared_async(page):
                self.current_checkpoint.status = CheckpointStatus.COMPLETED
                return VerificationResult.SUCCESS

            await asyncio.sleep(self.poll_interval)

        self.current_checkpoint.status = CheckpointStatus.TIMEOUT
        return VerificationResult.TIMEOUT

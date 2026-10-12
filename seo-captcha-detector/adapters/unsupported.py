"""UnsupportedChallengeAdapter: Explicit clean failure mode for unsupported challenges."""
from models import ChallengeInfo, ResolutionResult
from session import CrawlerSession
from adapters.base import BaseSolverAdapter


class UnsupportedChallengeAdapter(BaseSolverAdapter):
    def can_handle(self, challenge: ChallengeInfo) -> bool:
        # Handles any challenge marked supported=False or unknown blocks
        return not challenge.supported

    def resolve(self, challenge: ChallengeInfo, session: CrawlerSession) -> ResolutionResult:
        # DO NOT call the solver!
        action = challenge.action_required or "manual_or_external_handler_required"
        error_msg = challenge.note or f"Challenge type '{challenge.type.value}' is not supported by automated sidecar."

        return ResolutionResult(
            success=False,
            status="unsupported",
            challenge_type=challenge.type,
            error=error_msg,
            has_token=False,
            has_clearance=False,
        )

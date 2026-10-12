"""ChallengeHandler: Orchestrates adapter dispatch and resolution."""
from typing import List, Optional
from models import ChallengeInfo, ResolutionResult
from session import CrawlerSession
from adapters.base import BaseSolverAdapter
from adapters.cloudflare import CloudflareAdapter
from adapters.widget import WidgetAdapter
from adapters.unsupported import UnsupportedChallengeAdapter


class ChallengeHandler:
    def __init__(self, solver_url: Optional[str] = None, timeout: float = 120.0):
        if not solver_url or solver_url == "http://127.0.0.1:8877":
            import os
            solver_url = os.getenv("SOLVER_URL", f"http://127.0.0.1:{os.getenv('PORT', '5001')}")
        self.adapters: List[BaseSolverAdapter] = [
            CloudflareAdapter(solver_url=solver_url, timeout=timeout),
            WidgetAdapter(solver_url=solver_url, timeout=timeout),
            UnsupportedChallengeAdapter(solver_url=solver_url, timeout=timeout),
        ]

    def handle(self, challenge: ChallengeInfo, session: CrawlerSession) -> ResolutionResult:
        """Find the matching adapter and resolve the challenge."""
        if not challenge.present:
            return ResolutionResult(
                success=True,
                status="no_action_needed",
                challenge_type=challenge.type,
            )

        for adapter in self.adapters:
            if adapter.can_handle(challenge):
                return adapter.resolve(challenge, session)

        # Fallback if no adapter claimed it
        return ResolutionResult(
            success=False,
            status="unsupported",
            challenge_type=challenge.type,
            error=f"No suitable adapter found for challenge type '{challenge.type.value}'.",
        )

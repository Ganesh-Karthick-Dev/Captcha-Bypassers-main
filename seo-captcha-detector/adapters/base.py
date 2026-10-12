"""Base adapter interface for solver bridges."""
from abc import ABC, abstractmethod
from typing import Optional
import httpx
from models import ChallengeInfo, ResolutionResult
from session import CrawlerSession


class BaseSolverAdapter(ABC):
    def __init__(self, solver_url: str = "http://127.0.0.1:8877", timeout: float = 60.0):
        self.solver_url = solver_url.rstrip("/")
        self.timeout = timeout

    @abstractmethod
    def can_handle(self, challenge: ChallengeInfo) -> bool:
        """Return True if this adapter supports the given challenge."""
        pass

    @abstractmethod
    def resolve(self, challenge: ChallengeInfo, session: CrawlerSession) -> ResolutionResult:
        """Execute resolution via solver and update session state."""
        pass

"""Explicit state machine for SEO Copilot security testing workflows."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("cloakbrowser.stealth_browser.state_machine")


class WorkflowState(str, Enum):
    INIT = "INIT"
    BROWSER_STARTED = "BROWSER_STARTED"
    NAVIGATING = "NAVIGATING"
    REGISTRATION = "REGISTRATION"
    CHALLENGE_DETECTED = "CHALLENGE_DETECTED"
    TEST_VERIFICATION = "TEST_VERIFICATION"
    VERIFIED = "VERIFIED"
    REGISTRATION_COMPLETED = "REGISTRATION_COMPLETED"
    AUTHENTICATED = "AUTHENTICATED"
    SEO_ANALYSIS = "SEO_ANALYSIS"
    REPORT_GENERATION = "REPORT_GENERATION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    SESSION_EXPIRED = "SESSION_EXPIRED"


@dataclass
class StateTransition:
    from_state: WorkflowState
    to_state: WorkflowState
    timestamp: float = field(default_factory=time.monotonic)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ChallengeStateMachine:
    """Manages explicit state transitions for SEO Copilot workflows."""

    # Allowed state transitions
    _VALID_TRANSITIONS: Dict[WorkflowState, Set[WorkflowState]] = {
        WorkflowState.INIT: {WorkflowState.BROWSER_STARTED, WorkflowState.FAILED},
        WorkflowState.BROWSER_STARTED: {WorkflowState.NAVIGATING, WorkflowState.FAILED},
        WorkflowState.NAVIGATING: {
            WorkflowState.REGISTRATION,
            WorkflowState.CHALLENGE_DETECTED,
            WorkflowState.SEO_ANALYSIS,
            WorkflowState.FAILED,
            WorkflowState.TIMEOUT,
        },
        WorkflowState.REGISTRATION: {
            WorkflowState.CHALLENGE_DETECTED,
            WorkflowState.VERIFIED,
            WorkflowState.REGISTRATION_COMPLETED,
            WorkflowState.FAILED,
            WorkflowState.TIMEOUT,
        },
        WorkflowState.CHALLENGE_DETECTED: {
            WorkflowState.TEST_VERIFICATION,
            WorkflowState.SESSION_EXPIRED,
            WorkflowState.FAILED,
            WorkflowState.TIMEOUT,
        },
        WorkflowState.TEST_VERIFICATION: {
            WorkflowState.VERIFIED,
            WorkflowState.FAILED,
            WorkflowState.TIMEOUT,
            WorkflowState.SESSION_EXPIRED,
        },
        WorkflowState.VERIFIED: {
            WorkflowState.REGISTRATION,
            WorkflowState.REGISTRATION_COMPLETED,
            WorkflowState.AUTHENTICATED,
            WorkflowState.SEO_ANALYSIS,
            WorkflowState.FAILED,
        },
        WorkflowState.REGISTRATION_COMPLETED: {
            WorkflowState.AUTHENTICATED,
            WorkflowState.NAVIGATING,
            WorkflowState.FAILED,
        },
        WorkflowState.AUTHENTICATED: {
            WorkflowState.SEO_ANALYSIS,
            WorkflowState.NAVIGATING,
            WorkflowState.FAILED,
        },
        WorkflowState.SEO_ANALYSIS: {
            WorkflowState.REPORT_GENERATION,
            WorkflowState.FAILED,
            WorkflowState.TIMEOUT,
        },
        WorkflowState.REPORT_GENERATION: {
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
        },
        WorkflowState.COMPLETED: set(),
        WorkflowState.FAILED: set(),
        WorkflowState.TIMEOUT: set(),
        WorkflowState.SESSION_EXPIRED: {WorkflowState.NAVIGATING, WorkflowState.FAILED},
    }

    def __init__(self, initial_state: WorkflowState = WorkflowState.INIT):
        self.current_state: WorkflowState = initial_state
        self.history: List[StateTransition] = []

    def can_transition(self, new_state: WorkflowState) -> bool:
        """Check whether transition from current_state to new_state is allowed."""
        # Terminal failure/timeout transitions are permitted from any non-terminal state
        if new_state in (WorkflowState.FAILED, WorkflowState.TIMEOUT):
            return self.current_state not in (WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.TIMEOUT)

        allowed = self._VALID_TRANSITIONS.get(self.current_state, set())
        return new_state in allowed

    def transition(self, new_state: WorkflowState, metadata: Optional[Dict[str, Any]] = None) -> WorkflowState:
        """Execute a state transition with validation and history recording."""
        if not self.can_transition(new_state):
            logger.warning(
                "[state-machine] Transition from %s to %s is not permitted in strict state graph",
                self.current_state.value,
                new_state.value,
            )

        prev = self.current_state
        self.current_state = new_state
        entry = StateTransition(from_state=prev, to_state=new_state, metadata=metadata or {})
        self.history.append(entry)
        logger.info("[state-machine] %s -> %s", prev.value, new_state.value)
        return self.current_state

    @property
    def is_terminal(self) -> bool:
        return self.current_state in (WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.TIMEOUT)

"""Autonomous execution and recovery engine package."""

from .controller import AutomationController
from .recovery import RecoveryManager
from .retry import ErrorCategory, RetryAttempt, RetryPolicy, classify_error
from .state import AutomationState, StepRecord, StepStatus

__all__ = [
    "AutomationController",
    "RecoveryManager",
    "ErrorCategory",
    "RetryAttempt",
    "RetryPolicy",
    "classify_error",
    "AutomationState",
    "StepRecord",
    "StepStatus",
]

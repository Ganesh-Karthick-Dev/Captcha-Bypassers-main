"""Autonomous execution controller orchestrating end-to-end browser workflows."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from ..challenges.detector import detect_challenge
from ..challenges.status import ChallengeState
from ..challenges.test_adapter import TestChallengeAdapter
from ..challenges.wait import wait_for_challenge_resolution
from .recovery import RecoveryManager
from .retry import ErrorCategory, RetryPolicy, classify_error
from .state import AutomationState, StepRecord, StepStatus

logger = logging.getLogger("cloakbrowser.automation.controller")


class AutomationController:
    """Orchestrates autonomous browser actions with automated retry and recovery."""

    def __init__(
        self,
        page: Any,
        retry_policy: Optional[RetryPolicy] = None,
        challenge_adapter: Optional[TestChallengeAdapter] = None,
    ):
        self.page = page
        self.retry_policy = retry_policy or RetryPolicy()
        self.challenge_adapter = challenge_adapter or TestChallengeAdapter()
        self.recovery = RecoveryManager()

    def run_step(
        self,
        state: AutomationState,
        step_name: str,
        action: Callable[[], Any],
        verify_fn: Optional[Callable[[Any], bool]] = None,
        check_challenges: bool = True,
    ) -> Any:
        """Execute one workflow step with autonomous recovery and challenge handling."""
        record = state.start_step(step_name)
        start_t = time.monotonic()

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            record.attempts = attempt
            try:
                # 1. Execute the core action
                result = action()

                # 2. Check and handle challenges if present
                if check_challenges:
                    self._handle_active_challenge()

                # 3. Optional post-action verification
                if verify_fn is not None:
                    if not verify_fn(result):
                        raise RuntimeError(f"Step verification failed for {step_name}")

                state.finish_step(record, StepStatus.SUCCESS)
                return result

            except BaseException as exc:
                cat = classify_error(exc)
                elapsed = time.monotonic() - start_t

                if cat == ErrorCategory.NON_RECOVERABLE or attempt >= self.retry_policy.max_attempts:
                    state.finish_step(record, StepStatus.FAILED, error=str(exc))
                    logger.error("[controller] Step %s failed permanently: %s", step_name, exc)
                    raise

                # Determine recovery action
                recovery_action = "backoff_sleep"
                if "covered" in str(exc).lower() or "pointer_events" in str(exc).lower():
                    self.recovery.dismiss_overlays(self.page)
                    recovery_action = "dismiss_overlays"
                elif "context" in str(exc).lower() or "detached" in str(exc).lower():
                    self.recovery.recover_stale_context(self.page)
                    recovery_action = "recover_stale_context"
                elif cat == ErrorCategory.TRANSIENT and attempt == 2:
                    self.recovery.reload_and_wait(self.page)
                    recovery_action = "reload_and_wait"

                self.retry_policy.record(attempt, str(exc), elapsed, recovery_action, error=str(exc))
                delay = self.retry_policy.get_delay(attempt)
                time.sleep(delay)

        state.finish_step(record, StepStatus.FAILED, error="Exceeded max attempts")
        raise RuntimeError(f"Step {step_name} exceeded max retry attempts")

    def _handle_active_challenge(self) -> None:
        """Autonomous check and resolution for observed challenges."""
        status = detect_challenge(self.page)
        if not status.present:
            return

        logger.info("[controller] Security challenge %s detected (state=%s)", status.challenge_type.value, status.state.value)

        # 1. Attempt resolution via authorized test adapter
        handled = self.challenge_adapter.handle_test_challenge(self.page, status)
        if handled:
            logger.info("[controller] Test adapter handled challenge %s", status.challenge_type.value)

        # 2. Wait for challenge state to reach RESOLVED
        wait_for_challenge_resolution(self.page, timeout=30.0, poll_interval=0.5)

    def navigate(self, state: AutomationState, url: str, timeout: float = 30000) -> Any:
        return self.run_step(
            state,
            f"navigate to {url}",
            lambda: self.page.goto(url, timeout=timeout, wait_until="domcontentloaded"),
        )

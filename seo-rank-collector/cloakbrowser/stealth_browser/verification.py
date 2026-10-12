"""Verification provider abstractions for authorized security testing environments.

Provides controlled, deterministic test and staging verification adapters
without attempting to bypass, crack, or solve live production anti-bot protections.
"""

from __future__ import annotations

import abc
import logging
import time
from enum import Enum
from typing import Any, Dict, Optional

from ..challenges.status import ChallengeStatus, ChallengeType

logger = logging.getLogger("cloakbrowser.stealth_browser.verification")


class VerificationMode(str, Enum):
    TEST = "test"
    STAGING = "staging"
    INTERACTIVE = "interactive"
    PRODUCTION = "production"


class VerificationResult(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    SESSION_EXPIRED = "SESSION_EXPIRED"



class VerificationProvider(abc.ABC):
    """Abstract base for challenge verification providers."""

    @abc.abstractmethod
    def verify(self, page: Any, challenge: ChallengeStatus) -> VerificationResult:
        """Process an observed challenge in an authorized environment and return result."""
        raise NotImplementedError

    @abc.abstractmethod
    async def verify_async(self, page: Any, challenge: ChallengeStatus) -> VerificationResult:
        raise NotImplementedError


class TestVerificationProvider(VerificationProvider):
    """Deterministic mock provider for automated unit and CI pipelines.

    Allows testing all challenge workflow outcomes (SUCCESS, FAILURE, TIMEOUT, SESSION_EXPIRED)
    deterministically without network dependencies.
    """

    __test__ = False

    def __init__(
        self,
        simulated_result: VerificationResult = VerificationResult.SUCCESS,
        test_tokens: Optional[Dict[ChallengeType, str]] = None,
        inject_token_on_page: bool = True,
    ):
        self.simulated_result = simulated_result
        self.test_tokens = test_tokens or {
            ChallengeType.TURNSTILE: "0.test_mock_turnstile_pass_token",
            ChallengeType.RECAPTCHA_V2: "mock_authorized_test_recaptcha_token",
            ChallengeType.UNKNOWN: "mock_generic_pass_token",
        }
        self.inject_token_on_page = inject_token_on_page

    def verify(self, page: Any, challenge: ChallengeStatus) -> VerificationResult:
        logger.info(
            "[test-verification] Processing %s with simulated result=%s",
            challenge.challenge_type.value,
            self.simulated_result.value,
        )

        if self.simulated_result == VerificationResult.SUCCESS:
            token = self.test_tokens.get(challenge.challenge_type, self.test_tokens.get(ChallengeType.UNKNOWN, ""))
            if self.inject_token_on_page and token and hasattr(page, "evaluate"):
                try:
                    js = """
                    (token) => {
                        const t = document.querySelector('input[name="cf-turnstile-response"], #cf-token, #g-recaptcha-response');
                        if (t) { t.value = token; return true; }
                        return false;
                    }
                    """
                    page.evaluate(js, token)
                except Exception as e:
                    logger.debug("[test-verification] Token injection skipped or failed: %s", e)
            return VerificationResult.SUCCESS

        return self.simulated_result

    async def verify_async(self, page: Any, challenge: ChallengeStatus) -> VerificationResult:
        return self.verify(page, challenge)


class StagingVerificationProvider(VerificationProvider):
    """Authorized staging environment provider utilizing official vendor test keys / sandbox tokens."""

    def __init__(
        self,
        staging_bypass_header: Optional[str] = None,
        vendor_sandbox_token: Optional[str] = None,
    ):
        self.staging_bypass_header = staging_bypass_header
        self.vendor_sandbox_token = vendor_sandbox_token or "1x00000000000000000000AA"  # Cloudflare Turnstile Always Pass

    def verify(self, page: Any, challenge: ChallengeStatus) -> VerificationResult:
        logger.info(
            "[staging-verification] Applying staging credentials for %s",
            challenge.challenge_type.value,
        )
        try:
            js = """
            (token) => {
                const t = document.querySelector('input[name="cf-turnstile-response"], #g-recaptcha-response');
                if (t) { t.value = token; return true; }
                return false;
            }
            """
            injected = bool(page.evaluate(js, self.vendor_sandbox_token))
            return VerificationResult.SUCCESS if injected else VerificationResult.FAILURE
        except Exception as e:
            logger.warning("[staging-verification] Staging token injection error: %s", e)
            return VerificationResult.FAILURE

    async def verify_async(self, page: Any, challenge: ChallengeStatus) -> VerificationResult:
        return self.verify(page, challenge)


class RouteMockVerificationProvider(VerificationProvider):
    """Network-level interception provider for local and staging integration testing.

    Intercepts backend verification endpoints (e.g. siteverify, verify-token)
    and fulfills requests with deterministic mock responses using Playwright's route API.
    """

    __test__ = False

    def __init__(
        self,
        endpoint_patterns: Optional[list[str]] = None,
        mock_response: Optional[Dict[str, Any]] = None,
        test_token: str = "mock_verified_token_staging",
    ):
        self.endpoint_patterns = endpoint_patterns or [
            "**/turnstile/v0/siteverify",
            "**/recaptcha/api/siteverify",
            "**/api/verify-token",
        ]
        self.mock_response = mock_response or {
            "success": True,
            "challenge_ts": "2026-09-22T10:00:00Z",
            "hostname": "localhost",
        }
        self.test_token = test_token
        self._routes_registered = False

    def setup_routes(self, page: Any) -> None:
        """Register network route handlers on the target page."""
        import json

        if self._routes_registered or not hasattr(page, "route"):
            return

        body = json.dumps(self.mock_response)

        def _handle_route(route: Any) -> None:
            try:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=body,
                )
            except Exception as e:
                logger.debug("[route-mock] Route fulfill error: %s", e)

        for pattern in self.endpoint_patterns:
            try:
                page.route(pattern, _handle_route)
            except Exception as e:
                logger.debug("[route-mock] Registering route %s failed: %s", pattern, e)

        self._routes_registered = True
        logger.info("[route-mock] Registered mock routes for: %s", self.endpoint_patterns)

    def verify(self, page: Any, challenge: ChallengeStatus) -> VerificationResult:
        self.setup_routes(page)

        # Inject client-side token so form submissions include verification evidence
        js_inject = """
        (token) => {
            const el = document.querySelector('input[name="cf-turnstile-response"], #g-recaptcha-response');
            if (el) {
                el.value = token;
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }
            return false;
        }
        """
        try:
            if hasattr(page, "evaluate"):
                page.evaluate(js_inject, self.test_token)
            return VerificationResult.SUCCESS
        except Exception as e:
            logger.warning("[route-mock] Client-side token update failed: %s", e)
            return VerificationResult.FAILURE

    async def verify_async(self, page: Any, challenge: ChallengeStatus) -> VerificationResult:
        return self.verify(page, challenge)


class VerificationProviderFactory:
    """Factory to instantiate the appropriate VerificationProvider based on VerificationMode."""

    @staticmethod
    def create(
        mode: VerificationMode | str,
        **kwargs: Any,
    ) -> VerificationProvider:
        mode_val = VerificationMode(mode) if isinstance(mode, str) else mode
        if mode_val == VerificationMode.TEST:
            return TestVerificationProvider(**kwargs)
        elif mode_val == VerificationMode.STAGING:
            return StagingVerificationProvider(**kwargs)
        elif mode_val in (VerificationMode.INTERACTIVE, VerificationMode.PRODUCTION):
            from .hitl import InteractiveVerificationProvider
            return InteractiveVerificationProvider(**kwargs)
        raise ValueError(f"Unsupported verification mode: {mode}")


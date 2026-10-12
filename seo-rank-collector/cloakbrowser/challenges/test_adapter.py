"""Authorized test adapters and staging environment challenge handlers.

Facilitates autonomous test pipelines using official test keys, staging mock tokens,
or authorized test credentials without manual intervention or circumventing anti-abuse systems.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from .status import ChallengeStatus, ChallengeType, ChallengeState

logger = logging.getLogger("cloakbrowser.challenges.test_adapter")

# Official vendor test sitekeys (guaranteed to pass in test mode)
OFFICIAL_RECAPTCHA_TEST_KEY = "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
OFFICIAL_TURNSTILE_PASS_KEY = "1x00000000000000000000AA"
OFFICIAL_TURNSTILE_BLOCK_KEY = "2x00000000000000000000AB"


class TestChallengeAdapter:
    """Handles challenges in authorized testing and staging environments."""

    __test__ = False

    def __init__(
        self,
        test_tokens: Optional[Dict[ChallengeType, str]] = None,
        custom_resolver: Optional[Callable[[Any, ChallengeStatus], bool]] = None,
    ):
        self._test_tokens = test_tokens or {}
        self._custom_resolver = custom_resolver

    def handle_test_challenge(self, page: Any, status: ChallengeStatus) -> bool:
        """Attempt to satisfy a challenge using authorized test tokens or custom handlers."""
        if self._custom_resolver is not None:
            try:
                res = self._custom_resolver(page, status)
                if res:
                    logger.info("[challenge-test] Custom test resolver successfully handled %s", status.challenge_type)
                    return True
            except Exception as e:
                logger.warning("[challenge-test] Custom test resolver error: %s", e)

        token = self._test_tokens.get(status.challenge_type)
        if not token:
            token = self._test_tokens.get(ChallengeType.UNKNOWN)

        if token:
            return self._inject_test_token(page, status.challenge_type, token)

        return False

    def _inject_test_token(self, page: Any, ctype: ChallengeType, token: str) -> bool:
        """Inject an authorized test token into the page's response field (sync)."""
        js = """
        (token) => {
            let injected = false;
            const rc = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
            if (rc) { rc.value = token; injected = true; }

            const cf = document.querySelector('input[name="cf-turnstile-response"]');
            if (cf) { cf.value = token; injected = true; }

            const hc = document.querySelector('textarea[name="h-captcha-response"]');
            if (hc) { hc.value = token; injected = true; }

            return injected;
        }
        """
        try:
            res = bool(page.evaluate(js, token))
            if res:
                logger.info("[challenge-test] Injected test token for %s", ctype.value)
            return res
        except Exception as e:
            logger.debug("[challenge-test] Token injection failed: %s", e)
            return False

    async def handle_test_challenge_async(self, page: Any, status: ChallengeStatus) -> bool:
        """Attempt to satisfy a challenge in async mode."""
        token = self._test_tokens.get(status.challenge_type) or self._test_tokens.get(ChallengeType.UNKNOWN)
        if not token:
            return False

        js = """
        (token) => {
            let injected = false;
            const rc = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
            if (rc) { rc.value = token; injected = true; }

            const cf = document.querySelector('input[name="cf-turnstile-response"]');
            if (cf) { cf.value = token; injected = true; }

            const hc = document.querySelector('textarea[name="h-captcha-response"]');
            if (hc) { hc.value = token; injected = true; }

            return injected;
        }
        """
        try:
            return bool(await page.evaluate(js, token))
        except Exception:
            return False

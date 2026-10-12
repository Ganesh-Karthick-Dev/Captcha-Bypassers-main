"""Application state verifier for independently validating challenge clearance and readiness."""

import logging
from typing import Any, Optional, List

logger = logging.getLogger(__name__)


class ApplicationStateVerifier:
    """Independently verifies whether challenge conditions have truly cleared,
    the user session is authenticated, and the target application is ready.
    
    Acts as the single source of truth for workflow advancement rather than
    relying solely on verification provider signals.
    """

    DEFAULT_CHALLENGE_SELECTORS: List[str] = [
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "#challenge-running",
        "#cf-challenge-running",
        ".cf-turnstile-wrapper",
        "#turnstile-wrapper",
        "div[class*='turnstile']",
    ]

    DEFAULT_AUTH_SELECTORS: List[str] = [
        "[data-testid='user-profile']",
        "#user-profile",
        "#dashboard",
        ".dashboard",
        "a[href*='logout']",
        "button[name='logout']",
        "#logout-btn",
        ".user-avatar",
    ]

    def __init__(
        self,
        challenge_selectors: Optional[List[str]] = None,
        auth_selectors: Optional[List[str]] = None,
    ):
        self.challenge_selectors = challenge_selectors or self.DEFAULT_CHALLENGE_SELECTORS
        self.auth_selectors = auth_selectors or self.DEFAULT_AUTH_SELECTORS

    def challenge_cleared(self, page: Any) -> bool:
        """Verify that challenge frames, overlays, and interstitial elements are no longer present."""
        if not hasattr(page, "query_selector"):
            return True

        for selector in self.challenge_selectors:
            try:
                el = page.query_selector(selector)
                if el:
                    # Check if element is visible
                    if hasattr(el, "is_visible") and el.is_visible():
                        logger.debug("[state-verifier] Challenge element still visible: %s", selector)
                        return False
            except Exception as e:
                logger.debug("[state-verifier] Selector check error for %s: %s", selector, e)

        # Also inspect page title for Cloudflare / DDoS interstitial markers
        try:
            title = page.title() if hasattr(page, "title") else ""
            if any(t in title.lower() for t in ["just a moment", "attention required", "ddos-guard", "security check"]):
                logger.debug("[state-verifier] Interstitial title detected: %s", title)
                return False
        except Exception as e:
            logger.debug("[state-verifier] Title check error: %s", e)

        return True

    async def challenge_cleared_async(self, page: Any) -> bool:
        """Asynchronous verification that challenge elements are cleared."""
        if not hasattr(page, "query_selector"):
            return True

        for selector in self.challenge_selectors:
            try:
                el = await page.query_selector(selector)
                if el:
                    if hasattr(el, "is_visible") and await el.is_visible():
                        return False
            except Exception:
                pass

        try:
            title = await page.title() if hasattr(page, "title") else ""
            if any(t in title.lower() for t in ["just a moment", "attention required", "ddos-guard"]):
                return False
        except Exception:
            pass

        return True

    def authenticated(self, page: Any, custom_selector: Optional[str] = None) -> bool:
        """Verify whether an authenticated session is active on the page."""
        selectors = [custom_selector] if custom_selector else self.auth_selectors
        if not hasattr(page, "query_selector"):
            return False

        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el:
                    if not hasattr(el, "is_visible") or el.is_visible():
                        return True
            except Exception:
                pass

        # Also check for presence of session cookies in context
        try:
            if hasattr(page, "context") and hasattr(page.context, "cookies"):
                cookies = page.context.cookies()
                auth_cookie_names = ["session", "session_id", "auth_token", "jwt", "remember_token", "logged_in"]
                for c in cookies:
                    if any(name in c.get("name", "").lower() for name in auth_cookie_names):
                        return True
        except Exception as e:
            logger.debug("[state-verifier] Cookie check error: %s", e)

        return False

    async def authenticated_async(self, page: Any, custom_selector: Optional[str] = None) -> bool:
        """Asynchronously verify whether an authenticated session is active."""
        selectors = [custom_selector] if custom_selector else self.auth_selectors
        if not hasattr(page, "query_selector"):
            return False

        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    if not hasattr(el, "is_visible") or await el.is_visible():
                        return True
            except Exception:
                pass

        try:
            if hasattr(page, "context") and hasattr(page.context, "cookies"):
                cookies = await page.context.cookies()
                auth_cookie_names = ["session", "session_id", "auth_token", "jwt"]
                for c in cookies:
                    if any(name in c.get("name", "").lower() for name in auth_cookie_names):
                        return True
        except Exception:
            pass

        return False

    def application_ready(self, page: Any, require_auth: bool = False) -> bool:
        """Single source of truth: ensures challenge is cleared, no HTTP error interstitial,
        and optionally that authentication has completed."""
        if not self.challenge_cleared(page):
            logger.info("[state-verifier] Application NOT ready: challenge is not cleared.")
            return False

        if require_auth and not self.authenticated(page):
            logger.info("[state-verifier] Application NOT ready: authenticated state missing.")
            return False

        return True

    async def application_ready_async(self, page: Any, require_auth: bool = False) -> bool:
        """Asynchronous check of application readiness."""
        cleared = await self.challenge_cleared_async(page)
        if not cleared:
            return False

        if require_auth:
            authed = await self.authenticated_async(page)
            if not authed:
                return False

        return True

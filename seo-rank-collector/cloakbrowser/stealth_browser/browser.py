"""StealthBrowser wrapper integrating CloakBrowser core launch with domain security and session persistence."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..browser import launch, launch_persistent_context
from .domain_security import AuthorizedDomainValidator
from .session import SessionPersistence

logger = logging.getLogger("cloakbrowser.stealth_browser.browser")


class StealthBrowserConfig:
    """Configurable browser settings for deterministic SEO testing."""

    def __init__(
        self,
        headless: bool = True,
        humanize: bool = True,
        viewport: Optional[Dict[str, int]] = None,
        user_agent: Optional[str] = None,
        locale: Optional[str] = None,
        timezone: Optional[str] = None,
        navigation_timeout_ms: float = 30000.0,
        action_timeout_ms: float = 15000.0,
        screenshot_on_failure: bool = True,
        user_data_dir: Optional[Path] = None,
    ):
        self.headless = headless
        self.humanize = humanize
        self.viewport = viewport or {"width": 1280, "height": 800}
        self.user_agent = user_agent
        self.locale = locale
        self.timezone = timezone
        self.navigation_timeout_ms = navigation_timeout_ms
        self.action_timeout_ms = action_timeout_ms
        self.screenshot_on_failure = screenshot_on_failure
        self.user_data_dir = user_data_dir


class StealthBrowser:
    """Session manager integrating CloakBrowser stealth launch with SEO Copilot testing safeguards."""

    def __init__(
        self,
        config: Optional[StealthBrowserConfig] = None,
        domain_validator: Optional[AuthorizedDomainValidator] = None,
        session_persistence: Optional[SessionPersistence] = None,
    ):
        self.config = config or StealthBrowserConfig()
        self.validator = domain_validator or AuthorizedDomainValidator()
        self.session_persistence = session_persistence or SessionPersistence()
        self._browser: Any = None
        self._context: Any = None

    def start(self) -> StealthBrowser:
        """Launch browser session with configured parameters."""
        launch_kwargs: Dict[str, Any] = {
            "headless": self.config.headless,
            "humanize": self.config.humanize,
        }
        if self.config.user_agent:
            launch_kwargs["user_agent"] = self.config.user_agent
        if self.config.locale:
            launch_kwargs["locale"] = self.config.locale
        if self.config.timezone:
            launch_kwargs["timezone"] = self.config.timezone

        if self.config.user_data_dir:
            self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
            self._context = launch_persistent_context(
                str(self.config.user_data_dir),
                viewport=self.config.viewport,
                **launch_kwargs,
            )
            self._browser = None
        else:
            self._browser = launch(**launch_kwargs)
            self._context = self._browser.new_context(viewport=self.config.viewport)

        self._context.set_default_navigation_timeout(self.config.navigation_timeout_ms)
        self._context.set_default_timeout(self.config.action_timeout_ms)
        logger.info("[stealth-browser] Browser started successfully")
        return self

    def new_page(self) -> Any:
        """Create a new page managed by this session."""
        if not self._context:
            self.start()
        page = self._context.new_page()

        # Wrap goto to automatically enforce domain authorization
        orig_goto = page.goto

        def _safe_goto(url: str, **kwargs: Any) -> Any:
            self.validator.validate_url(url)
            resp = orig_goto(url, **kwargs)
            self.validator.validate_navigation(page.url)
            return resp

        page.goto = _safe_goto
        return page

    def close(self) -> None:
        """Clean up active pages, contexts, and browser instances."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
        except Exception as e:
            logger.debug("[stealth-browser] Close encountered exception: %s", e)
        finally:
            self._context = None
            self._browser = None

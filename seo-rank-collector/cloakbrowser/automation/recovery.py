"""Automated recovery routines for transient and recoverable browser failures."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("cloakbrowser.automation.recovery")


class RecoveryManager:
    """Executes safe, targeted recovery actions on pages and frames."""

    @staticmethod
    def dismiss_overlays(page: Any) -> bool:
        """Attempt to dismiss common modal backdrops, popups, or cookie consent banners."""
        js_dismiss = """
        (() => {
            let dismissed = false;
            // 1. Click close buttons on modals / backdrops
            const closeSelectors = [
                'button.close', 'button[aria-label="Close"]', 'button[aria-label="close"]',
                '.modal-close', '.popup-close', '.btn-close', '[data-dismiss="modal"]',
                '#close', '.close-btn'
            ];
            for (const sel of closeSelectors) {
                const btn = document.querySelector(sel);
                if (btn && btn.offsetParent !== null) {
                    btn.click();
                    dismissed = true;
                }
            }

            // 2. Hide stubborn full-page blocking backdrops if transparent
            const backdrops = document.querySelectorAll('.modal-backdrop, .overlay-backdrop, .fade.show');
            for (const bd of backdrops) {
                bd.style.pointerEvents = 'none';
                dismissed = true;
            }

            return dismissed;
        })()
        """
        try:
            res = bool(page.evaluate(js_dismiss))
            if res:
                logger.info("[recovery] Successfully dismissed blocking overlay")
            return res
        except Exception as e:
            logger.debug("[recovery] Overlay dismissal failed: %s", e)
            return False

    @staticmethod
    def reload_and_wait(page: Any, timeout: float = 30000) -> bool:
        """Reload the page and wait for domcontentloaded."""
        try:
            logger.info("[recovery] Reloading page to recover from transient error")
            page.reload(timeout=timeout, wait_until="domcontentloaded")
            time.sleep(1.0)
            return True
        except Exception as e:
            logger.warning("[recovery] Reload failed: %s", e)
            return False

    @staticmethod
    def recover_stale_context(page: Any) -> bool:
        """Invalidate stealth world to ensure clean new isolated context creation."""
        stealth = getattr(page, "_stealth_world", None)
        if stealth is not None:
            try:
                stealth.invalidate()
                logger.info("[recovery] Re-initialized isolated world execution context")
                return True
            except Exception as e:
                logger.warning("[recovery] Stale context invalidation failed: %s", e)
        return False

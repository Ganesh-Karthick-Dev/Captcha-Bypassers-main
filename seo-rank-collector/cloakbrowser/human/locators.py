"""Locator capability classification, routing, and explicit fallback handling.

Provides an explicit capability model for Playwright selectors in isolated-world
humanized automation. Determines whether a selector can be resolved inside the
CDP isolated world or must fall back to native Playwright with structured logging.
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .config import HumanConfig
from .stealth_dom import UnsupportedHumanizeSelectorError

logger = logging.getLogger("cloakbrowser.human.locators")


class SelectorCapability(str, Enum):
    """Explicit capability classification for selectors in humanize mode."""
    SUPPORTED = "supported"
    FALLBACK = "fallback"
    UNSUPPORTED = "unsupported"


# Matches Playwright internal engines that are supported in isolated world
_SUPPORTED_PREFIXES = (
    "css=",
    "xpath=",
    "//",
    "text=",
    "internal:text=",
    "internal:label=",
    "internal:testid=",
    "internal:attr=[placeholder=",
    "internal:attr=[title=",
    "internal:attr=[alt=",
)

# Patterns requiring fallback because isolated world doesn't have full accessible-name algorithm
_FALLBACK_PATTERNS = (
    "internal:role=",
    "internal:has=",
    "internal:has-text=",
    "internal:control=enter-frame",
    ":visible",
    ":nth-match",
)


def classify_selector(selector: str) -> SelectorCapability:
    """Classify selector capability for isolated-world resolution.

    - SUPPORTED: Can be resolved directly in CDP isolated world without page script awareness.
    - FALLBACK: Advanced Playwright selector (e.g. role, complex filter, enter-frame) that
      can fall back to native Playwright execution if strict_stealth=False.
    - UNSUPPORTED: Malformed or unroutable selector grammar.
    """
    if not selector or not isinstance(selector, str):
        return SelectorCapability.UNSUPPORTED

    # Check for complex fallback engines
    for pattern in _FALLBACK_PATTERNS:
        if pattern in selector:
            return SelectorCapability.FALLBACK

    # Chained >> selectors
    if " >> " in selector:
        parts = selector.split(" >> ")
        # If any part contains fallback pattern, whole chain requires fallback
        for part in parts:
            for pattern in _FALLBACK_PATTERNS:
                if pattern in part:
                    return SelectorCapability.FALLBACK
        return SelectorCapability.SUPPORTED

    # Simple prefixes
    if any(selector.startswith(prefix) for prefix in _SUPPORTED_PREFIXES):
        return SelectorCapability.SUPPORTED

    # Standard CSS selector
    return SelectorCapability.SUPPORTED


def route_locator_action(
    action_name: str,
    target: Any,
    selector: str,
    native_method: Callable[..., Any],
    human_method: Callable[..., Any],
    cfg: Optional[HumanConfig],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Route locator action through humanized or native path based on capability and config.

    Behavior:
    - If selector is SUPPORTED: attempts isolated-world humanized execution.
    - If selector fails isolated evaluation or is FALLBACK:
      - If strict_stealth is True: raises UnsupportedHumanizeSelectorError.
      - If strict_stealth is False: logs structured warning and executes native_method.
    - Never silently switch execution modes without structured logging.
    """
    strict_stealth = getattr(cfg, "strict_stealth", False) if cfg else False
    fallback_to_native = getattr(cfg, "fallback_to_native", True) if cfg else True

    capability = classify_selector(selector)

    if capability == SelectorCapability.FALLBACK:
        if strict_stealth:
            raise UnsupportedHumanizeSelectorError(selector)
        if fallback_to_native:
            logger.warning(
                "[human] selector unsupported in isolated world\n"
                "[human] action=%s\n"
                "[human] selector=%s\n"
                "[human] fallback=native",
                action_name,
                selector,
            )
            # Strip humanize-specific kwargs before native call
            clean_kwargs = {k: v for k, v in kwargs.items() if k != "human_config"}
            return native_method(*args, **clean_kwargs)

    try:
        return human_method(*args, **kwargs)
    except UnsupportedHumanizeSelectorError:
        if strict_stealth:
            raise
        if fallback_to_native:
            logger.warning(
                "[human] selector unsupported in isolated world\n"
                "[human] action=%s\n"
                "[human] selector=%s\n"
                "[human] fallback=native",
                action_name,
                selector,
            )
            clean_kwargs = {k: v for k, v in kwargs.items() if k != "human_config"}
            return native_method(*args, **clean_kwargs)
        raise


async def async_route_locator_action(
    action_name: str,
    target: Any,
    selector: str,
    native_method: Callable[..., Any],
    human_method: Callable[..., Any],
    cfg: Optional[HumanConfig],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Async variant of route_locator_action."""
    strict_stealth = getattr(cfg, "strict_stealth", False) if cfg else False
    fallback_to_native = getattr(cfg, "fallback_to_native", True) if cfg else True

    capability = classify_selector(selector)

    if capability == SelectorCapability.FALLBACK:
        if strict_stealth:
            raise UnsupportedHumanizeSelectorError(selector)
        if fallback_to_native:
            logger.warning(
                "[human] selector unsupported in isolated world\n"
                "[human] action=%s\n"
                "[human] selector=%s\n"
                "[human] fallback=native",
                action_name,
                selector,
            )
            clean_kwargs = {k: v for k, v in kwargs.items() if k != "human_config"}
            return await native_method(*args, **clean_kwargs)

    try:
        return await human_method(*args, **kwargs)
    except UnsupportedHumanizeSelectorError:
        if strict_stealth:
            raise
        if fallback_to_native:
            logger.warning(
                "[human] selector unsupported in isolated world\n"
                "[human] action=%s\n"
                "[human] selector=%s\n"
                "[human] fallback=native",
                action_name,
                selector,
            )
            clean_kwargs = {k: v for k, v in kwargs.items() if k != "human_config"}
            return await native_method(*args, **clean_kwargs)
        raise

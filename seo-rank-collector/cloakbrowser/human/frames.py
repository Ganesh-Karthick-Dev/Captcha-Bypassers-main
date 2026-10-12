"""IsolatedFrame abstraction for frame-aware DOM querying and coordinate translation.

Supports main frame, child iframes, and nested iframes with recursive viewport
offset calculation for accurate humanized mouse actions.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .isolated_world import IsolatedWorldManager, AsyncIsolatedWorldManager

logger = logging.getLogger("cloakbrowser.human.frames")


class IsolatedFrame:
    """Represents a frame (main or iframe) with its own isolated execution context (sync)."""

    __slots__ = (
        "frame_id",
        "parent_frame_id",
        "_manager",
        "_playwright_frame",
    )

    def __init__(
        self,
        frame_id: str,
        manager: "IsolatedWorldManager",
        parent_frame_id: Optional[str] = None,
        playwright_frame: Any = None,
    ):
        self.frame_id = frame_id
        self.parent_frame_id = parent_frame_id
        self._manager = manager
        self._playwright_frame = playwright_frame

    @property
    def execution_context_id(self) -> int:
        return self._manager.ensure_context(self.frame_id)

    def evaluate(self, expression: str) -> Any:
        """Evaluate JS inside this frame's isolated world."""
        return self._manager.evaluate(expression, frame_id=self.frame_id)

    def query(self, selector: str) -> Any:
        """Query element snapshot in this frame's isolated world."""
        from .stealth_dom import build_snapshot_js, parse_result
        js = build_snapshot_js(selector)
        raw = self.evaluate(js)
        return parse_result(raw)

    def get_viewport_offset(self) -> Tuple[float, float]:
        """Calculate the (x, y) offset of this frame relative to the top-level viewport.

        For main frame, returns (0.0, 0.0).
        For child and nested iframes, calculates recursive bounding offsets.
        """
        if self.parent_frame_id is None:
            return (0.0, 0.0)

        # 1. If Playwright frame is attached, frame_element().bounding_box()
        # computes viewport coordinates directly taking nested frames into account.
        if self._playwright_frame is not None:
            try:
                elem = self._playwright_frame.frame_element()
                if elem:
                    box = elem.bounding_box()
                    if box:
                        return (float(box["x"]), float(box["y"]))
            except Exception as e:
                logger.debug("Playwright frame_element bounding_box failed: %s", e)

        # 2. CDP evaluation fallback: calculate recursive frameElement offsets
        js_offset = """
        (() => {
            let cur = window;
            let totalX = 0;
            let totalY = 0;
            while (cur && cur !== cur.parent) {
                try {
                    const el = cur.frameElement;
                    if (!el) break;
                    const rect = el.getBoundingClientRect();
                    totalX += rect.left;
                    totalY += rect.top;
                    cur = cur.parent;
                } catch (e) {
                    break;
                }
            }
            return [totalX, totalY];
        })()
        """
        try:
            res = self.evaluate(js_offset)
            if isinstance(res, (list, tuple)) and len(res) == 2:
                return (float(res[0]), float(res[1]))
        except Exception as e:
            logger.debug("JS recursive frameElement offset failed: %s", e)

        return (0.0, 0.0)


class AsyncIsolatedFrame:
    """Represents a frame with its own isolated execution context (async)."""

    __slots__ = (
        "frame_id",
        "parent_frame_id",
        "_manager",
        "_playwright_frame",
    )

    def __init__(
        self,
        frame_id: str,
        manager: "AsyncIsolatedWorldManager",
        parent_frame_id: Optional[str] = None,
        playwright_frame: Any = None,
    ):
        self.frame_id = frame_id
        self.parent_frame_id = parent_frame_id
        self._manager = manager
        self._playwright_frame = playwright_frame

    async def get_execution_context_id(self) -> int:
        return await self._manager.ensure_context(self.frame_id)

    async def evaluate(self, expression: str) -> Any:
        return await self._manager.evaluate(expression, frame_id=self.frame_id)

    async def query(self, selector: str) -> Any:
        from .stealth_dom import build_snapshot_js, parse_result
        js = build_snapshot_js(selector)
        raw = await self.evaluate(js)
        return parse_result(raw)

    async def get_viewport_offset(self) -> Tuple[float, float]:
        if self.parent_frame_id is None:
            return (0.0, 0.0)

        if self._playwright_frame is not None:
            try:
                elem = await self._playwright_frame.frame_element()
                if elem:
                    box = await elem.bounding_box()
                    if box:
                        return (float(box["x"]), float(box["y"]))
            except Exception as e:
                logger.debug("Async frame_element bounding_box failed: %s", e)

        js_offset = """
        (() => {
            let cur = window;
            let totalX = 0;
            let totalY = 0;
            while (cur && cur !== cur.parent) {
                try {
                    const el = cur.frameElement;
                    if (!el) break;
                    const rect = el.getBoundingClientRect();
                    totalX += rect.left;
                    totalY += rect.top;
                    cur = cur.parent;
                } catch (e) {
                    break;
                }
            }
            return [totalX, totalY];
        })()
        """
        try:
            res = await self.evaluate(js_offset)
            if isinstance(res, (list, tuple)) and len(res) == 2:
                return (float(res[0]), float(res[1]))
        except Exception:
            pass

        return (0.0, 0.0)

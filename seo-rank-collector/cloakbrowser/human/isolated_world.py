"""Frame-aware CDP isolated execution context manager.

Maintains a registry of frame_id -> execution_context_id for main frame,
child iframes, and nested iframes. Handles CDP lifecycle events (frameAttached,
frameNavigated, frameDetached, executionContextDestroyed) to automatically
invalidate and recreate execution contexts without stale context references.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Set, Tuple

logger = logging.getLogger("cloakbrowser.human.isolated_world")


class IsolatedWorldManager:
    """Manages CDP isolated execution contexts across all frames in a page (sync)."""

    __slots__ = (
        "_page",
        "_cdp",
        "_contexts",          # frame_id -> execution_context_id
        "_frame_parents",     # frame_id -> parent_frame_id
        "_main_frame_id",     # str
        "_cdp_initialized",   # bool
    )

    def __init__(self, page: Any):
        self._page = page
        self._cdp: Any = None
        self._contexts: Dict[str, int] = {}
        self._frame_parents: Dict[str, Optional[str]] = {}
        self._main_frame_id: Optional[str] = None
        self._cdp_initialized: bool = False

    def _ensure_cdp(self) -> Any:
        if self._cdp is None:
            self._cdp = self._page.context.new_cdp_session(self._page)
            self._setup_cdp_listeners()
        return self._cdp

    def _setup_cdp_listeners(self) -> None:
        if self._cdp_initialized:
            return
        self._cdp_initialized = True
        try:
            self._cdp.send("Page.enable")
        except Exception as e:
            logger.debug("Page.enable failed or already enabled: %s", e)

        # Wire lifecycle listeners
        try:
            self._cdp.on("Page.frameNavigated", self._on_frame_navigated)
            self._cdp.on("Page.frameDetached", self._on_frame_detached)
            self._cdp.on("Page.frameAttached", self._on_frame_attached)
            self._cdp.on("Runtime.executionContextDestroyed", self._on_context_destroyed)
        except Exception as e:
            logger.debug("CDP event listener registration failed: %s", e)

    def _on_frame_navigated(self, event: Dict[str, Any]) -> None:
        frame_info = event.get("frame", {})
        frame_id = frame_info.get("id")
        parent_id = frame_info.get("parentId")
        if frame_id:
            logger.debug("CDP Page.frameNavigated: frame_id=%s", frame_id)
            if parent_id is None:
                self._main_frame_id = frame_id
                self._contexts.clear()
            else:
                self._invalidate_frame_and_descendants(frame_id)
            self._frame_parents[frame_id] = parent_id

    def _on_frame_detached(self, event: Dict[str, Any]) -> None:
        frame_id = event.get("frameId")
        if frame_id:
            logger.debug("CDP Page.frameDetached: frame_id=%s", frame_id)
            self._invalidate_frame_and_descendants(frame_id)
            self._frame_parents.pop(frame_id, None)

    def _on_frame_attached(self, event: Dict[str, Any]) -> None:
        frame_id = event.get("frameId")
        parent_id = event.get("parentFrameId")
        if frame_id:
            logger.debug("CDP Page.frameAttached: frame_id=%s, parent_id=%s", frame_id, parent_id)
            self._frame_parents[frame_id] = parent_id

    def _on_context_destroyed(self, event: Dict[str, Any]) -> None:
        ctx_id = event.get("executionContextId")
        if ctx_id:
            for fid, cid in list(self._contexts.items()):
                if cid == ctx_id:
                    self._contexts.pop(fid, None)
                    logger.debug("CDP Runtime.executionContextDestroyed: cleared frame_id=%s (ctx=%d)", fid, ctx_id)

    def _invalidate_frame_and_descendants(self, frame_id: str) -> None:
        to_remove: Set[str] = {frame_id}
        for fid, pid in self._frame_parents.items():
            if pid in to_remove:
                to_remove.add(fid)
        for fid in to_remove:
            self._contexts.pop(fid, None)

    def _refresh_frame_tree(self) -> None:
        cdp = self._ensure_cdp()
        try:
            tree_data = cdp.send("Page.getFrameTree")
            frame_tree = tree_data.get("frameTree", {})
            self._walk_frame_tree(frame_tree, parent_id=None)
        except Exception as e:
            logger.debug("Page.getFrameTree failed: %s", e)

    def _walk_frame_tree(self, node: Dict[str, Any], parent_id: Optional[str] = None) -> None:
        frame = node.get("frame", {})
        fid = frame.get("id")
        if fid:
            self._frame_parents[fid] = parent_id
            if parent_id is None:
                self._main_frame_id = fid
            for child in node.get("childFrames", []):
                self._walk_frame_tree(child, parent_id=fid)

    def get_main_frame_id(self) -> str:
        if self._main_frame_id is None:
            self._refresh_frame_tree()
        if self._main_frame_id is None:
            raise RuntimeError("Could not determine main frame ID from CDP")
        return self._main_frame_id

    def ensure_context(self, frame_id: Optional[str] = None) -> int:
        """Get or create the isolated world execution context ID for a frame."""
        cdp = self._ensure_cdp()
        if frame_id is None:
            frame_id = self.get_main_frame_id()

        if frame_id in self._contexts:
            return self._contexts[frame_id]

        if frame_id not in self._frame_parents:
            self._refresh_frame_tree()

        res = cdp.send("Page.createIsolatedWorld", {
            "frameId": frame_id,
            "worldName": "",
            "grantUniveralAccess": True,
        })
        ctx_id = res["executionContextId"]
        self._contexts[frame_id] = ctx_id
        logger.debug("Created isolated world for frame_id=%s -> ctx_id=%d", frame_id, ctx_id)
        return ctx_id

    def evaluate(self, expression: str, frame_id: Optional[str] = None) -> Any:
        """Evaluate JS in the isolated world of the specified frame."""
        cdp = self._ensure_cdp()
        target_fid = frame_id if frame_id is not None else self.get_main_frame_id()

        for attempt in range(2):
            ctx_id = self.ensure_context(target_fid)
            try:
                result = cdp.send("Runtime.evaluate", {
                    "expression": expression,
                    "contextId": ctx_id,
                    "returnByValue": True,
                })
                if "exceptionDetails" in result:
                    if attempt == 0:
                        self.invalidate(target_fid)
                        continue
                    return None
                return result.get("result", {}).get("value")
            except Exception as e:
                logger.debug("evaluate failed in frame_id=%s (attempt %d): %s", target_fid, attempt, e)
                if attempt == 0:
                    self.invalidate(target_fid)
                    continue
                return None
        return None

    def invalidate(self, frame_id: Optional[str] = None) -> None:
        """Mark context(s) as stale."""
        if frame_id is None:
            self._contexts.clear()
        else:
            self._invalidate_frame_and_descendants(frame_id)

    def get_cdp_session(self) -> Any:
        return self._ensure_cdp()


class AsyncIsolatedWorldManager:
    """Manages CDP isolated execution contexts across all frames in a page (async)."""

    __slots__ = (
        "_page",
        "_cdp",
        "_contexts",
        "_frame_parents",
        "_main_frame_id",
        "_cdp_initialized",
    )

    def __init__(self, page: Any):
        self._page = page
        self._cdp: Any = None
        self._contexts: Dict[str, int] = {}
        self._frame_parents: Dict[str, Optional[str]] = {}
        self._main_frame_id: Optional[str] = None
        self._cdp_initialized: bool = False

    async def _ensure_cdp(self) -> Any:
        if self._cdp is None:
            self._cdp = await self._page.context.new_cdp_session(self._page)
            await self._setup_cdp_listeners()
        return self._cdp

    async def _setup_cdp_listeners(self) -> None:
        if self._cdp_initialized:
            return
        self._cdp_initialized = True
        try:
            await self._cdp.send("Page.enable")
        except Exception as e:
            logger.debug("Page.enable failed or already enabled: %s", e)

        try:
            self._cdp.on("Page.frameNavigated", self._on_frame_navigated)
            self._cdp.on("Page.frameDetached", self._on_frame_detached)
            self._cdp.on("Page.frameAttached", self._on_frame_attached)
            self._cdp.on("Runtime.executionContextDestroyed", self._on_context_destroyed)
        except Exception as e:
            logger.debug("CDP event listener registration failed: %s", e)

    def _on_frame_navigated(self, event: Dict[str, Any]) -> None:
        frame_info = event.get("frame", {})
        frame_id = frame_info.get("id")
        parent_id = frame_info.get("parentId")
        if frame_id:
            if parent_id is None:
                self._main_frame_id = frame_id
                self._contexts.clear()
            else:
                self._invalidate_frame_and_descendants(frame_id)
            self._frame_parents[frame_id] = parent_id

    def _on_frame_detached(self, event: Dict[str, Any]) -> None:
        frame_id = event.get("frameId")
        if frame_id:
            self._invalidate_frame_and_descendants(frame_id)
            self._frame_parents.pop(frame_id, None)

    def _on_frame_attached(self, event: Dict[str, Any]) -> None:
        frame_id = event.get("frameId")
        parent_id = event.get("parentFrameId")
        if frame_id:
            self._frame_parents[frame_id] = parent_id

    def _on_context_destroyed(self, event: Dict[str, Any]) -> None:
        ctx_id = event.get("executionContextId")
        if ctx_id:
            for fid, cid in list(self._contexts.items()):
                if cid == ctx_id:
                    self._contexts.pop(fid, None)

    def _invalidate_frame_and_descendants(self, frame_id: str) -> None:
        to_remove: Set[str] = {frame_id}
        for fid, pid in self._frame_parents.items():
            if pid in to_remove:
                to_remove.add(fid)
        for fid in to_remove:
            self._contexts.pop(fid, None)

    async def _refresh_frame_tree(self) -> None:
        cdp = await self._ensure_cdp()
        try:
            tree_data = await cdp.send("Page.getFrameTree")
            frame_tree = tree_data.get("frameTree", {})
            self._walk_frame_tree(frame_tree, parent_id=None)
        except Exception as e:
            logger.debug("Page.getFrameTree failed: %s", e)

    def _walk_frame_tree(self, node: Dict[str, Any], parent_id: Optional[str] = None) -> None:
        frame = node.get("frame", {})
        fid = frame.get("id")
        if fid:
            self._frame_parents[fid] = parent_id
            if parent_id is None:
                self._main_frame_id = fid
            for child in node.get("childFrames", []):
                self._walk_frame_tree(child, parent_id=fid)

    async def get_main_frame_id(self) -> str:
        if self._main_frame_id is None:
            await self._refresh_frame_tree()
        if self._main_frame_id is None:
            raise RuntimeError("Could not determine main frame ID from CDP")
        return self._main_frame_id

    async def ensure_context(self, frame_id: Optional[str] = None) -> int:
        cdp = await self._ensure_cdp()
        if frame_id is None:
            frame_id = await self.get_main_frame_id()

        if frame_id in self._contexts:
            return self._contexts[frame_id]

        if frame_id not in self._frame_parents:
            await self._refresh_frame_tree()

        res = await cdp.send("Page.createIsolatedWorld", {
            "frameId": frame_id,
            "worldName": "",
            "grantUniveralAccess": True,
        })
        ctx_id = res["executionContextId"]
        self._contexts[frame_id] = ctx_id
        return ctx_id

    async def evaluate(self, expression: str, frame_id: Optional[str] = None) -> Any:
        cdp = await self._ensure_cdp()
        target_fid = frame_id if frame_id is not None else await self.get_main_frame_id()

        for attempt in range(2):
            ctx_id = await self.ensure_context(target_fid)
            try:
                result = await cdp.send("Runtime.evaluate", {
                    "expression": expression,
                    "contextId": ctx_id,
                    "returnByValue": True,
                })
                if "exceptionDetails" in result:
                    if attempt == 0:
                        self.invalidate(target_fid)
                        continue
                    return None
                return result.get("result", {}).get("value")
            except Exception as e:
                logger.debug("async evaluate failed in frame_id=%s (attempt %d): %s", target_fid, attempt, e)
                if attempt == 0:
                    self.invalidate(target_fid)
                    continue
                return None
        return None

    def invalidate(self, frame_id: Optional[str] = None) -> None:
        if frame_id is None:
            self._contexts.clear()
        else:
            self._invalidate_frame_and_descendants(frame_id)

    async def get_cdp_session(self) -> Any:
        return await self._ensure_cdp()

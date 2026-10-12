"""SEO web performance signals: TTFB, DOMContentLoaded, and page load timing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class PerformanceAuditResult:
    ttfb_ms: Optional[float] = None
    dom_content_loaded_ms: Optional[float] = None
    page_load_ms: Optional[float] = None
    transfer_size_bytes: Optional[int] = None
    raw_timing: Dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class PerformanceExtractor:
    """Extracts Core Web Vitals / Navigation Timing API performance indicators."""

    @staticmethod
    def extract(page: Any) -> PerformanceAuditResult:
        js = """
        () => {
            const nav = window.performance && window.performance.getEntriesByType ?
                window.performance.getEntriesByType('navigation')[0] : null;

            if (nav) {
                return {
                    ttfb: nav.responseStart - nav.requestStart,
                    dom_content_loaded: nav.domContentLoadedEventEnd - nav.startTime,
                    load_event: nav.loadEventEnd - nav.startTime,
                    transfer_size: nav.transferSize || 0
                };
            }

            const timing = window.performance ? window.performance.timing : null;
            if (timing && timing.navigationStart) {
                return {
                    ttfb: Math.max(0, timing.responseStart - timing.requestStart),
                    dom_content_loaded: Math.max(0, timing.domContentLoadedEventEnd - timing.navigationStart),
                    load_event: Math.max(0, timing.loadEventEnd - timing.navigationStart),
                    transfer_size: 0
                };
            }

            return null;
        }
        """
        raw = page.evaluate(js) if hasattr(page, "evaluate") else None
        if not raw:
            return PerformanceAuditResult(warnings=["Navigation Timing API unavailable"])

        ttfb = raw.get("ttfb")
        dcl = raw.get("dom_content_loaded")
        load = raw.get("load_event")
        transfer = raw.get("transfer_size")
        warnings: list[str] = []

        if ttfb is not None and ttfb > 800:
            warnings.append(f"High Time To First Byte (TTFB): {ttfb:.1f}ms (target < 800ms)")
        if load is not None and load > 4000:
            warnings.append(f"Slow Page Load Time: {load:.1f}ms (target < 4000ms)")

        return PerformanceAuditResult(
            ttfb_ms=ttfb,
            dom_content_loaded_ms=dcl,
            page_load_ms=load,
            transfer_size_bytes=transfer,
            raw_timing=raw,
            warnings=warnings,
        )

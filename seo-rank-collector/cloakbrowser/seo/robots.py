"""SEO robots meta directives extractor and audit."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class RobotsAuditResult:
    directives: List[str] = field(default_factory=list)
    has_noindex: bool = False
    has_nofollow: bool = False
    raw_content: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


class RobotsExtractor:
    """Audits robots meta tags on page."""

    @staticmethod
    def extract(page: Any) -> RobotsAuditResult:
        js = """
        () => {
            const el = document.querySelector('meta[name="robots" i], meta[name="googlebot" i]');
            return el ? el.getAttribute('content') : null;
        }
        """
        raw = page.evaluate(js) if hasattr(page, "evaluate") else None
        directives: List[str] = []
        warnings: List[str] = []

        if raw:
            directives = [d.strip().lower() for d in raw.split(",") if d.strip()]

        has_noindex = "noindex" in directives
        has_nofollow = "nofollow" in directives

        if has_noindex:
            warnings.append("Page contains 'noindex' directive: search engines will not index this page")
        if has_nofollow:
            warnings.append("Page contains 'nofollow' directive: outbound links will not be crawled")

        return RobotsAuditResult(
            directives=directives,
            has_noindex=has_noindex,
            has_nofollow=has_nofollow,
            raw_content=raw,
            warnings=warnings,
        )

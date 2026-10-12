"""SEO canonical URL extractor and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class CanonicalAuditResult:
    canonical_url: Optional[str] = None
    all_canonicals: List[str] = field(default_factory=list)
    is_self_referential: bool = False
    warnings: List[str] = field(default_factory=list)


class CanonicalExtractor:
    """Audits document canonical link tags."""

    @staticmethod
    def extract(page: Any) -> CanonicalAuditResult:
        page_url = page.url if hasattr(page, "url") else ""
        js = """
        () => {
            const canonicals = [];
            document.querySelectorAll('link[rel="canonical"]').forEach(el => {
                const href = el.getAttribute('href');
                if (href) canonicals.push(href);
            });
            return canonicals;
        }
        """
        raw = page.evaluate(js) if hasattr(page, "evaluate") else []
        warnings: List[str] = []

        if not raw:
            warnings.append("Missing canonical link tag (<link rel='canonical'>)")
        elif len(raw) > 1:
            warnings.append(f"Multiple canonical link tags detected ({len(raw)})")

        primary = raw[0] if raw else None
        is_self = False
        if primary and page_url:
            # Strip trailing slash comparison
            is_self = primary.rstrip("/") == page_url.rstrip("/")

        return CanonicalAuditResult(
            canonical_url=primary,
            all_canonicals=raw,
            is_self_referential=is_self,
            warnings=warnings,
        )

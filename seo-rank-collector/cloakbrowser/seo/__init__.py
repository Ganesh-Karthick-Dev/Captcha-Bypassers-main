"""Modular SEO audit engine package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from .canonical import CanonicalAuditResult, CanonicalExtractor
from .headings import HeadingsAuditResult, HeadingsExtractor
from .links import LinksAuditResult, LinksExtractor
from .metadata import MetadataAuditResult, MetadataExtractor
from .performance import PerformanceAuditResult, PerformanceExtractor
from .robots import RobotsAuditResult, RobotsExtractor
from .structured_data import StructuredDataAuditResult, StructuredDataExtractor


@dataclass
class CompleteSEOAudit:
    url: str
    metadata: MetadataAuditResult
    headings: HeadingsAuditResult
    links: LinksAuditResult
    structured_data: StructuredDataAuditResult
    canonical: CanonicalAuditResult
    robots: RobotsAuditResult
    performance: PerformanceAuditResult
    all_warnings: list[str] = field(default_factory=list)


class SEOAuditor:
    """Orchestrates comprehensive modular SEO audits on a live page."""

    @staticmethod
    def audit_page(page: Any) -> CompleteSEOAudit:
        url = getattr(page, "url", "")
        meta = MetadataExtractor.extract(page)
        headings = HeadingsExtractor.extract(page)
        links = LinksExtractor.extract(page)
        structured = StructuredDataExtractor.extract(page)
        canonical = CanonicalExtractor.extract(page)
        robots = RobotsExtractor.extract(page)
        perf = PerformanceExtractor.extract(page)

        all_warnings = (
            meta.warnings
            + headings.warnings
            + [w for l in links.internal_links + links.external_links for w in l.warnings]
            + canonical.warnings
            + robots.warnings
            + perf.warnings
        )

        return CompleteSEOAudit(
            url=url,
            metadata=meta,
            headings=headings,
            links=links,
            structured_data=structured,
            canonical=canonical,
            robots=robots,
            performance=perf,
            all_warnings=all_warnings,
        )


__all__ = [
    "MetadataExtractor",
    "MetadataAuditResult",
    "HeadingsExtractor",
    "HeadingsAuditResult",
    "LinksExtractor",
    "LinksAuditResult",
    "StructuredDataExtractor",
    "StructuredDataAuditResult",
    "CanonicalExtractor",
    "CanonicalAuditResult",
    "RobotsExtractor",
    "RobotsAuditResult",
    "PerformanceExtractor",
    "PerformanceAuditResult",
    "SEOAuditor",
    "CompleteSEOAudit",
]

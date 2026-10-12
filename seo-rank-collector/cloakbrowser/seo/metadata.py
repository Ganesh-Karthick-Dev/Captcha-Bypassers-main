"""SEO metadata extraction: title, meta description, OpenGraph, and Twitter tags."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class MetadataAuditResult:
    title: Optional[str] = None
    description: Optional[str] = None
    viewport: Optional[str] = None
    charset: Optional[str] = None
    open_graph: Dict[str, str] = field(default_factory=dict)
    twitter_card: Dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class MetadataExtractor:
    """Extracts and validates core HTML document metadata for SEO."""

    @staticmethod
    def extract(page: Any) -> MetadataAuditResult:
        js = """
        () => {
            const getMeta = (name) => {
                const el = document.querySelector(`meta[name="${name}" i], meta[property="${name}" i]`);
                return el ? el.getAttribute('content') : null;
            };

            const og = {};
            const twitter = {};
            document.querySelectorAll('meta[property^="og:"]').forEach(el => {
                const prop = el.getAttribute('property');
                const content = el.getAttribute('content');
                if (prop && content) og[prop] = content;
            });
            document.querySelectorAll('meta[name^="twitter:"], meta[property^="twitter:"]').forEach(el => {
                const name = el.getAttribute('name') || el.getAttribute('property');
                const content = el.getAttribute('content');
                if (name && content) twitter[name] = content;
            });

            const charsetMeta = document.querySelector('meta[charset]');
            return {
                title: document.title || null,
                description: getMeta('description'),
                viewport: getMeta('viewport'),
                charset: charsetMeta ? charsetMeta.getAttribute('charset') : null,
                open_graph: og,
                twitter_card: twitter
            };
        }
        """
        raw = page.evaluate(js) if hasattr(page, "evaluate") else {}
        warnings: list[str] = []

        title = raw.get("title")
        desc = raw.get("description")

        if not title:
            warnings.append("Missing <title> tag")
        elif len(title) < 10 or len(title) > 70:
            warnings.append(f"Title length ({len(title)}) outside recommended 10-70 characters")

        if not desc:
            warnings.append("Missing meta description")
        elif len(desc) < 50 or len(desc) > 160:
            warnings.append(f"Description length ({len(desc)}) outside recommended 50-160 characters")

        return MetadataAuditResult(
            title=title,
            description=desc,
            viewport=raw.get("viewport"),
            charset=raw.get("charset"),
            open_graph=raw.get("open_graph", {}),
            twitter_card=raw.get("twitter_card", {}),
            warnings=warnings,
        )

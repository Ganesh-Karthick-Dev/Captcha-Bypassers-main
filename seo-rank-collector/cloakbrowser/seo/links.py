"""SEO links extractor: internal vs external links, anchor text, and rel attributes."""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Any, List


@dataclass
class LinkItem:
    href: str
    text: str
    is_internal: bool
    rel: str
    warnings: List[str] = field(default_factory=list)


@dataclass
class LinksAuditResult:
    internal_links: List[LinkItem] = field(default_factory=list)
    external_links: List[LinkItem] = field(default_factory=list)
    total_links: int = 0
    empty_anchors_count: int = 0


class LinksExtractor:
    """Extracts and audits document hyperlinks."""

    @staticmethod
    def extract(page: Any) -> LinksAuditResult:
        base_url = page.url if hasattr(page, "url") else "http://localhost"
        base_netloc = urllib.parse.urlparse(base_url).netloc.lower()

        js = """
        () => {
            const links = [];
            document.querySelectorAll('a[href]').forEach(el => {
                links.push({
                    href: el.getAttribute('href'),
                    text: el.innerText.trim(),
                    rel: el.getAttribute('rel') || ''
                });
            });
            return links;
        }
        """
        raw_links = page.evaluate(js) if hasattr(page, "evaluate") else []
        if not isinstance(raw_links, list):
            raw_links = []
        internal: List[LinkItem] = []
        external: List[LinkItem] = []
        empty_anchors = 0

        for l in raw_links:
            if not isinstance(l, dict):
                continue
            href = l.get("href") or ""
            text = l.get("text") or ""
            rel = l.get("rel") or ""

            if not text:
                empty_anchors += 1

            parsed = urllib.parse.urlparse(href)
            is_internal = not parsed.netloc or parsed.netloc.lower() == base_netloc

            item = LinkItem(
                href=href,
                text=text,
                is_internal=is_internal,
                rel=rel,
                warnings=["Missing anchor text"] if not text else [],
            )

            if is_internal:
                internal.append(item)
            else:
                external.append(item)

        return LinksAuditResult(
            internal_links=internal,
            external_links=external,
            total_links=len(raw_links),
            empty_anchors_count=empty_anchors,
        )

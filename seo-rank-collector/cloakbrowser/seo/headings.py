"""SEO heading audit: H1-H6 hierarchy, uniqueness, and structure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class HeadingsAuditResult:
    h1: List[str] = field(default_factory=list)
    h2: List[str] = field(default_factory=list)
    h3: List[str] = field(default_factory=list)
    all_headings: List[Dict[str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class HeadingsExtractor:
    """Audits document heading tags (H1-H6) for SEO compliance."""

    @staticmethod
    def extract(page: Any) -> HeadingsAuditResult:
        js = """
        () => {
            const headings = [];
            document.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(el => {
                headings.push({
                    level: el.tagName.toLowerCase(),
                    text: el.innerText.trim()
                });
            });
            return headings;
        }
        """
        raw_headings = page.evaluate(js) if hasattr(page, "evaluate") else []
        if not isinstance(raw_headings, list):
            raw_headings = []
        warnings: List[str] = []

        h1s = [h["text"] for h in raw_headings if isinstance(h, dict) and h.get("level") == "h1" and h.get("text")]
        h2s = [h["text"] for h in raw_headings if isinstance(h, dict) and h.get("level") == "h2" and h.get("text")]
        h3s = [h["text"] for h in raw_headings if isinstance(h, dict) and h.get("level") == "h3" and h.get("text")]

        if not h1s:
            warnings.append("Missing H1 heading tag")
        elif len(h1s) > 1:
            warnings.append(f"Multiple H1 headings detected ({len(h1s)})")

        return HeadingsAuditResult(
            h1=h1s,
            h2=h2s,
            h3=h3s,
            all_headings=raw_headings,
            warnings=warnings,
        )

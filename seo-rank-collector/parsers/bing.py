"""Bing SERP Parser implementation for SEO Rank Collector."""
import base64
import urllib.parse
from typing import List
from bs4 import BeautifulSoup

from rank_models import SERPItem, ResultType
from normalizer import URLNormalizer
from parsers.base import BaseSERPParser


class BingSERPParser(BaseSERPParser):
    """Parses Bing Search Result Page (SERP) HTML into structured SERPItem instances."""

    @staticmethod
    def _unwrap_bing_url(raw_url: str) -> str:
        """Unwraps Bing's /ck/a click tracking URL: u=a1<base64_encoded_url>."""
        try:
            if "bing.com/ck/a" in raw_url:
                parsed = urllib.parse.urlparse(raw_url)
                qs = urllib.parse.parse_qs(parsed.query)
                if "u" in qs:
                    u_val = qs["u"][0]
                    if u_val.startswith("a1"):
                        b64 = u_val[2:]
                        b64 += "=" * ((4 - len(b64) % 4) % 4)
                        decoded = base64.urlsafe_b64decode(b64).decode("utf-8", errors="ignore")
                        if decoded.startswith("http://") or decoded.startswith("https://"):
                            return decoded
        except Exception:
            pass
        return raw_url

    def parse(self, html: str, page_number: int = 1) -> List[SERPItem]:
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        items: List[SERPItem] = []

        page_organic_offset = (page_number - 1) * 10
        organic_counter = 0
        absolute_counter = 0

        blocks = soup.select("li.b_algo")
        seen_urls = set()

        for block in blocks:
            link_tag = block.select_one("h2 a[href]")
            if not link_tag:
                continue

            raw_href = link_tag.get("href", "").strip()
            if not raw_href or raw_href.startswith("#") or "javascript:" in raw_href:
                continue

            target_url = self._unwrap_bing_url(raw_href)
            if not target_url or "bing.com/" in target_url:
                continue

            canonical_url, domain = URLNormalizer.normalize(target_url)
            if not canonical_url or canonical_url in seen_urls:
                continue

            seen_urls.add(canonical_url)

            # Extract Title
            title = link_tag.get_text(strip=True)
            if not title:
                title = domain

            # Extract Snippet
            snippet_tag = block.select_one("div.b_caption p, p.b_lineclamp2, div.b_snippet, p")
            snippet = snippet_tag.get_text(separator=" ", strip=True) if snippet_tag else ""

            absolute_counter += 1
            organic_counter += 1
            current_organic_pos = page_organic_offset + organic_counter

            items.append(
                SERPItem(
                    absolute_position=absolute_counter,
                    organic_position=current_organic_pos,
                    title=title,
                    url=target_url,
                    canonical_url=canonical_url,
                    domain=domain,
                    snippet=snippet,
                    result_type=ResultType.ORGANIC,
                    serp_page=page_number,
                )
            )

        return items

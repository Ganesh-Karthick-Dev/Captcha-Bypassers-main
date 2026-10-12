"""Google SERP Parser implementation for SEO Rank Collector."""
import re
import urllib.parse
from typing import List
from bs4 import BeautifulSoup

from rank_models import SERPItem, ResultType, MapPackItem
from normalizer import URLNormalizer
from parsers.base import BaseSERPParser


class GoogleSERPParser(BaseSERPParser):
    """Parses Google Search Result Page (SERP) HTML into structured SERPItem instances."""

    @classmethod
    def _resolve_goto_url(cls, target_url: str) -> str:
        if not target_url or "/goto?" not in target_url:
            return target_url
        if target_url.startswith("/"):
            target_url = "https://www.google.com" + target_url
        try:
            import curl_cffi.requests as requests
            r = requests.get(target_url, allow_redirects=False, timeout=3.5, impersonate="chrome120")
            loc = r.headers.get("Location") or r.headers.get("location")
            if loc and loc.startswith("http"):
                return loc
        except Exception:
            pass
        return target_url

    def detect_features(self, html: str) -> dict:
        """Detects presence of page-level SERP features like AI Overview and Featured Snippets."""
        if not html:
            return {"ai_overview_present": False, "featured_snippet_present": False}
        soup = BeautifulSoup(html, "html.parser")

        # AI Overview / SGE summary containers
        ai_overview = bool(
            soup.select(
                "div[data-attrid='sge_summary'], div[jsname='xQjX4c'], "
                "div[aria-label*='AI Overview' i], div.MjjYud:has([aria-label*='AI Overview' i])"
            )
        )
        if not ai_overview:
            for heading in soup.select("h1, h2, div[role='heading']"):
                if "ai overview" in heading.get_text(strip=True).lower():
                    ai_overview = True
                    break

        # Featured Snippet containers
        featured_snippet = bool(
            soup.select(
                "div.cUnQKe, div[data-attrid='wa:/description'], div.xpdopen, "
                "div.kp-blk, div.ifM9O, div.LGOjhe"
            )
        )

        return {
            "ai_overview_present": ai_overview,
            "featured_snippet_present": featured_snippet,
        }

    def parse_map_pack(self, html: str) -> List[MapPackItem]:
        """Extracts Google Maps 3-Pack local business results."""
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        map_items: List[MapPackItem] = []

        # Target local pack listing containers
        pack_containers = soup.select("div.VkpGBb, div.cXedhc, div[data-cid], div.u39ukf")
        pos = 1

        for box in pack_containers:
            # Business / Venue Name
            title_tag = box.select_one("div.dbg0pd, span.OSrXXb, div[role='heading'], span.rllt__wrapped")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            if not title or len(title) < 2:
                continue

            # Rating (e.g. "4.8" or "4.8 stars")
            rating_val = None
            rating_tag = box.select_one("span.YwPhnf, span[aria-label*='stars' i]")
            if rating_tag:
                rating_str = rating_tag.get_text(strip=True) or rating_tag.get("aria-label", "")
                m_rate = re.search(r"(\d+(?:\.\d+)?)", rating_str)
                if m_rate:
                    try:
                        rating_val = float(m_rate.group(1))
                    except ValueError:
                        pass

            # Reviews Count (e.g. "(142)" or "142 reviews")
            reviews_val = None
            review_tag = box.select_one("span.RDA7Le, span:-soup-contains('(')")
            if review_tag:
                rev_text = review_tag.get_text(strip=True)
                m_rev = re.search(r"\(([\d,]+)\)", rev_text) or re.search(r"([\d,]+)\s+reviews", rev_text)
                if m_rev:
                    try:
                        reviews_val = int(m_rev.group(1).replace(",", ""))
                    except ValueError:
                        pass

            # Details / Address
            address_val = None
            detail_tag = box.select_one("div.rllt__details > div:nth-child(2), div.rllt__details")
            if detail_tag:
                address_val = detail_tag.get_text(separator=" · ", strip=True)

            # Website link
            web_tag = box.select_one("a[data-website-url], a.yYflre[href^='http'], a:-soup-contains('Website')")
            website_url = None
            web_domain = None
            if web_tag:
                raw_href = web_tag.get("data-website-url") or web_tag.get("href", "")
                if raw_href and raw_href.startswith("http") and "google.com" not in raw_href:
                    can_url, dom = URLNormalizer.normalize(raw_href)
                    website_url = raw_href
                    web_domain = dom

            map_items.append(
                MapPackItem(
                    position=pos,
                    title=title,
                    rating=rating_val,
                    reviews_count=reviews_val,
                    address=address_val,
                    website_url=website_url,
                    domain=web_domain,
                )
            )
            pos += 1
            if pos > 3:  # Only 3-Pack
                break

        return map_items

    def parse(self, html: str, page_number: int = 1) -> List[SERPItem]:
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        items: List[SERPItem] = []

        # Organic positions start at (page_number - 1) * 10 + 1
        page_organic_offset = (page_number - 1) * 10
        organic_counter = 0
        absolute_counter = 0

        # Find search result blocks in modern Google DOM layouts
        blocks = soup.select("div.g, div.MjjYud, div.yuRUbf, div.v7W49e > div")

        seen_urls = set()

        for block in blocks:
            # Check for organic link header
            link_tag = block.select_one("div.yuRUbf > a, a[data-ved][href]:has(h3), a:has(h3)")
            if not link_tag:
                continue

            raw_url = link_tag.get("href", "").strip()

            # 1. Google redirect links: /url?q=https://... or /url?url=https://...
            if raw_url.startswith("/url?") or raw_url.startswith("https://www.google.com/url?"):
                parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query)
                if "url" in parsed_qs and parsed_qs["url"][0].startswith("http"):
                    raw_url = parsed_qs["url"][0]
                elif "q" in parsed_qs and parsed_qs["q"][0].startswith("http"):
                    raw_url = parsed_qs["q"][0]

            # 2. Check ping attribute for destination URL: ping="/url?sa=t&...&url=https://target.com"
            ping_val = link_tag.get("ping", "")
            if "url=" in ping_val:
                parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(ping_val).query)
                if "url" in parsed_qs and parsed_qs["url"][0].startswith("http"):
                    raw_url = parsed_qs["url"][0]

            # Filter non-external search URLs (e.g., google.com services, maps, accounts)
            if not raw_url or raw_url.startswith("#") or "javascript:" in raw_url:
                continue
            if ("google.com/" in raw_url or raw_url.startswith("/")) and "/goto?" not in raw_url:
                continue

            canonical_url, domain = URLNormalizer.normalize(raw_url)

            # Extract destination from cite tag for /goto? or obfuscated redirects
            if not domain or domain == "google.com":
                cite_tag = block.select_one("cite")
                if cite_tag:
                    cite_text = cite_tag.get_text(strip=True).split(" ")[0].split("›")[0].split("»")[0].strip()
                    if cite_text:
                        if not cite_text.startswith("http"):
                            cite_text = "https://" + cite_text
                        can_cite, cite_domain = URLNormalizer.normalize(cite_text)
                        if cite_domain and cite_domain != "google.com":
                            domain = cite_domain
                            canonical_url = can_cite

            if not canonical_url or domain == "google.com" or canonical_url in seen_urls:
                continue

            seen_urls.add(canonical_url)

            # Extract Title
            h3_tag = block.select_one("h3")
            title = h3_tag.get_text(strip=True) if h3_tag else link_tag.get_text(strip=True)
            if not title:
                title = domain

            # Extract Snippet
            snippet_tag = block.select_one("div.VwiC3b, div.yXK7kf, div.MUxGbe, div.BNeawe")
            snippet = snippet_tag.get_text(separator=" ", strip=True) if snippet_tag else ""

            # Classify result type
            # Check for Sponsored / Ads markers (including modern Google ad headings)
            block_text = block.get_text(separator=" ", strip=True)
            is_sponsored = bool(
                block.select_one("div.uE2f3, span:-soup-contains('Sponsored'), div[data-text-ad], div.vdQmEd, div[aria-label*='Sponsored' i]")
                or re.search(r"\b(sponsored|advertiser|offers from advertisers)\b", block_text[:120], re.I)
            )
            result_type = ResultType.SPONSORED if is_sponsored else ResultType.ORGANIC

            # Detect Featured Snippet (#0)
            is_featured = bool(
                block.select_one("div.cUnQKe, div[data-attrid='wa:/description'], div.xpdopen, div.kp-blk, div.ifM9O, div.LGOjhe")
            )

            # Detect Sitelinks
            sitelink_tags = block.select("table.jmPtSe a, div.usJj9c a, div.HiHjCd a, div.mslg a")
            sitelinks_list = [a.get_text(strip=True) for a in sitelink_tags if a.get_text(strip=True)]
            has_sitelinks = bool(sitelinks_list)

            absolute_counter += 1
            if result_type == ResultType.ORGANIC:
                organic_counter += 1
                current_organic_pos = page_organic_offset + organic_counter
            else:
                current_organic_pos = None

            items.append(
                SERPItem(
                    absolute_position=absolute_counter,
                    organic_position=current_organic_pos,
                    title=title,
                    url=raw_url,
                    canonical_url=canonical_url,
                    domain=domain,
                    snippet=snippet,
                    result_type=result_type,
                    serp_page=page_number,
                    is_featured_snippet=is_featured,
                    has_sitelinks=has_sitelinks,
                    sitelinks=sitelinks_list[:6],
                )
            )

        # Resolve any /goto? redirect URLs concurrently to their real landing page URLs
        goto_indices = [idx for idx, it in enumerate(items) if "/goto?" in (it.url or "")]
        if goto_indices:
            from concurrent.futures import ThreadPoolExecutor
            goto_urls = [items[i].url for i in goto_indices]
            with ThreadPoolExecutor(max_workers=min(10, len(goto_urls))) as ex:
                resolved_urls = list(ex.map(self._resolve_goto_url, goto_urls))
            for i, resolved_url in zip(goto_indices, resolved_urls):
                if resolved_url and resolved_url != items[i].url and resolved_url.startswith("http"):
                    can_url, dom = URLNormalizer.normalize(resolved_url)
                    items[i].url = resolved_url
                    items[i].canonical_url = can_url
                    if dom:
                        items[i].domain = dom

        return items

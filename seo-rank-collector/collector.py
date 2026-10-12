"""SERP Collector Orchestrator for SEO Rank Collector.

Coordinates multi-page SERP acquisition, parsing, normalization, storage, and rank diff comparison.
Supports:
- Engine: Google, Bing
- Mode: Browser (Approach 1 with CloakBrowser) or HTTP (Approach 2)
"""
import os
import time
import random
import logging
import urllib.parse
from datetime import datetime
from typing import Optional, Tuple, List

from rank_models import SERPSnapshot, SERPPageResult, SERPItem, RankDelta
from acquisition import SERPAcquisitor, SERPAcquisitionResult
from parsers.google import GoogleSERPParser
from parsers.bing import BingSERPParser
from storage import SERPStorage
from diff_engine import SERPDiffEngine

log = logging.getLogger("seo.rank_collector.collector")


REGIONAL_GOOGLE_DOMAINS = {
    "in": "https://www.google.co.in/search",
    "uk": "https://www.google.co.uk/search",
    "gb": "https://www.google.co.uk/search",
    "ca": "https://www.google.ca/search",
    "au": "https://www.google.com.au/search",
    "sg": "https://www.google.com.sg/search",
    "ae": "https://www.google.ae/search",
    "za": "https://www.google.co.za/search",
    "nz": "https://www.google.co.nz/search",
    "my": "https://www.google.com.my/search",
    "ph": "https://www.google.com.ph/search",
    "pk": "https://www.google.com.pk/search",
    "bd": "https://www.google.com.bd/search",
    "us": "https://www.google.com/search",
}


def generate_uule(location: str) -> str:
    """Generates Google canonical UULE parameter for geographic targeting."""
    import base64
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    loc_clean = location.strip()
    if not loc_clean:
        return ""
    length_char = chars[len(loc_clean)] if len(loc_clean) < len(chars) else chars[-1]
    encoded = base64.b64encode(loc_clean.encode("utf-8")).decode("utf-8")
    return f"w+CAIQICI{length_char}{encoded}"


class SERPCollector:
    """Orchestrates keyword SERP acquisition, pagination parsing, and snapshot creation."""

    def __init__(
        self,
        engine: str = "google",  # "google" or "bing"
        mode: str = "browser",   # "browser", "http", or "hybrid"
        headless: bool = True,
        solver_url: Optional[str] = None,
        storage_dir: Optional[str] = None,
        page_delay: float = 1.5,
        persist_cookies: bool = True,
    ):
        self.engine = engine.lower()
        self.mode = mode.lower()
        self.page_delay = page_delay
        if not solver_url:
            solver_url = os.getenv("SOLVER_URL", f"http://127.0.0.1:{os.getenv('PORT', '5001')}")
        self.acquisitor = SERPAcquisitor(
            solver_url=solver_url,
            mode=self.mode,
            headless=headless,
            persist_cookies=persist_cookies,
            storage_dir=storage_dir,
        )
        self.parser = BingSERPParser() if self.engine == "bing" else GoogleSERPParser()
        self.storage = SERPStorage(storage_dir=storage_dir)

    def _build_search_url(
        self,
        keyword: str,
        page_number: int = 1,
        location: Optional[str] = None,
        country: str = "us",
        uule: Optional[str] = None,
    ) -> str:
        if self.engine == "bing":
            base_url = "https://www.bing.com/search"
            # Bing pagination uses first=1, first=11, first=21
            first_offset = (page_number - 1) * 10 + 1
            params = {"q": keyword}
            if first_offset > 1:
                params["first"] = str(first_offset)
            if country:
                params["cc"] = country.upper()
            return f"{base_url}?{urllib.parse.urlencode(params)}"

        # Default Google: Use global google.com with gl & hl targeting.
        # Note: Regional ccTLDs (like google.co.in) have extremely strict per-IP
        # rate limits and trigger immediate HTTP 429 rate limiting on local IPs.
        base_url = "https://www.google.com/search"
        try:
            from geo_resolver import normalize_country_code
            country_code = normalize_country_code(country, location=location)
        except Exception:
            country_code = (country or "us").lower().strip()
        start_offset = (page_number - 1) * 10
        params = {
            "q": keyword,
            "hl": "en",
            "gl": country_code,
        }
        if start_offset > 0:
            params["start"] = str(start_offset)
        if uule:
            params["uule"] = uule
        elif location:
            try:
                from geo_resolver import resolve_canonical_location
                resolved_loc = resolve_canonical_location(location, country=country_code)
            except Exception:
                resolved_loc = location
            params["uule"] = generate_uule(resolved_loc)
        return f"{base_url}?{urllib.parse.urlencode(params)}"

    def collect(
        self,
        keyword: str,
        max_pages: int = 5,
        location: Optional[str] = None,
        country: str = "us",
        uule: Optional[str] = None,
        compare_previous: bool = True,
        target_domain: Optional[str] = None,
    ) -> Tuple[SERPSnapshot, Optional[List[RankDelta]]]:
        """Executes full keyword ranking collection across specified pages."""
        snapshot_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        all_items: List[SERPItem] = []
        pages_crawled = 0
        pages_failed = 0
        pages_skipped = 0
        failure_reason = None
        challenge_type = None
        status_code = None
        reason_code = None
        block_reason = None
        ai_overview_present = False
        featured_snippet_present = False
        map_pack_items = []

        # Resolve full GeoContext (Canonical, UULE, Hardware GPS coordinates, Timezone)
        geo_context = None
        effective_uule = uule
        if location:
            try:
                from geo_resolver import resolve_geo_context
                geo_context = resolve_geo_context(location, country=country)
                if not effective_uule:
                    effective_uule = geo_context.uule
                log.info(
                    "[Collector] Resolved GeoContext: %s | GPS: (%.4f, %.4f) | TZ: %s",
                    geo_context.canonical_name,
                    geo_context.latitude,
                    geo_context.longitude,
                    geo_context.timezone_id,
                )
            except Exception as geo_e:
                log.warning("Could not resolve geo_context: %s", geo_e)

        seen_canonical_urls = set()
        max_page_retries = 2
        for page_num in range(1, max_pages + 1):
            if page_num > 1 and self.page_delay > 0:
                jitter = random.uniform(0.85, 1.25)
                actual_delay = round(self.page_delay * jitter, 2)
                log.info("Pacing delay: waiting %.2fs before acquiring page %d to simulate natural human browsing...", actual_delay, page_num)
                time.sleep(actual_delay)

            search_url = self._build_search_url(
                keyword,
                page_number=page_num,
                location=location,
                country=country,
                uule=effective_uule,
            )

            acq_result = None
            for p_attempt in range(1, max_page_retries + 2):
                acq_result = self.acquisitor.acquire_serp(search_url, page_number=page_num, geo_context=geo_context)
                if acq_result.is_success and acq_result.html:
                    break
                if p_attempt <= max_page_retries:
                    log.warning(
                        "Page %d acquisition failed (status=%s). Retrying page (%d/%d)...",
                        page_num,
                        acq_result.status,
                        p_attempt,
                        max_page_retries,
                    )

            if not acq_result or not acq_result.is_success or not acq_result.html:
                pages_failed += 1
                failure_reason = acq_result.error if acq_result else "Acquisition failed"
                challenge_type = acq_result.challenge_type if acq_result else None
                status_code = acq_result.status_code if acq_result else None
                reason_code = acq_result.reason_code if acq_result else None
                block_reason = acq_result.block_reason if acq_result else None
                log.warning("Failed to acquire SERP page %d for '%s' after retries: %s. Continuing to next page...", page_num, keyword, failure_reason)
                continue

            if page_num == 1:
                if hasattr(self.parser, "detect_features"):
                    feats = self.parser.detect_features(acq_result.html)
                    ai_overview_present = feats.get("ai_overview_present", False)
                    featured_snippet_present = feats.get("featured_snippet_present", False)
                if hasattr(self.parser, "parse_map_pack"):
                    map_pack_items = self.parser.parse_map_pack(acq_result.html)

            page_items = self.parser.parse(acq_result.html, page_number=page_num)

            # Detect duplicate page redirect (e.g. Google redirected back to Page 1 because no more results exist)
            page_canonical_urls = {item.canonical_url for item in page_items if item.canonical_url}
            if page_num > 1 and page_canonical_urls and page_canonical_urls.issubset(seen_canonical_urls):
                log.info(
                    "Page %d returned duplicate results of previous page (%d duplicate URLs). "
                    "Google has redirected to the first page (no further search results exist for '%s'). Stopping pagination.",
                    page_num,
                    len(page_items),
                    keyword,
                )
                pages_skipped = max_pages - (page_num - 1)
                break

            # Filter out any overlapping duplicate URLs across pages
            unique_items = []
            for item in page_items:
                if item.canonical_url not in seen_canonical_urls:
                    seen_canonical_urls.add(item.canonical_url)
                    unique_items.append(item)

            if not unique_items and page_num == 1 and self.mode in ("hybrid", "auto", "http"):
                log.info("Page 1 yielded 0 parsed items in HTTP mode. Escalating to CloakBrowser stealth mode...")
                try:
                    browser_res = self.acquisitor._acquire_serp_browser(search_url, page_number=page_num)
                    if browser_res.is_success and browser_res.html:
                        page_items = self.parser.parse(browser_res.html, page_number=page_num)
                        for item in page_items:
                            if item.canonical_url not in seen_canonical_urls:
                                seen_canonical_urls.add(item.canonical_url)
                                unique_items.append(item)
                        if hasattr(self.parser, "detect_features"):
                            feats = self.parser.detect_features(browser_res.html)
                            ai_overview_present = feats.get("ai_overview_present", False)
                            featured_snippet_present = feats.get("featured_snippet_present", False)
                        if hasattr(self.parser, "parse_map_pack"):
                            map_pack_items = self.parser.parse_map_pack(browser_res.html)
                except Exception as e:
                    log.warning("Browser escalation failed: %s", e)

            if not unique_items:
                log.info("Page %d yielded no new organic results. Stopping pagination.", page_num)
                pages_skipped = max_pages - (page_num - 1)
                break

            pages_crawled += 1
            log.info("Page %d yielded %d new parsed items", page_num, len(unique_items))
            all_items.extend(unique_items)

            # Early-Exit Optimization: If target_domain is discovered on this page, stop pagination early
            if target_domain:
                clean_target = target_domain.strip().lower()
                if "://" in clean_target:
                    clean_target = clean_target.split("://", 1)[1]
                clean_target = clean_target.split("/", 1)[0]
                if clean_target.startswith("www."):
                    clean_target = clean_target[4:]

                found_target = any(
                    (it.domain and (it.domain.lower() == clean_target or it.domain.lower().endswith("." + clean_target)))
                    for it in unique_items
                    if it.organic_position
                )
                if found_target and page_num < max_pages:
                    pages_skipped = max_pages - page_num
                    log.info(
                        "Target domain '%s' found on page %d. Early-exit triggered: saving %d remaining page crawls.",
                        target_domain,
                        page_num,
                        pages_skipped,
                    )
                    break

        # Determine overall acquisition status
        if pages_crawled == max_pages or (pages_crawled > 0 and pages_skipped > 0):
            acquisition_status = "SUCCESS"
        elif pages_crawled > 0:
            acquisition_status = "PARTIAL"
        elif status_code in (401, 403, 429, 503) or (failure_reason and "block" in failure_reason.lower()) or (challenge_type and challenge_type != "none_detected"):
            acquisition_status = "BLOCKED"
        else:
            acquisition_status = "FAILED"

        organic_count = sum(1 for item in all_items if item.organic_position is not None)

        snapshot = SERPSnapshot(
            snapshot_id=snapshot_id,
            keyword=keyword,
            location=location,
            country=country,
            target_domain=target_domain,
            pages_requested=max_pages,
            pages_crawled=pages_crawled,
            pages_failed=pages_failed,
            pages_skipped=pages_skipped,
            total_organic_results=organic_count,
            items=all_items,
            ai_overview_present=ai_overview_present,
            featured_snippet_present=featured_snippet_present,
            map_pack_items=map_pack_items,
            acquisition_status=acquisition_status,
            failure_reason=failure_reason,
            reason_code=reason_code,
            block_reason=block_reason,
            challenge_type=challenge_type,
            status_code=status_code,
        )

        # Check for previous snapshot to generate rank diffs ONLY if we have valid crawled items
        rank_deltas: Optional[List[RankDelta]] = None
        if compare_previous and pages_crawled > 0 and len(all_items) > 0:
            previous_snapshot = self.storage.get_latest_snapshot(keyword, country=country, city=location, device="desktop")
            # Only compare if previous snapshot also had valid crawled results
            if previous_snapshot and previous_snapshot.pages_crawled > 0 and len(previous_snapshot.items) > 0:
                rank_deltas = SERPDiffEngine.compare_snapshots(previous_snapshot, snapshot)
                log.info("Generated %d rank deltas compared to previous snapshot %s", len(rank_deltas), previous_snapshot.snapshot_id)

        # Save snapshot
        self.storage.save_snapshot(snapshot, country=country, city=location, device="desktop")

        return snapshot, rank_deltas

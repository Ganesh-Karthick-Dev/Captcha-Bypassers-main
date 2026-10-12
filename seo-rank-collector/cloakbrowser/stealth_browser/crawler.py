"""Adaptive crawler with rate limiting, concurrency throttling, and exponential backoff."""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("cloakbrowser.stealth_browser.crawler")


@dataclass
class CrawlResponse:
    url: str
    status_code: int
    headers: Dict[str, str]
    body: Optional[str] = None
    is_success: bool = False
    retry_after: Optional[float] = None
    redirect_chain: List[str] = None


class AdaptiveCrawler:
    """Adaptive crawler that dynamically manages request pacing, concurrency,
    and HTTP response status codes (including 429 Retry-After and exponential backoff).
    """

    def __init__(
        self,
        base_delay_seconds: float = 0.5,
        max_delay_seconds: float = 30.0,
        max_concurrency: int = 4,
        max_retries: int = 3,
        jitter_ratio: float = 0.2,
    ):
        self.base_delay = base_delay_seconds
        self.max_delay = max_delay_seconds
        self.max_retries = max_retries
        self.jitter_ratio = jitter_ratio
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._last_request_time = 0.0

    @staticmethod
    def parse_retry_after(header_val: Optional[str]) -> Optional[float]:
        """Parse Retry-After header which can be an integer (seconds) or an HTTP date."""
        if not header_val:
            return None
        clean_val = header_val.strip()
        if clean_val.isdigit():
            return float(clean_val)
        try:
            parsed_date = email.utils.parsedate_to_datetime(clean_val)
            delta = parsed_date.timestamp() - time.time()
            return max(0.0, delta)
        except Exception:
            return None

    def calculate_backoff(self, retry_count: int, retry_after: Optional[float] = None) -> float:
        """Calculate backoff delay respecting Retry-After or exponential backoff with jitter."""
        if retry_after is not None and retry_after > 0:
            return min(retry_after, self.max_delay)

        exponential = self.base_delay * (2 ** retry_count)
        jitter = random.uniform(0, self.jitter_ratio * exponential)
        return min(exponential + jitter, self.max_delay)

    def pace_request(self) -> None:
        """Synchronous delay to enforce baseline pacing between requests."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.base_delay:
            time.sleep(self.base_delay - elapsed)
        self._last_request_time = time.time()

    async def pace_request_async(self) -> None:
        """Asynchronous delay to enforce baseline pacing between requests."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.base_delay:
            await asyncio.sleep(self.base_delay - elapsed)
        self._last_request_time = time.time()

    def navigate_with_resilience(
        self,
        page: Any,
        url: str,
        on_reauth_needed: Optional[Callable[[], None]] = None,
    ) -> CrawlResponse:
        """Navigate a Playwright page with automatic 429 rate limit backoff and 5xx retries."""
        retries = 0
        last_status = 0
        headers_dict: Dict[str, str] = {}

        while retries <= self.max_retries:
            self.pace_request()
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if response:
                    last_status = response.status
                    headers_dict = response.headers
                else:
                    last_status = 200

                # 2xx / 3xx Success
                if 200 <= last_status < 400:
                    return CrawlResponse(
                        url=url,
                        status_code=last_status,
                        headers=headers_dict,
                        is_success=True,
                    )

                # 401 Unauthorized -> Trigger re-authentication if callback provided
                if last_status == 401 and on_reauth_needed:
                    logger.warning("[crawler] Received 401 Unauthorized on %s; triggering re-auth callback", url)
                    on_reauth_needed()

                # 429 Rate Limited -> Exponential backoff with Retry-After
                if last_status == 429:
                    retry_after = self.parse_retry_after(headers_dict.get("retry-after"))
                    delay = self.calculate_backoff(retries, retry_after)
                    logger.warning(
                        "[crawler] 429 Rate Limited on %s (attempt %d/%d). Backing off for %.2fs",
                        url, retries + 1, self.max_retries, delay
                    )
                    time.sleep(delay)
                    retries += 1
                    continue

                # 5xx Server Error -> Retry
                if 500 <= last_status < 600:
                    delay = self.calculate_backoff(retries)
                    logger.warning(
                        "[crawler] Server error %d on %s (attempt %d/%d). Retrying in %.2fs",
                        last_status, url, retries + 1, self.max_retries, delay
                    )
                    time.sleep(delay)
                    retries += 1
                    continue

                # 403 or other 4xx -> Non-retryable
                return CrawlResponse(
                    url=url,
                    status_code=last_status,
                    headers=headers_dict,
                    is_success=False,
                )

            except Exception as e:
                logger.warning("[crawler] Navigation error on %s: %s", url, e)
                delay = self.calculate_backoff(retries)
                time.sleep(delay)
                retries += 1

        return CrawlResponse(
            url=url,
            status_code=last_status or 504,
            headers=headers_dict,
            is_success=False,
        )

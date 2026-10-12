"""FastAPI Router for SEO Rank Tracking & Queued Collector Endpoints.

Connects directly with automation_dashboard position-tracker:
- POST /keyword-tracking/jobs               (Queue asynchronous multi-keyword crawl job with Fair Scheduler & In-Flight Dedup)
- GET  /keyword-tracking/jobs/{job_id}      (Poll job status & items from Memory + SQLite)
- GET  /keyword-tracking/jobs/{job_id}/stream (SSE real-time progress streaming)
- POST /keyword-tracking/search             (Synchronous single keyword position search)
- POST /keyword-tracking/batch              (Synchronous batch search)
- POST /api/v1/ranks/collect                (Direct SERP collection)
- GET  /api/v1/ranks/snapshots/{keyword}
- GET  /api/v1/ranks/health
"""
import os
import sys
import time
import json
import uuid
import random
import asyncio
import logging
import collections
import urllib.parse
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# Ensure package directory and repo root are on sys.path
RANK_DIR = Path(__file__).resolve().parent
REPO_ROOT = RANK_DIR.parent
for p in (RANK_DIR, REPO_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from rank_models import SERPSnapshot, SERPItem, ResultType, RankDelta  # type: ignore
from collector import SERPCollector  # type: ignore
from storage import SERPStorage  # type: ignore
from normalizer import URLNormalizer  # type: ignore

log = logging.getLogger("seo.rank_router")

rank_router = APIRouter()


# ── Pydantic Request & Response Schemas ────────────────────────────────────────

class KeywordTrackingSearchRequest(BaseModel):
    keyword: str
    targetDomain: Optional[str] = None
    domain: Optional[str] = None  # alias
    target: Optional[str] = None  # alias
    country: Optional[str] = "us"
    city: Optional[str] = None
    location: Optional[str] = None
    device: Optional[str] = "desktop"
    depth: Optional[int] = 50
    useProxy: Optional[bool] = True
    forceFresh: Optional[bool] = False
    force_fresh: Optional[bool] = None
    refresh: Optional[bool] = None
    isRefresh: Optional[bool] = None
    bypassCache: Optional[bool] = None
    bypass_cache: Optional[bool] = None
    fresh: Optional[bool] = None
    maxAgeHours: Optional[int] = 24
    headless: Optional[bool] = True
    mode: Optional[str] = "hybrid"
    engine: Optional[str] = "google"

    @property
    def is_force_fresh(self) -> bool:
        if self.forceFresh or self.force_fresh or self.refresh or self.isRefresh:
            return True
        if self.bypassCache or self.bypass_cache or self.fresh:
            return True
        if self.maxAgeHours is not None and self.maxAgeHours <= 0:
            return True
        return False


class KeywordTrackingBatchRequest(BaseModel):
    keywords: List[str]
    targetDomain: Optional[str] = None
    domain: Optional[str] = None
    target: Optional[str] = None
    country: Optional[str] = "us"
    city: Optional[str] = None
    location: Optional[str] = None
    device: Optional[str] = "desktop"
    depth: Optional[int] = 50
    concurrency: Optional[int] = 2
    useProxy: Optional[bool] = True
    forceFresh: Optional[bool] = False
    force_fresh: Optional[bool] = None
    refresh: Optional[bool] = None
    isRefresh: Optional[bool] = None
    bypassCache: Optional[bool] = None
    bypass_cache: Optional[bool] = None
    fresh: Optional[bool] = None
    maxAgeHours: Optional[int] = 24
    headless: Optional[bool] = True
    mode: Optional[str] = "hybrid"
    engine: Optional[str] = "google"

    @property
    def is_force_fresh(self) -> bool:
        if self.forceFresh or self.force_fresh or self.refresh or self.isRefresh:
            return True
        if self.bypassCache or self.bypass_cache or self.fresh:
            return True
        if self.maxAgeHours is not None and self.maxAgeHours <= 0:
            return True
        return False


class CreateJobRequest(BaseModel):
    keywords: List[str]
    userId: Optional[str] = None
    clientId: Optional[str] = None
    targetDomain: Optional[str] = None
    domain: Optional[str] = None
    target: Optional[str] = None
    country: Optional[str] = "us"
    city: Optional[str] = None
    location: Optional[str] = None
    device: Optional[str] = "desktop"
    depth: Optional[int] = 50
    concurrency: Optional[int] = 2
    useProxy: Optional[bool] = True
    forceFresh: Optional[bool] = False
    maxAgeHours: Optional[int] = 24
    mode: Optional[str] = "hybrid"
    engine: Optional[str] = "google"
    webhookUrl: Optional[str] = None


class JobItemState(BaseModel):
    id: str
    keyword: str
    status: str = "queued"  # queued, processing, completed, failed
    rank: Optional[int] = None
    rankLabel: Optional[str] = None
    matchedUrl: Optional[str] = None
    targetUrl: Optional[str] = None
    error: Optional[str] = None
    elapsed_sec: Optional[float] = None
    fromCache: bool = False
    result: Optional[Dict[str, Any]] = None


class JobState(BaseModel):
    jobId: str
    userId: Optional[str] = "default_user"
    status: str = "queued"  # queued, in_progress, completed, failed
    createdAt: str
    updatedAt: str
    targetDomain: Optional[str] = None
    country: Optional[str] = "us"
    city: Optional[str] = None
    device: Optional[str] = "desktop"
    depth: int = 10
    total: int = 0
    completed: int = 0
    failed: int = 0
    progressPercent: int = 0
    currentKeyword: Optional[str] = None
    items: List[JobItemState] = []


class CollectRankRequest(BaseModel):
    keyword: str
    target_domain: Optional[str] = None
    max_pages: Optional[int] = 2
    location: Optional[str] = None
    country: Optional[str] = "us"
    engine: Optional[str] = "google"
    mode: Optional[str] = "hybrid"
    headless: Optional[bool] = True
    compare_previous: Optional[bool] = True


# ── Domain Matching Utilities ──────────────────────────────────────────────────

def normalize_target_domain(target: Optional[str]) -> str:
    """Extracts bare domain (e.g., 'webnox.in') from input URL or string."""
    if not target:
        return ""
    target = target.strip().lower()
    if "://" in target:
        target = target.split("://", 1)[1]
    target = target.split("/", 1)[0]
    if target.startswith("www."):
        target = target[4:]
    return target


def matches_domain(item_domain: str, item_url: str, target_clean: str) -> bool:
    """Checks if an organic SERP result matches target domain or subdomain."""
    if not target_clean:
        return False
    item_domain = (item_domain or "").lower()
    item_url = (item_url or "").lower()

    if item_domain == target_clean or item_domain.endswith("." + target_clean):
        return True

    try:
        parsed = urllib.parse.urlparse(item_url if "://" in item_url else f"https://{item_url}")
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host == target_clean or host.endswith("." + target_clean):
            return True
    except Exception:
        pass

    return False


def _process_single_search(req: KeywordTrackingSearchRequest) -> dict:
    """Executes single keyword position search and returns dashboard payload."""
    start_ts = time.time()
    target_domain = req.targetDomain or req.domain or getattr(req, "target", None) or ""
    clean_target = normalize_target_domain(target_domain)

    # Resolve location and clean country
    location_str = req.location or req.city
    if req.city and req.country and not req.location:
        location_str = f"{req.city}, {req.country.upper()}"
    try:
        from geo_resolver import normalize_country_code  # type: ignore
        clean_country = normalize_country_code(req.country, location=location_str)
    except Exception:
        clean_country = (req.country or "us").lower().strip()

    # Calculate pages needed based on requested depth (1 page = 10 organic results, min 5 pages / depth 50)
    depth = req.depth if (req.depth and req.depth > 0) else 50
    if depth <= 10:
        depth = 50
    max_pages = max(1, min(10, (depth + 9) // 10))

    storage = SERPStorage()
    snapshot: Optional[SERPSnapshot] = None
    from_cache = False

    # Check cache if not forcing fresh
    if not req.is_force_fresh:
        cached = storage.get_latest_snapshot(
            keyword=req.keyword,
            country=clean_country,
            city=req.city or req.location,
            device=req.device or "desktop",
        )
        if cached and cached.pages_crawled > 0 and len(cached.items) > 0:
            max_age_sec = (req.maxAgeHours or 24) * 3600
            cached_dt = cached.timestamp.replace(tzinfo=None) if hasattr(cached.timestamp, "replace") else datetime.now(timezone.utc).replace(tzinfo=None)
            age = (datetime.now(timezone.utc).replace(tzinfo=None) - cached_dt).total_seconds()
            if age <= max_age_sec and cached.pages_crawled >= max_pages:
                snapshot = cached
                from_cache = True
                log.info("🎯 [CACHE HIT] '%s' (%s, %s, age=%.0fs)", req.keyword, clean_country, req.device, age)
    else:
        log.info("🔄 [REFRESH TRIGGERED] Force-fresh enabled: bypassing cache for '%s'", req.keyword)

    # Perform crawl if no cache hit
    if not snapshot:
        mode = req.mode or "hybrid"
        log.info(
            "🚀 [LIVE SCRAPE] '%s' | Target: '%s' | Max Pages: %d (Depth %d) | Engine: %s | Mode: %s | Geo: %s (%s)",
            req.keyword,
            clean_target or "None",
            max_pages,
            depth,
            req.engine or "google",
            mode,
            location_str or clean_country,
            clean_country,
        )
        collector = SERPCollector(
            engine=req.engine or "google",
            mode=mode,
            headless=req.headless if req.headless is not None else True,
        )
        snapshot, _ = collector.collect(
            keyword=req.keyword,
            max_pages=max_pages,
            location=location_str,
            country=clean_country,
            target_domain=clean_target or None,
            compare_previous=True,
        )

        # Save snapshot with composite geo/device key
        if snapshot and snapshot.pages_crawled > 0:
            try:
                storage.save_snapshot(
                    snapshot,
                    country=clean_country,
                    city=req.city or req.location,
                    device=req.device or "desktop",
                )
            except Exception as e:
                log.warning("Failed saving snapshot to cache: %s", e)

    # Match target domain
    matched_item = None
    rank = -1
    matched_url = None

    if clean_target and snapshot and snapshot.items:
        for it in snapshot.items:
            if it.organic_position and matches_domain(it.domain, it.url, clean_target):
                matched_item = it
                rank = it.organic_position
                matched_url = it.url
                break

    # Structure organic results list
    organic_results = [
        {
            "position": it.organic_position,
            "absolutePosition": getattr(it, "absolute_position", it.organic_position or 1),
            "domain": it.domain,
            "url": it.url,
            "title": it.title,
            "snippet": it.snippet,
            "isFeaturedSnippet": getattr(it, "is_featured_snippet", False),
            "hasSitelinks": getattr(it, "has_sitelinks", False),
            "sitelinks": getattr(it, "sitelinks", []),
        }
        for it in (snapshot.items if snapshot else [])
        if it.result_type == ResultType.ORGANIC
    ]

    # Map Pack items & business match
    map_pack_results = [
        {
            "position": m.position,
            "title": m.title,
            "rating": m.rating,
            "reviewsCount": m.reviews_count,
            "address": m.address,
            "phone": m.phone,
            "websiteUrl": m.website_url,
            "domain": m.domain,
        }
        for m in (getattr(snapshot, "map_pack_items", []) or [])
    ] if snapshot else []

    map_pack_rank = None
    if clean_target and map_pack_results:
        for m in map_pack_results:
            m_domain = (m.get("domain") or "").lower()
            m_url = (m.get("websiteUrl") or "").lower()
            if matches_domain(m_domain, m_url, clean_target):
                map_pack_rank = m["position"]
                break

    # Assign final status & rank label
    if rank > 0:
        status = "SUCCESS"
        rank_label = f"#{rank}"
    elif snapshot and (snapshot.acquisition_status in ("BLOCKED", "FAILED") or snapshot.pages_crawled == 0):
        if snapshot.challenge_type and snapshot.challenge_type != "none_detected":
            status = "CAPTCHA"
            rank = -2
        else:
            status = "ERROR"
            rank = -2
        rank_label = status
    else:
        status = "NOT_FOUND"
        rank = -1
        rank_label = f"Not in top {depth}"

    res_dict = {
        "keyword": req.keyword,
        "rank": rank,
        "rankLabel": rank_label,
        "matchedUrl": matched_url,
        "targetUrl": matched_url,
        "targetDomain": clean_target or target_domain,
        "status": status,
        "fromCache": from_cache,
        "totalOrganicResults": snapshot.total_organic_results if snapshot else 0,
        "pagesCrawled": snapshot.pages_crawled if snapshot else 0,
        "depth": depth,
        "organicResults": organic_results,
        "mapPackResults": map_pack_results,
        "mapPackRank": map_pack_rank,
        "aiOverviewPresent": getattr(snapshot, "ai_overview_present", False) if snapshot else False,
        "featuredSnippetPresent": getattr(snapshot, "featured_snippet_present", False) if snapshot else False,
        "isFeaturedSnippet": getattr(matched_item, "is_featured_snippet", False) if matched_item else False,
        "hasSitelinks": getattr(matched_item, "has_sitelinks", False) if matched_item else False,
        "snapshotId": snapshot.snapshot_id if snapshot else "",
        "bandwidth": {"bytes": len(json.dumps(organic_results)) + 2048},
    }
    if snapshot and snapshot.failure_reason:
        res_dict["error"] = snapshot.failure_reason

    elapsed_sec = round(time.time() - start_ts, 2)
    tag = "🎯 CACHED" if from_cache else "🚀 LIVE"
    log.info(
        "[%s] '%s' -> %s | Matched: %s | Pages: %d | Time: %.2fs",
        tag,
        req.keyword,
        rank_label,
        matched_url or "None",
        snapshot.pages_crawled if snapshot else 0,
        elapsed_sec,
    )

    return res_dict


# ── Optimization Layer 1: In-Flight Single-Flight Registry ──────────────────────

class InFlightRegistry:
    """Single-flight deduplication registry for active live crawls.

    If multiple users/jobs request the exact same (keyword, country, city, device) concurrently,
    only 1 worker executes the live crawl against Google. All other waiters attach to the same Future.
    """

    def __init__(self):
        self._futures: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    async def get_or_register(self, cache_slug: str) -> Tuple[asyncio.Future, bool]:
        """Returns (future, is_initiator).
        If is_initiator is True, caller is responsible for executing crawl and resolving future.
        If is_initiator is False, caller simply awaits the returned future.
        """
        async with self._lock:
            if cache_slug in self._futures:
                log.info("🔗 [IN-FLIGHT COALESCE] Attaching to existing crawl for '%s'", cache_slug)
                return self._futures[cache_slug], False

            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            self._futures[cache_slug] = fut
            return fut, True

    async def resolve(self, cache_slug: str, result: dict):
        async with self._lock:
            fut = self._futures.pop(cache_slug, None)
            if fut and not fut.done():
                fut.set_result(result)

    async def fail(self, cache_slug: str, exc: Exception):
        async with self._lock:
            fut = self._futures.pop(cache_slug, None)
            if fut and not fut.done():
                fut.set_exception(exc)


# ── Optimization Layer 2: Cadence Pacer (Gentle Anti-Bot Spacing) ─────────────

class CadencePacer:
    """Enforces dynamic queue-aware randomized jitter delay between outbound searches.

    - Small bursts (<= 15 items): 1.5s - 2.5s (fast ~5s/KW)
    - Medium batches (16 - 50 items): 3.0s - 4.5s (balanced ~6.5s - 8s/KW)
    - Large batches (> 50 items): 4.0s - 6.5s + 15s breather every 20 searches (sustained safe)

    Can be overridden via SERP_PACER_MIN_DELAY / SERP_PACER_MAX_DELAY in .env.
    """

    def __init__(self, min_delay: Optional[float] = None, max_delay: Optional[float] = None):
        self._env_min = min_delay
        self._env_max = max_delay
        self._lock = asyncio.Lock()
        self._last_egress_ts = 0.0
        self._consecutive_searches = 0

    async def pace(self, queue_depth: int = 1):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_egress_ts

            # Reset consecutive counter if there was already an idle gap between batches
            if elapsed > 25.0:
                self._consecutive_searches = 0

            # 1. Periodic Human Breather on long sustained queues
            self._consecutive_searches += 1
            if self._consecutive_searches >= 20 and queue_depth > 10:
                breather = random.uniform(12.0, 16.0)
                log.info(
                    "☕ [Cadence Pacer] 20 continuous searches reached (%d remaining in queue). Taking a %.1fs human breather to protect residential IP...",
                    queue_depth, breather
                )
                await asyncio.sleep(breather)
                self._consecutive_searches = 0
                self._last_egress_ts = time.monotonic()
                return

            # 2. Determine Dynamic Delays
            if self._env_min is not None and self._env_max is not None:
                min_d = self._env_min
                max_d = self._env_max
            elif queue_depth <= 15:
                # Fast burst mode for quick check jobs
                min_d, max_d = 1.5, 2.5
            elif queue_depth <= 50:
                # Medium batch mode
                min_d, max_d = 3.0, 4.5
            else:
                # Conservative sustained queue mode
                min_d, max_d = 4.0, 6.5

            target_delay = random.uniform(min_d, max_d)
            if elapsed < target_delay:
                sleep_time = target_delay - elapsed
                await asyncio.sleep(sleep_time)
            self._last_egress_ts = time.monotonic()


# ── Optimization Layer 3: Weighted Fair Multi-Tenant Scheduler ──────────────────

class FairJobQueue:
    """Weighted Fair Scheduler with Short-Job Priority and Wait-Time Aging.

    Partitions tasks into virtual queues per user_id.
    Dynamic fairness score:
        Score(u) = Remaining Items in User's Job / (max(1, Seconds Waited since Last Dispatch) ** 1.2)
    Lowest score wins the next dispatch slot.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition(self._lock)
        self.user_queues: Dict[str, collections.deque] = {}  # user_id -> deque[(job_id, item_id, req)]
        self.last_dispatched_at: Dict[str, float] = {}       # user_id -> timestamp
        self._total_items = 0

    async def put(self, user_id: str, item_tuple: tuple):
        async with self._cond:
            if user_id not in self.user_queues:
                self.user_queues[user_id] = collections.deque()
                self.last_dispatched_at[user_id] = time.time() - 5.0  # initial wait credit
            self.user_queues[user_id].append(item_tuple)
            self._total_items += 1
            self._cond.notify()

    async def get(self) -> tuple:
        async with self._cond:
            while self._total_items == 0:
                await self._cond.wait()

            now = time.time()
            best_user = None
            best_score = float("inf")

            for uid, q in list(self.user_queues.items()):
                if not q:
                    continue
                remaining = len(q)
                waited = max(1.0, now - self.last_dispatched_at.get(uid, now))
                # Dynamic scoring: small remaining = low score (fast finish), large waited = low score (no starvation)
                score = remaining / (waited ** 1.2)
                if score < best_score:
                    best_score = score
                    best_user = uid

            if best_user is None:
                for uid, q in self.user_queues.items():
                    if q:
                        best_user = uid
                        break

            if best_user is None or best_user not in self.user_queues:
                raise RuntimeError("FairJobQueue: total_items > 0 but no items found in queues")

            item_tuple = self.user_queues[best_user].popleft()
            self._total_items -= 1
            self.last_dispatched_at[best_user] = now

            if not self.user_queues[best_user]:
                del self.user_queues[best_user]

            return item_tuple

    @property
    def qsize(self) -> int:
        return self._total_items


# ── Asynchronous Job Manager (Fair Multi-User & Persistent) ───────────────────

class AsyncJobManager:
    """Controls background task queuing, bounded worker concurrency, and SSE broadcasting."""

    def __init__(self, concurrency: Optional[int] = None):
        if concurrency is None:
            # Default to 1 worker for residential IP safety to eliminate Google HTTP 429 rate limits.
            # Can be scaled via SERP_WORKER_CONCURRENCY env var when rotating proxies are configured.
            concurrency = int(os.getenv("SERP_WORKER_CONCURRENCY", "1"))
        self.concurrency = max(1, concurrency)
        self.jobs: Dict[str, JobState] = {}
        self.subscribers: Dict[str, List[asyncio.Queue]] = {}
        self.storage = SERPStorage()
        self.in_flight = InFlightRegistry()
        env_min = os.getenv("SERP_PACER_MIN_DELAY")
        env_max = os.getenv("SERP_PACER_MAX_DELAY")
        pacer_min = float(env_min) if (env_min and env_min.strip()) else None
        pacer_max = float(env_max) if (env_max and env_max.strip()) else None
        self.pacer = CadencePacer(min_delay=pacer_min, max_delay=pacer_max)
        self.fair_queue = FairJobQueue()
        self._workers: List[asyncio.Task] = []
        self._workers_running = False

    def ensure_workers(self):
        """Ensures that the worker tasks are running and resets any leased items on boot."""
        if not self._workers_running:
            # Recovery from unexpected restarts
            recovered_count = self.storage.reset_leased_items_to_queued()
            if recovered_count > 0:
                log.info("🔄 Server reboot recovery: Reset %d in-flight item(s) to 'queued'", recovered_count)

            self._workers_running = True
            for i in range(self.concurrency):
                task = asyncio.create_task(self._worker_loop(i + 1))
                self._workers.append(task)
            log.info("🚀 AsyncJobManager initialized with %d Fair Worker(s)", self.concurrency)

    async def create_job(self, req: CreateJobRequest) -> JobState:
        self.ensure_workers()
        now_iso = datetime.now(timezone.utc).isoformat()
        job_id = f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        user_id = req.userId or req.clientId or req.targetDomain or "default_user"

        items: List[JobItemState] = []
        queued_items: List[JobItemState] = []
        completed_count = 0

        target_domain = req.targetDomain or req.domain or getattr(req, "target", None) or ""
        clean_target = normalize_target_domain(target_domain)
        depth = req.depth if (req.depth and req.depth > 0) else 50
        if depth <= 10:
            depth = 50
        max_pages = max(1, min(10, (depth + 9) // 10))

        # Normalize country
        location_str = req.location or req.city
        if req.city and req.country and not req.location:
            location_str = f"{req.city}, {req.country.upper()}"
        try:
            from geo_resolver import normalize_country_code  # type: ignore
            clean_country = normalize_country_code(req.country, location=location_str)
            req.country = clean_country
        except Exception:
            clean_country = (req.country or "us").lower().strip()

        # 1. Pre-Queue Cache Resolution (0ms Instant Return for Cache Hits)
        for idx, kw in enumerate(req.keywords):
            kw_clean = kw.strip()
            if not kw_clean:
                continue

            item_id = f"item_{job_id}_{idx}"
            item_state = JobItemState(
                id=item_id,
                keyword=kw_clean,
                status="queued",
            )

            # Check cache if not force-fresh
            if not req.forceFresh:
                cached = self.storage.get_latest_snapshot(
                    keyword=kw_clean,
                    country=req.country or "us",
                    city=req.city or req.location,
                    device=req.device or "desktop",
                )
                if cached and cached.pages_crawled >= max_pages and len(cached.items) > 0:
                    max_age_sec = (req.maxAgeHours or 24) * 3600
                    cached_dt = cached.timestamp.replace(tzinfo=None) if hasattr(cached.timestamp, "replace") else datetime.now(timezone.utc).replace(tzinfo=None)
                    age = (datetime.now(timezone.utc).replace(tzinfo=None) - cached_dt).total_seconds()
                    if age <= max_age_sec:
                        # Instant cache hit!
                        matched_item = None
                        rank = -1
                        matched_url = None
                        if clean_target:
                            for it in cached.items:
                                if it.organic_position and matches_domain(it.domain, it.url, clean_target):
                                    matched_item = it
                                    rank = it.organic_position
                                    matched_url = it.url
                                    break

                        rank_label = f"#{rank}" if rank > 0 else f"Not in top {depth}"
                        item_state.status = "completed"
                        item_state.rank = rank
                        item_state.rankLabel = rank_label
                        item_state.matchedUrl = matched_url
                        item_state.targetUrl = matched_url
                        item_state.fromCache = True
                        item_state.elapsed_sec = 0.05
                        cached_organic = [
                            {
                                "position": it.organic_position,
                                "absolutePosition": getattr(it, "absolute_position", it.organic_position or 1),
                                "domain": it.domain,
                                "url": it.url,
                                "title": it.title,
                                "snippet": it.snippet,
                                "isFeaturedSnippet": getattr(it, "is_featured_snippet", False),
                                "hasSitelinks": getattr(it, "has_sitelinks", False),
                                "sitelinks": getattr(it, "sitelinks", []),
                            }
                            for it in (cached.items if cached else [])
                            if it.result_type == ResultType.ORGANIC
                        ]
                        item_state.result = {
                            "keyword": kw_clean,
                            "rank": rank,
                            "rankLabel": rank_label,
                            "matchedUrl": matched_url,
                            "targetUrl": matched_url,
                            "targetDomain": clean_target or req.targetDomain or req.domain,
                            "status": "SUCCESS",
                            "fromCache": True,
                            "totalOrganicResults": len(cached_organic),
                            "organicResults": cached_organic,
                        }
                        completed_count += 1
                        log.info("⚡ [PRE-QUEUE CACHE HIT] '%s' returned instantly (<50ms)", kw_clean)

            items.append(item_state)
            if item_state.status == "queued":
                queued_items.append(item_state)

        total_items = len(items)
        progress_pct = int((completed_count / total_items) * 100) if total_items > 0 else 100
        initial_status = "completed" if completed_count >= total_items else "queued"

        state = JobState(
            jobId=job_id,
            userId=user_id,
            status=initial_status,
            createdAt=now_iso,
            updatedAt=now_iso,
            targetDomain=req.targetDomain or req.domain,
            country=req.country or "us",
            city=req.city or req.location,
            device=req.device or "desktop",
            depth=depth,
            total=total_items,
            completed=completed_count,
            failed=0,
            progressPercent=progress_pct,
            items=items,
        )

        self.jobs[job_id] = state
        self.subscribers[job_id] = []

        # 2. Persist Initial Job State to SQLite
        try:
            self.storage.save_job(state.model_dump(), req.model_dump())
            self.storage.save_job_items(job_id, [it.model_dump() for it in items])
        except Exception as e:
            log.warning("Could not persist job %s to SQLite: %s", job_id, e)

        # 3. Enqueue Only Uncached Items into Fair Multi-Tenant Queue
        for q_item in queued_items:
            await self.fair_queue.put(user_id, (job_id, q_item.id, req))

        log.info(
            "📋 Enqueued Job '%s' [User: %s] | Total: %d | Instant Cached: %d | Live Queue: %d (Fair Queue Total: %d)",
            job_id,
            user_id,
            total_items,
            completed_count,
            len(queued_items),
            self.fair_queue.qsize,
        )
        return state

    async def _worker_loop(self, worker_id: int):
        log.info("Worker #%d ready for Fair Queue items", worker_id)
        while True:
            try:
                job_id, item_id, req = await self.fair_queue.get()
                await self._process_item(worker_id, job_id, item_id, req)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Worker #%d unhandled loop error: %s", worker_id, e, exc_info=True)

    async def _process_item(self, worker_id: int, job_id: str, item_id: str, job_req: CreateJobRequest):
        job = self.jobs.get(job_id)
        if not job:
            # Fallback to SQLite if not in memory
            job_dict = self.storage.get_job(job_id)
            if job_dict:
                job = JobState(**job_dict)
                self.jobs[job_id] = job
            else:
                return

        item = next((it for it in job.items if it.id == item_id), None)
        if not item:
            return

        # Update item to processing
        item.status = "processing"
        job.status = "in_progress"
        job.currentKeyword = item.keyword
        job.updatedAt = datetime.now(timezone.utc).isoformat()

        self.storage.update_job_item(item_id=item.id, status="processing")
        self.storage.update_job_progress(
            job_id=job.jobId,
            completed=job.completed,
            failed=job.failed,
            progress_percent=job.progressPercent,
            status="in_progress",
            current_keyword=item.keyword,
        )

        await self._broadcast_event(job_id, {
            "event": "item_started",
            "jobId": job_id,
            "itemId": item.id,
            "keyword": item.keyword,
            "completed": job.completed,
            "total": job.total,
            "progressPercent": job.progressPercent,
        })

        search_req = KeywordTrackingSearchRequest(
            keyword=item.keyword,
            targetDomain=job_req.targetDomain or job_req.domain,
            country=job_req.country,
            city=job_req.city,
            location=job_req.location,
            device=job_req.device,
            depth=job_req.depth,
            useProxy=job_req.useProxy,
            forceFresh=job_req.forceFresh,
            maxAgeHours=job_req.maxAgeHours,
            mode=job_req.mode or "hybrid",
            engine=job_req.engine or "google",
        )

        cache_slug = self.storage._cache_slug(
            keyword=item.keyword,
            country=job_req.country or "us",
            city=job_req.city or job_req.location,
            device=job_req.device or "desktop",
        )

        item_start = time.time()
        res = None
        try:
            # ── In-Flight Deduplication Check ──
            fut, is_initiator = await self.in_flight.get_or_register(cache_slug)

            if is_initiator:
                try:
                    # Adaptive queue-aware pacing (burst vs medium vs sustained)
                    remaining_in_job = max(1, job.total - (job.completed + job.failed)) if job else 1
                    total_queued = max(remaining_in_job, getattr(self.fair_queue, "_total_items", 0))
                    await self.pacer.pace(queue_depth=total_queued)
                    res = await asyncio.to_thread(_process_single_search, search_req)
                    await self.in_flight.resolve(cache_slug, res)
                except Exception as crawl_err:
                    await self.in_flight.fail(cache_slug, crawl_err)
                    raise crawl_err
            else:
                # Piggyback on in-flight crawl from other worker/user
                try:
                    res = await asyncio.wait_for(asyncio.shield(fut), timeout=45.0)
                except Exception as wait_err:
                    log.warning("In-flight wait timeout/error for '%s': %s. Retrying directly...", item.keyword, wait_err)
                    res = await asyncio.to_thread(_process_single_search, search_req)

            elapsed = round(time.time() - item_start, 2)
            item.rank = res.get("rank")
            item.rankLabel = res.get("rankLabel")
            item.matchedUrl = res.get("matchedUrl")
            item.targetUrl = res.get("targetUrl")
            item.fromCache = res.get("fromCache", False)
            item.elapsed_sec = elapsed
            item.result = res
            item.status = "completed" if res.get("status") in ("SUCCESS", "NOT_FOUND") else "failed"

            if item.status == "completed":
                job.completed += 1
            else:
                job.failed += 1

        except Exception as e:
            log.error("Item crawl error for '%s': %s", item.keyword, e)
            item.status = "failed"
            item.error = str(e)
            item.rank = -2
            item.rankLabel = "ERROR"
            job.failed += 1

        # Persist item completion to SQLite
        self.storage.update_job_item(
            item_id=item.id,
            status=item.status,
            rank=item.rank,
            rank_label=item.rankLabel,
            matched_url=item.matchedUrl,
            target_url=item.targetUrl,
            error=item.error,
            elapsed_sec=item.elapsed_sec,
            from_cache=item.fromCache,
            result=item.result,
        )

        # Recalculate progress
        finished_count = job.completed + job.failed
        job.progressPercent = int((finished_count / job.total) * 100) if job.total > 0 else 100
        job.updatedAt = datetime.now(timezone.utc).isoformat()

        self.storage.update_job_progress(
            job_id=job.jobId,
            completed=job.completed,
            failed=job.failed,
            progress_percent=job.progressPercent,
            status="completed" if finished_count >= job.total else "in_progress",
            current_keyword=item.keyword,
        )

        await self._broadcast_event(job_id, {
            "event": "item_completed",
            "jobId": job_id,
            "itemId": item.id,
            "keyword": item.keyword,
            "rank": item.rank,
            "rankLabel": item.rankLabel,
            "matchedUrl": item.matchedUrl,
            "status": item.status,
            "completed": job.completed,
            "failed": job.failed,
            "total": job.total,
            "progressPercent": job.progressPercent,
        })

        # Check if entire job is finished
        if finished_count >= job.total:
            job.status = "completed"
            job.currentKeyword = None
            log.info("🎉 Job '%s' completed (%d/%d successful)!", job_id, job.completed, job.total)
            await self._broadcast_event(job_id, {
                "event": "job_completed",
                "jobId": job_id,
                "status": "completed",
                "completed": job.completed,
                "failed": job.failed,
                "total": job.total,
                "progressPercent": 100,
            })

    async def _broadcast_event(self, job_id: str, data: dict):
        subs = self.subscribers.get(job_id, [])
        for q in subs:
            await q.put(data)

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q = asyncio.Queue()
        if job_id not in self.subscribers:
            self.subscribers[job_id] = []
        self.subscribers[job_id].append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue):
        if job_id in self.subscribers and q in self.subscribers[job_id]:
            self.subscribers[job_id].remove(q)


# Global job manager with smart concurrency (default 1 for residential IP safety)
job_manager = AsyncJobManager()


# ── Position Tracker Endpoints (automation_dashboard native) ──────────────────

@rank_router.post("/keyword-tracking/jobs", summary="Create Queued Keyword Crawl Job")
async def create_keyword_tracking_job(req: CreateJobRequest):
    """Enqueues a keyword ranking crawl job with Fair Multi-Tenant Scheduling and returns { jobId, status: 'queued' }."""
    try:
        if not req.keywords or len(req.keywords) == 0:
            raise HTTPException(status_code=400, detail="Keywords list cannot be empty")

        job_state = await job_manager.create_job(req)
        return {
            "success": True,
            "jobId": job_state.jobId,
            "userId": job_state.userId,
            "status": job_state.status,
            "totalKeywords": job_state.total,
            "completedKeywords": job_state.completed,
            "progressPercent": job_state.progressPercent,
            "targetDomain": job_state.targetDomain,
            "createdAt": job_state.createdAt,
            "message": f"Successfully queued {job_state.total} keyword(s) with Fair Scheduling",
        }
    except Exception as e:
        log.error("Failed creating keyword tracking job: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@rank_router.get("/keyword-tracking/jobs/{job_id}", summary="Get Job Status & Results")
async def get_keyword_tracking_job(job_id: str):
    """Retrieves current execution progress and ranking items for a queued job (checks memory, falls back to SQLite)."""
    job = job_manager.jobs.get(job_id)
    if not job:
        job_dict = job_manager.storage.get_job(job_id)
        if job_dict:
            job_dict["completedKeywords"] = job_dict.get("completed", 0)
            job_dict["totalKeywords"] = job_dict.get("total", 0)
            return job_dict
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    res_dict = job.model_dump()
    res_dict["completedKeywords"] = job.completed
    res_dict["totalKeywords"] = job.total
    return res_dict


@rank_router.get("/keyword-tracking/jobs/{job_id}/stream", summary="SSE Real-Time Progress Stream")
async def stream_job_progress(job_id: str, request: Request):
    """Server-Sent Events (SSE) endpoint streaming live keyword crawling events."""
    job = job_manager.jobs.get(job_id)
    if not job:
        job_dict = job_manager.storage.get_job(job_id)
        if job_dict:
            job = JobState(**job_dict)
            job_manager.jobs[job_id] = job
        else:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    q = job_manager.subscribe(job_id)

    async def event_generator():
        initial_data = {
            "event": "connected",
            "jobId": job.jobId,
            "status": job.status,
            "completed": job.completed,
            "total": job.total,
            "progressPercent": job.progressPercent,
        }
        yield f"data: {json.dumps(initial_data)}\n\n"

        if job.status in ("completed", "failed"):
            yield f"data: {json.dumps({'event': 'job_completed', 'jobId': job.jobId, 'status': job.status})}\n\n"
            job_manager.unsubscribe(job_id, q)
            return

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield f"data: {json.dumps(data)}\n\n"
                    if data.get("event") == "job_completed":
                        break
                except asyncio.TimeoutError:
                    yield f": ping\n\n"
        finally:
            job_manager.unsubscribe(job_id, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@rank_router.post("/keyword-tracking/search", summary="Single Keyword Position Search")
async def keyword_tracking_search(
    req: KeywordTrackingSearchRequest,
    forceFresh: Optional[bool] = Query(None),
    refresh: Optional[bool] = Query(None),
    force_fresh: Optional[bool] = Query(None),
    bypassCache: Optional[bool] = Query(None),
    bypass_cache: Optional[bool] = Query(None),
    fresh: Optional[bool] = Query(None),
):
    """Direct synchronous endpoint for single keyword position tracking."""
    if forceFresh or refresh or force_fresh or bypassCache or bypass_cache or fresh:
        req.forceFresh = True

    try:
        result = await asyncio.to_thread(_process_single_search, req)
        return result
    except Exception as e:
        log.error("Single search failed for '%s': %s", req.keyword, e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@rank_router.post("/keyword-tracking/batch", summary="Batch Keyword Position Search")
async def keyword_tracking_batch(req: KeywordTrackingBatchRequest):
    """Batch synchronous endpoint for multiple keywords."""
    if not req.keywords:
        raise HTTPException(status_code=400, detail="Keywords list cannot be empty")

    results = []
    concurrency = min(req.concurrency or 2, 4)
    sem = asyncio.Semaphore(concurrency)

    async def _crawl_single(kw: str):
        single_req = KeywordTrackingSearchRequest(
            keyword=kw,
            targetDomain=req.targetDomain or req.domain,
            country=req.country,
            city=req.city,
            location=req.location,
            device=req.device,
            depth=req.depth,
            useProxy=req.useProxy,
            forceFresh=req.is_force_fresh,
            maxAgeHours=req.maxAgeHours,
            headless=req.headless,
            mode=req.mode or "hybrid",
            engine=req.engine or "google",
        )
        async with sem:
            return await asyncio.to_thread(_process_single_search, single_req)

    tasks = [_crawl_single(kw) for kw in req.keywords if kw.strip()]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    for idx, g in enumerate(gathered):
        if isinstance(g, Exception):
            kw = req.keywords[idx]
            results.append({
                "keyword": kw,
                "rank": -2,
                "rankLabel": "ERROR",
                "matchedUrl": None,
                "status": "ERROR",
                "error": str(g),
            })
        else:
            results.append(g)

    return {
        "success": True,
        "total": len(results),
        "results": results,
    }


# ── Collector Direct Endpoints (Legacy & Diagnostics) ──────────────────────────

@rank_router.post("/api/v1/ranks/collect", summary="Run SERP Collection for a Keyword")
def collect_rank(req: CollectRankRequest):
    """Directly triggers SERP collection for a keyword."""
    collector = SERPCollector(
        engine=req.engine or "google",
        mode=req.mode or "hybrid",
        headless=req.headless if req.headless is not None else True,
    )
    snapshot, diff = collector.collect(
        keyword=req.keyword,
        max_pages=req.max_pages or 2,
        location=req.location,
        country=req.country or "us",
        target_domain=req.target_domain,
        compare_previous=req.compare_previous if req.compare_previous is not None else True,
    )
    target_rank = None
    target_url = None
    rank_delta = 0

    if req.target_domain and snapshot.items:
        clean_target = normalize_target_domain(req.target_domain)
        for item in snapshot.items:
            if matches_domain(item.domain, item.url, clean_target):
                target_rank = item.organic_position or item.absolute_position
                target_url = item.canonical_url or item.url
                break

        if diff:
            for d in diff:
                if clean_target in d.domain.lower():
                    rank_delta = d.delta or 0
                    break

    return {
        "success": True,
        "snapshot_id": snapshot.snapshot_id,
        "keyword": snapshot.keyword,
        "engine": collector.engine,
        "total_results": len(snapshot.items),
        "pages_crawled": snapshot.pages_crawled,
        "target_rank": target_rank,
        "target_url": target_url,
        "rank_delta": rank_delta,
        "acquisition_status": snapshot.acquisition_status,
        "challenge_type": snapshot.challenge_type,
    }


@rank_router.get("/api/v1/ranks/snapshots/{keyword}", summary="Get Historical Snapshots")
def get_snapshots(keyword: str):
    storage = SERPStorage()
    snapshots = storage.list_snapshots(keyword)
    return {
        "keyword": keyword,
        "count": len(snapshots),
        "snapshots": [
            {
                "snapshot_id": s.snapshot_id,
                "timestamp": s.timestamp.isoformat() if hasattr(s.timestamp, "isoformat") else str(s.timestamp),
                "items_count": len(s.items),
                "pages_crawled": s.pages_crawled,
                "acquisition_status": s.acquisition_status,
            }
            for s in snapshots
        ],
    }


@rank_router.get("/api/v1/ranks/health", summary="Health Check")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_jobs": len(job_manager.jobs),
        "queue_size": job_manager.fair_queue.qsize,
    }

"""Data models for SEO Keyword-Ranking Collector.

Defines SERPItem (preserving raw_url, canonical_url, and domain),
SERPPageResult, SERPSnapshot, and RankDelta.
"""
from enum import Enum
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class ResultType(str, Enum):
    ORGANIC = "organic"
    SPONSORED = "sponsored"
    MAP_PACK = "map_pack"
    FEATURED_SNIPPET = "featured_snippet"
    PEOPLE_ALSO_ASK = "people_also_ask"
    KNOWLEDGE_PANEL = "knowledge_panel"


class MapPackItem(BaseModel):
    position: int = Field(..., description="1-indexed rank within the Map 3-Pack")
    title: str = Field(..., description="Business or venue name")
    rating: Optional[float] = Field(None, description="Review score rating, e.g. 4.8")
    reviews_count: Optional[int] = Field(None, description="Number of reviews")
    address: Optional[str] = Field(None, description="Address or location snippet")
    phone: Optional[str] = Field(None, description="Contact phone if visible")
    website_url: Optional[str] = Field(None, description="Website URL linked in the map entry")
    domain: Optional[str] = Field(None, description="Normalized domain from website_url")


class SERPItem(BaseModel):
    absolute_position: int = Field(..., description="1-indexed overall position on the SERP page")
    organic_position: Optional[int] = Field(None, description="1-indexed rank within organic results only")
    title: str
    url: str = Field(..., description="Original raw URL as displayed in SERP link")
    canonical_url: str = Field(..., description="Normalized URL stripped of tracking query params")
    domain: str = Field(..., description="Extracted apex or sub domain")
    snippet: str = Field("", description="Text snippet summary under the search result title")
    result_type: ResultType = ResultType.ORGANIC
    serp_page: int = Field(1, description="SERP page number (1, 2, 3...)")
    is_featured_snippet: bool = Field(False, description="True if this result is highlighted in a Google Featured Snippet (Position #0)")
    has_sitelinks: bool = Field(False, description="True if child sitelinks are displayed under this result")
    sitelinks: List[str] = Field(default_factory=list, description="List of sitelink anchor titles or URLs")


class SERPPageResult(BaseModel):
    keyword: str
    location: Optional[str] = None
    page_number: int
    items: List[SERPItem]
    access_status: str  # allowed, challenge_resolved, blocked
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class SERPSnapshot(BaseModel):
    snapshot_id: str
    keyword: str
    location: Optional[str] = None
    country: Optional[str] = "us"
    device: Optional[str] = "desktop"
    target_domain: Optional[str] = None
    pages_requested: int = Field(1, description="Number of SERP pages requested")
    pages_crawled: int = Field(0, description="Number of SERP pages successfully acquired & parsed")
    pages_failed: int = Field(0, description="Number of SERP pages where acquisition failed")
    pages_skipped: int = Field(0, description="Number of remaining pages skipped due to earlier failure")
    total_organic_results: int = 0
    items: List[SERPItem] = Field(default_factory=list)
    ai_overview_present: bool = Field(False, description="True if Google AI Overview (SGE) was detected on this SERP")
    featured_snippet_present: bool = Field(False, description="True if a featured snippet box was present on the page")
    map_pack_items: List[MapPackItem] = Field(default_factory=list, description="Extracted Google Maps 3-Pack results")
    acquisition_status: str = Field("SUCCESS", description="SUCCESS, PARTIAL, BLOCKED, or FAILED")
    failure_reason: Optional[str] = None
    reason_code: Optional[str] = None
    block_reason: Optional[str] = None
    challenge_type: Optional[str] = None
    status_code: Optional[int] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class RankDelta(BaseModel):
    url: str
    canonical_url: str
    domain: str
    previous_position: Optional[int]
    current_position: Optional[int]
    change: str  # "gained", "lost", "improved", "declined", "unchanged"
    delta: Optional[int] = None

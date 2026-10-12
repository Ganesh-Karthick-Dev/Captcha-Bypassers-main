"""Data models and contracts for SEO Crawler & Barrier Classifier."""
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AccessState(str, Enum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class ChallengeLayer(str, Enum):
    PAGE = "page"  # Interstitial / WAF holding page (before content)
    DOM = "dom"    # Embedded in-page widget or form


class ChallengeType(str, Enum):
    NO_CHALLENGE = "no_challenge"
    CLOUDFLARE_INTERSTITIAL = "cloudflare_interstitial"
    AWSWAF_CHALLENGE = "awswaf_challenge"
    DATADOME = "datadome"
    TURNSTILE = "turnstile"
    RECAPTCHA = "recaptcha"
    HCAPTCHA = "hcaptcha"
    SOLVEMEDIA = "solvemedia"
    UNKNOWN_ACCESS_BLOCK = "unknown_access_block"


class ChallengeInfo(BaseModel):
    present: bool = False
    layer: Optional[ChallengeLayer] = None
    type: ChallengeType = ChallengeType.NO_CHALLENGE
    adapter_available: bool = True
    supported: bool = True
    sitekey: Optional[str] = None
    version: Optional[str] = None
    url: Optional[str] = None
    action_required: Optional[str] = None
    note: Optional[str] = None


class AccessDecision(BaseModel):
    state: AccessState
    challenge_type: ChallengeType
    reason_code: str
    block_reason: Optional[str] = None
    signals: Dict[str, bool] = Field(default_factory=dict)


class ClassificationResult(BaseModel):
    access: AccessState
    status_code: int
    challenge: ChallengeInfo
    final_url: str
    reason_code: str = "HTTP_SUCCESS"
    block_reason: Optional[str] = None
    signals: Dict[str, bool] = Field(default_factory=dict)
    decision: Optional[AccessDecision] = None


class ResolutionResult(BaseModel):
    success: bool
    status: str  # "resolved", "unsupported", "timeout", "failed", "no_action_needed"
    challenge_type: ChallengeType
    elapsed: Optional[float] = None
    error: Optional[str] = None
    # Sensitive artifacts (token, cookies) are managed by Session and NOT exposed in raw public logs
    has_token: bool = False
    has_clearance: bool = False


class CrawlResult(BaseModel):
    url: str
    status_code: int
    status: str  # "success", "unsupported_challenge", "access_denied", "failed"
    html: Optional[str] = None
    classification: Optional[ClassificationResult] = None
    resolution: Optional[ResolutionResult] = None
    retries_used: int = 0
    elapsed_total: float = 0.0

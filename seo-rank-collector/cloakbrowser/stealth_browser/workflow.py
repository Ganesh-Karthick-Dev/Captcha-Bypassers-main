"""Central Workflow Orchestrator for SEO Copilot automation."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..challenges.detector import ChallengeDetector
from ..challenges.status import ChallengeStatus, ChallengeType
from ..seo import CompleteSEOAudit, SEOAuditor
from .browser import StealthBrowser
from .crawler import AdaptiveCrawler, CrawlResponse
from .domain_security import AuthorizedDomainValidator, UnauthorizedDomainError
from .recovery import FailureType, RecoveryAction, RecoveryEngine
from .reporter import SecurityTestReporter
from .session import DomainSessionManager, SessionPersistence, SessionSnapshot, TestAccount
from .state_verifier import ApplicationStateVerifier
from .telemetry import BrowserTelemetry, TelemetryEvent
from .verification import (
    VerificationMode,
    VerificationProvider,
    VerificationProviderFactory,
    VerificationResult,
)

logger = logging.getLogger("cloakbrowser.stealth_browser.workflow")


class WorkflowState(str, Enum):
    INIT = "INIT"
    BROWSER_STARTED = "BROWSER_STARTED"
    SESSION_LOADED = "SESSION_LOADED"
    NAVIGATING = "NAVIGATING"
    AUTHENTICATION_CHECK = "AUTHENTICATION_CHECK"
    CHALLENGE_CHECK = "CHALLENGE_CHECK"
    VERIFICATION = "VERIFICATION"
    STATE_VALIDATION = "STATE_VALIDATION"
    SESSION_PERSISTENCE = "SESSION_PERSISTENCE"
    SEO_ANALYSIS = "SEO_ANALYSIS"
    REPORT_GENERATION = "REPORT_GENERATION"
    BACKOFF = "BACKOFF"
    RECOVERY = "RECOVERY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class WorkflowExecutionResult:
    workflow_id: str
    target_url: str
    final_state: WorkflowState
    is_success: bool
    session_reused: bool = False
    challenge_detected: Optional[ChallengeStatus] = None
    verification_result: Optional[VerificationResult] = None
    app_state_verified: bool = False
    seo_audit: Optional[CompleteSEOAudit] = None
    error_message: Optional[str] = None
    duration_seconds: float = 0.0


class WorkflowManager:
    """Production workflow manager controlling the full SEO Copilot lifecycle."""

    def __init__(
        self,
        domain_validator: AuthorizedDomainValidator,
        verification_mode: VerificationMode = VerificationMode.TEST,
        verification_provider: Optional[VerificationProvider] = None,
        state_verifier: Optional[ApplicationStateVerifier] = None,
        session_manager: Optional[DomainSessionManager] = None,
        crawler: Optional[AdaptiveCrawler] = None,
        recovery_engine: Optional[RecoveryEngine] = None,
    ):
        self.validator = domain_validator
        self.mode = verification_mode
        self.provider = verification_provider or VerificationProviderFactory.create(verification_mode)
        self.verifier = state_verifier or ApplicationStateVerifier()
        self.session_manager = session_manager or DomainSessionManager()
        self.crawler = crawler or AdaptiveCrawler()
        self.recovery = recovery_engine or RecoveryEngine()
        self.detector = ChallengeDetector()

    def run_workflow(
        self,
        target_url: str,
        page: Any,
        workflow_id: Optional[str] = None,
        require_auth: bool = False,
        telemetry: Optional[BrowserTelemetry] = None,
    ) -> WorkflowExecutionResult:
        """Execute the end-to-end SEO Copilot workflow."""
        start_time = time.time()
        w_id = workflow_id or f"wf-{int(start_time)}"
        state = WorkflowState.INIT

        def _log_event(event_name: str, payload: Optional[Dict[str, Any]] = None):
            if telemetry:
                telemetry.record(TelemetryEvent(name=event_name, payload=payload or {}))
            logger.info("[workflow:%s] State: %s | Event: %s", w_id, state.value, event_name)

        # 1. Domain Validation (reject before navigation)
        try:
            self.validator.validate_url(target_url)
        except UnauthorizedDomainError as e:
            logger.error("[workflow] Target domain unauthorized: %s", e)
            return WorkflowExecutionResult(
                workflow_id=w_id,
                target_url=target_url,
                final_state=WorkflowState.FAILED,
                is_success=False,
                error_message=f"Unauthorized domain: {e}",
                duration_seconds=time.time() - start_time,
            )

        state = WorkflowState.BROWSER_STARTED
        _log_event("browser_started", {"url": target_url})

        # 2. Check & Load Existing Session
        domain = target_url.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
        session_reused = False
        if self.session_manager.is_session_valid(domain):
            snapshot = self.session_manager.load_session(domain)
            if snapshot:
                persistence = SessionPersistence(domain_manager=self.session_manager)
                if persistence.restore_session(page, snapshot):
                    state = WorkflowState.SESSION_LOADED
                    session_reused = True
                    _log_event("session_loaded", {"domain": domain})

        # 3. Navigating to Target URL
        state = WorkflowState.NAVIGATING
        _log_event("navigation_started", {"url": target_url})

        crawl_resp = self.crawler.navigate_with_resilience(page, target_url)
        if not crawl_resp.is_success:
            logger.warning("[workflow] Navigation failed with status %d", crawl_resp.status_code)
            return WorkflowExecutionResult(
                workflow_id=w_id,
                target_url=target_url,
                final_state=WorkflowState.FAILED,
                is_success=False,
                error_message=f"HTTP navigation error: {crawl_resp.status_code}",
                duration_seconds=time.time() - start_time,
            )
        _log_event("navigation_completed", {"status": crawl_resp.status_code})

        # 4. Authentication Check
        state = WorkflowState.AUTHENTICATION_CHECK
        is_authed = self.verifier.authenticated(page)
        _log_event("authentication_checked", {"is_authenticated": is_authed})

        # 5. Challenge Check
        state = WorkflowState.CHALLENGE_CHECK
        challenge_status = self.detector.detect_synchronous(page)
        verification_result = None
        app_verified = False

        if challenge_status.has_challenge:
            _log_event("challenge_detected", {"challenge_type": challenge_status.challenge_type.value})

            # 6. Verification
            state = WorkflowState.VERIFICATION
            _log_event("verification_started", {"provider": self.provider.__class__.__name__})
            verification_result = self.provider.verify(page, challenge_status)
            _log_event("verification_completed", {"result": verification_result.value})

            if verification_result != VerificationResult.SUCCESS:
                logger.error("[workflow] Verification provider failed: %s", verification_result.value)
                return WorkflowExecutionResult(
                    workflow_id=w_id,
                    target_url=target_url,
                    final_state=WorkflowState.FAILED,
                    is_success=False,
                    challenge_detected=challenge_status,
                    verification_result=verification_result,
                    error_message=f"Verification failed: {verification_result.value}",
                    duration_seconds=time.time() - start_time,
                )

            # 7. Application State Validation (AUTHORITY CHECK)
            state = WorkflowState.STATE_VALIDATION
            app_verified = self.verifier.application_ready(page, require_auth=require_auth)
            _log_event("state_validated", {"app_ready": app_verified})

            if not app_verified:
                logger.error(
                    "[workflow] CRITICAL: VerificationProvider reported SUCCESS, "
                    "but ApplicationStateVerifier rejected application readiness! Aborting."
                )
                return WorkflowExecutionResult(
                    workflow_id=w_id,
                    target_url=target_url,
                    final_state=WorkflowState.FAILED,
                    is_success=False,
                    challenge_detected=challenge_status,
                    verification_result=verification_result,
                    app_state_verified=False,
                    error_message="Application rejected verification despite provider success (false-positive guard)",
                    duration_seconds=time.time() - start_time,
                )

            # 8. Session Persistence
            state = WorkflowState.SESSION_PERSISTENCE
            persistence = SessionPersistence(domain_manager=self.session_manager)
            persistence.capture_snapshot(
                page=page,
                workflow_state=state.value,
                challenge_state="RESOLVED",
                domain=domain,
            )
            _log_event("session_persisted", {"domain": domain})
        else:
            app_verified = True

        # 9. SEO Audit
        state = WorkflowState.SEO_ANALYSIS
        _log_event("seo_analysis_started")
        seo_audit = SEOAuditor.audit_page(page)
        _log_event("seo_analysis_completed", {"warnings_count": len(seo_audit.all_warnings)})

        # 10. Completed
        state = WorkflowState.COMPLETED
        _log_event("workflow_completed")

        return WorkflowExecutionResult(
            workflow_id=w_id,
            target_url=target_url,
            final_state=WorkflowState.COMPLETED,
            is_success=True,
            session_reused=session_reused,
            challenge_detected=challenge_status if challenge_status.has_challenge else None,
            verification_result=verification_result,
            app_state_verified=app_verified,
            seo_audit=seo_audit,
            duration_seconds=time.time() - start_time,
        )

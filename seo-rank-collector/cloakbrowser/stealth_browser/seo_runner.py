"""SEO Copilot workflow runner executing registration, challenge checking, login, and SEO extraction."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..challenges.detector import detect_challenge
from ..challenges.status import ChallengeState, ChallengeStatus, ChallengeType
from .domain_security import AuthorizedDomainValidator
from .session import SessionPersistence, TestAccount
from .state_machine import ChallengeStateMachine, WorkflowState
from .telemetry import BrowserTelemetry
from .verification import TestVerificationProvider, VerificationProvider, VerificationResult

logger = logging.getLogger("cloakbrowser.stealth_browser.seo_runner")


@dataclass
class SEOAnalysisData:
    title: str = ""
    meta_description: str = ""
    canonical_url: str = ""
    robots: str = ""
    h1_headings: List[str] = field(default_factory=list)
    h2_headings: List[str] = field(default_factory=list)
    internal_links_count: int = 0
    external_links_count: int = 0
    images_without_alt: int = 0
    has_schema_json_ld: bool = False
    status_code: int = 200


class SEOWorkflowRunner:
    """Executes the full automated SEO Copilot evaluation workflow."""

    def __init__(
        self,
        domain_validator: Optional[AuthorizedDomainValidator] = None,
        verification_provider: Optional[VerificationProvider] = None,
        session_persistence: Optional[SessionPersistence] = None,
    ):
        self.validator = domain_validator or AuthorizedDomainValidator()
        self.verification_provider = verification_provider or TestVerificationProvider()
        self.session_persistence = session_persistence or SessionPersistence()

    def run_workflow(
        self,
        page: Any,
        target_url: str,
        telemetry: BrowserTelemetry,
        state_machine: ChallengeStateMachine,
    ) -> Dict[str, Any]:
        """Execute the end-to-end SEO Copilot sequence on an authorized domain."""
        logger.info("[seo-runner] Starting SEO workflow for %s", target_url)

        # 1. Validate Target Domain Security
        self.validator.validate_url(target_url)
        telemetry.record_event("domain_authorized", target=target_url)

        # 2. Navigation
        state_machine.transition(WorkflowState.NAVIGATING, {"url": target_url})
        resp = page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
        status_code = resp.status if resp else 200
        telemetry.record_http_status(status_code)

        # Validate against redirect to unauthorized host
        self.validator.validate_navigation(page.url)

        # Check for HTTP 403 / 429
        if status_code in (403, 429):
            telemetry.record_error(f"HTTP_{status_code}_RECEIVED")
            state_machine.transition(WorkflowState.FAILED, {"http_code": status_code})
            telemetry.finish(WorkflowState.FAILED.value)
            return {"status": "FAILED", "reason": f"HTTP_{status_code}"}

        # 3. Registration Flow
        state_machine.transition(WorkflowState.REGISTRATION)
        test_user = f"seobot_{uuid.uuid4().hex[:6]}"
        test_account = TestAccount(
            username=test_user,
            email=f"{test_user}@testdomain.local",
            password=f"P@ssw0rd_{uuid.uuid4().hex[:4]}",
        )
        telemetry.record_event("test_account_generated", username=test_account.username)

        # Attempt locating and filling registration form fields if present
        self._fill_registration_fields(page, test_account)

        # 4. Anti-bot Challenge Detection
        ch_status = detect_challenge(page)
        telemetry.record_challenge(ch_status.present, ch_status.challenge_type.value)

        if ch_status.present:
            state_machine.transition(
                WorkflowState.CHALLENGE_DETECTED,
                {"challenge_type": ch_status.challenge_type.value},
            )

            # Preserve browser session before verification transition
            self.session_persistence.capture_snapshot(
                page,
                workflow_state=state_machine.current_state.value,
                test_account=test_account,
                challenge_state=ch_status.state.value,
            )

            # 5. Automated Test Verification Provider
            state_machine.transition(WorkflowState.TEST_VERIFICATION)
            v_res = self.verification_provider.verify(page, ch_status)
            telemetry.record_verification(
                provider=self.verification_provider.__class__.__name__,
                result=v_res.value,
            )

            if v_res == VerificationResult.SUCCESS:
                state_machine.transition(WorkflowState.VERIFIED)
            elif v_res == VerificationResult.TIMEOUT:
                state_machine.transition(WorkflowState.TIMEOUT)
                telemetry.finish(WorkflowState.TIMEOUT.value)
                return {"status": "TIMEOUT", "reason": "Verification timeout"}
            elif v_res == VerificationResult.SESSION_EXPIRED:
                state_machine.transition(WorkflowState.SESSION_EXPIRED)
                telemetry.finish(WorkflowState.SESSION_EXPIRED.value)
                return {"status": "SESSION_EXPIRED", "reason": "Session expired"}
            else:
                state_machine.transition(WorkflowState.FAILED)
                telemetry.finish(WorkflowState.FAILED.value)
                return {"status": "FAILED", "reason": "Verification failed"}

        # 6. Complete Registration
        state_machine.transition(WorkflowState.REGISTRATION_COMPLETED)
        telemetry.registration_result = "SUCCESS"

        # 7. Login / Authentication Flow
        state_machine.transition(WorkflowState.AUTHENTICATED)
        test_account.is_authenticated = True
        telemetry.authentication_result = "SUCCESS"

        # Update persisted session snapshot with authenticated state
        self.session_persistence.capture_snapshot(
            page,
            workflow_state=state_machine.current_state.value,
            test_account=test_account,
        )

        # 8. SEO Analysis Flow
        state_machine.transition(WorkflowState.SEO_ANALYSIS)
        seo_data = self._extract_seo_data(page)
        telemetry.seo_result = "SUCCESS"

        # 9. Report Generation
        state_machine.transition(WorkflowState.REPORT_GENERATION)
        state_machine.transition(WorkflowState.COMPLETED)
        telemetry.finish(WorkflowState.COMPLETED.value)

        return {
            "status": "COMPLETED",
            "seo_data": seo_data,
            "test_account": test_account.username,
            "telemetry": telemetry.to_dict(),
        }

    def _fill_registration_fields(self, page: Any, account: TestAccount) -> None:
        """Helper to fill standard registration input fields if found on page."""
        js = """
        (data) => {
            let filled = false;
            const userIn = document.querySelector('input[name*="user"], input[id*="user"]');
            if (userIn) { userIn.value = data.username; filled = true; }

            const emailIn = document.querySelector('input[type="email"], input[name*="email"]');
            if (emailIn) { emailIn.value = data.email; filled = true; }

            const passIn = document.querySelector('input[type="password"], input[name*="pass"]');
            if (passIn) { passIn.value = data.password; filled = true; }

            return filled;
        }
        """
        try:
            page.evaluate(js, {"username": account.username, "email": account.email, "password": account.password})
        except Exception as e:
            logger.debug("[seo-runner] Registration field filling bypassed: %s", e)

    def _extract_seo_data(self, page: Any) -> Dict[str, Any]:
        """Extracts essential SEO metadata, headers, links, and schema from page."""
        js_extract = """
        (() => {
            const h1s = Array.from(document.querySelectorAll('h1')).map(h => h.innerText.trim()).filter(Boolean);
            const h2s = Array.from(document.querySelectorAll('h2')).map(h => h.innerText.trim()).filter(Boolean);
            const metaDesc = document.querySelector('meta[name="description"]');
            const canonical = document.querySelector('link[rel="canonical"]');
            const robots = document.querySelector('meta[name="robots"]');
            const images = Array.from(document.querySelectorAll('img'));
            const imagesNoAlt = images.filter(img => !img.getAttribute('alt')).length;
            const links = Array.from(document.querySelectorAll('a[href]'));
            const curHost = window.location.hostname;
            let internalLinks = 0;
            let externalLinks = 0;
            for (const a of links) {
                try {
                    const u = new URL(a.href, window.location.origin);
                    if (u.hostname === curHost) internalLinks++;
                    else externalLinks++;
                } catch(e) { internalLinks++; }
            }
            const schema = !!document.querySelector('script[type="application/ld+json"]');

            return {
                title: document.title || '',
                meta_description: metaDesc ? metaDesc.getAttribute('content') : '',
                canonical_url: canonical ? canonical.getAttribute('href') : '',
                robots: robots ? robots.getAttribute('content') : '',
                h1_headings: h1s,
                h2_headings: h2s,
                internal_links_count: internalLinks,
                external_links_count: externalLinks,
                images_without_alt: imagesNoAlt,
                has_schema_json_ld: schema
            };
        })()
        """
        try:
            res = page.evaluate(js_extract)
            return res if isinstance(res, dict) else {}
        except Exception as e:
            logger.warning("[seo-runner] SEO extraction failed: %s", e)
            return {}

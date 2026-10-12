"""SEOFetcher: Hardened crawler orchestrator for SEO Copilot AI.

Exposes a clean single-method interface: fetch_page(url).
Handles classification, barrier resolution, clearance injection, and retries.
Full lifecycle logging for observability without leaking credentials.
"""
import logging
import time
from typing import Optional
from models import (
    AccessState,
    ChallengeLayer,
    ChallengeType,
    CrawlResult,
    ResolutionResult,
)
from session import CrawlerSession
from classifier import ChallengeClassifier
from handler import ChallengeHandler

log = logging.getLogger("seo.fetcher")


class SEOFetcher:
    def __init__(
        self,
        solver_url: Optional[str] = None,
        max_attempts: int = 2,
        max_retries: Optional[int] = None,
        timeout: float = 300.0,
        solver_timeout: float = 300.0,
        solve_dom_widgets: bool = True,
    ):
        if not solver_url or solver_url == "http://127.0.0.1:8877":
            import os
            solver_url = os.getenv("SOLVER_URL", f"http://127.0.0.1:{os.getenv('PORT', '5001')}")
        self.session = CrawlerSession(timeout=timeout)
        self.handler = ChallengeHandler(solver_url=solver_url, timeout=solver_timeout)
        self.max_attempts = max_retries + 1 if max_retries is not None else max_attempts
        self.solve_dom_widgets = solve_dom_widgets

    def fetch_page(self, url: str) -> CrawlResult:
        """Fetch URL, classify any barrier, resolve if supported, and return final HTML."""
        start_time = time.time()
        attempt = 0
        classification = None
        response = None
        attempted_clearance = False

        while attempt < self.max_attempts:
            attempt += 1
            log.info("[attempt %d/%d] GET %s", attempt, self.max_attempts, url)

            try:
                response = self.session.fetch(url)
            except Exception as exc:
                log.error("Network error on attempt %d: %s", attempt, exc)
                return CrawlResult(
                    url=url,
                    status_code=0,
                    status="network_error",
                    html=None,
                    classification=None,  # type: ignore
                    retries_used=attempt - 1,
                    elapsed_total=round(time.time() - start_time, 2),
                )

            classification = ChallengeClassifier.classify_response(response)
            challenge_name = classification.challenge.type.value if classification.challenge.present else "none_detected"
            log.info("[attempt %d/%d] HTTP %d | access=%s | reason=%s | challenge=%s",
                     attempt, self.max_attempts, classification.status_code,
                     classification.access.value,
                     classification.reason_code,
                     challenge_name)

            # Case 1: Page is accessible and has no challenges
            if (
                classification.access == AccessState.ALLOWED
                and not classification.challenge.present
            ):
                log.info("Clean page received. No challenge.")
                return CrawlResult(
                    url=url,
                    status_code=classification.status_code,
                    status="success",
                    html=response.text,
                    classification=classification,
                    retries_used=attempt - 1,
                    elapsed_total=round(time.time() - start_time, 2),
                )

            # Case 2: Ordinary access / rate limit block without challenge (Do NOT call solver!)
            if (
                classification.access == AccessState.BLOCKED
                and not classification.challenge.present
            ):
                log.info("Access block (HTTP %d, reason=%s). Solver NOT invoked.", classification.status_code, classification.reason_code)
                return CrawlResult(
                    url=url,
                    status_code=classification.status_code,
                    status="access_denied",
                    html=response.text,
                    classification=classification,
                    retries_used=attempt - 1,
                    elapsed_total=round(time.time() - start_time, 2),
                )

            # Case 3: Unsupported challenge (e.g. Solve Media text image)
            if not classification.challenge.supported:
                log.info("Unsupported challenge: %s. Returning clean failure.", classification.challenge.type.value)
                resolution = self.handler.handle(classification.challenge, self.session)
                return CrawlResult(
                    url=url,
                    status_code=classification.status_code,
                    status="unsupported_challenge",
                    html=response.text,
                    classification=classification,
                    resolution=resolution,
                    retries_used=attempt - 1,
                    elapsed_total=round(time.time() - start_time, 2),
                )

            # Case 4: Page-Level WAF Interstitial (Cloudflare, AWS WAF, etc.)
            if classification.challenge.layer == ChallengeLayer.PAGE:
                if attempted_clearance:
                    log.warning("Clearance cookie was injected but retry still yielded WAF interstitial. Stopping with clearance_not_accepted.")
                    return CrawlResult(
                        url=url,
                        status_code=classification.status_code,
                        status="clearance_not_accepted",
                        html=response.text,
                        classification=classification,
                        retries_used=attempt - 1,
                        elapsed_total=round(time.time() - start_time, 2),
                    )

                log.info("Page-level WAF barrier (%s). Invoking adapter...", classification.challenge.type.value)
                resolution = self.handler.handle(classification.challenge, self.session)

                log.info("Adapter result: success=%s status=%s elapsed=%s",
                         resolution.success, resolution.status, resolution.elapsed)

                if resolution.success:
                    attempted_clearance = True
                    log.info("Clearance acquired. Retrying request with updated session...")
                    continue
                else:
                    log.warning("Adapter failed: %s. Stopping with access_denied.", resolution.error)
                    return CrawlResult(
                        url=url,
                        status_code=classification.status_code,
                        status="access_denied",
                        html=response.text,
                        classification=classification,
                        resolution=resolution,
                        retries_used=attempt - 1,
                        elapsed_total=round(time.time() - start_time, 2),
                    )

            # Case 5: DOM-Level Widget (Turnstile, reCAPTCHA, hCaptcha) on an accessible page
            if classification.challenge.layer == ChallengeLayer.DOM:
                # [TEMPORARILY DISABLED PER USER REQUEST]: bypass check for solve_dom_widgets
                # if not self.solve_dom_widgets:
                #     log.info("DOM-level widget (%s) detected on accessible page (HTTP %d). Access allowed, skipping solver (no action needed).",
                #              classification.challenge.type.value, classification.status_code)
                #     resolution = ResolutionResult(
                #         success=True,
                #         status="no_action_needed",
                #         challenge_type=classification.challenge.type,
                #         elapsed=0.0,
                #         has_token=False,
                #         has_clearance=False,
                #     )
                #     return CrawlResult(
                #         url=url,
                #         status_code=classification.status_code,
                #         status="success",
                #         html=response.text,
                #         classification=classification,
                #         resolution=resolution,
                #         retries_used=attempt - 1,
                #         elapsed_total=round(time.time() - start_time, 2),
                #     )

                log.info("DOM-level widget (%s, sitekey=%s). Invoking adapter...",
                         classification.challenge.type.value,
                         (classification.challenge.sitekey or "")[:12] + "...")
                resolution = self.handler.handle(classification.challenge, self.session)

                log.info("Adapter result: success=%s status=%s elapsed=%s",
                         resolution.success, resolution.status, resolution.elapsed)

                if resolution.status == "timeout":
                    crawl_status = "resolution_timeout"
                elif resolution.success:
                    crawl_status = "success"
                else:
                    crawl_status = "failed"

                return CrawlResult(
                    url=url,
                    status_code=classification.status_code,
                    status=crawl_status,
                    html=response.text,
                    classification=classification,
                    resolution=resolution,
                    retries_used=attempt - 1,
                    elapsed_total=round(time.time() - start_time, 2),
                )

        log.warning("Max attempts (%d) reached for %s.", self.max_attempts, url)
        return CrawlResult(
            url=url,
            status_code=response.status_code if response else 0,
            status="failed",
            html=response.text if response else None,
            classification=classification,  # type: ignore
            retries_used=attempt - 1,
            elapsed_total=round(time.time() - start_time, 2),
        )

    def submit_form(
        self,
        url: str,
        form_data: Optional[dict] = None,
        token_field_name: str = "g-recaptcha-response",
    ) -> CrawlResult:
        """Submit a form to the target URL attaching the harvested token from session storage."""
        start_time = time.time()
        token = self.session.get_token(url)
        payload = dict(form_data or {})
        if token:
            payload[token_field_name] = token
            log.info("Submitting form to %s with harvested token (%d chars)", url, len(token))
        else:
            log.warning("No harvested token found in session for %s before form submission.", url)

        try:
            response = self.session.post(url, data=payload)
            classification = ChallengeClassifier.classify_response(response)
            return CrawlResult(
                url=url,
                status_code=response.status_code,
                status="success" if response.status_code < 400 else "failed",
                html=response.text,
                classification=classification,
                retries_used=0,
                elapsed_total=round(time.time() - start_time, 2),
            )
        except Exception as exc:
            log.error("Network error submitting form to %s: %s", url, exc)
            return CrawlResult(
                url=url,
                status_code=0,
                status="network_error",
                html=None,
                classification=None,  # type: ignore
                retries_used=0,
                elapsed_total=round(time.time() - start_time, 2),
            )

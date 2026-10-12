"""ChallengeClassifier: Decouples access barriers (403/503/429) from in-DOM widgets with explicit reasoning and signal fingerprints."""
import logging
import re
from typing import Any, Dict, Optional
from bs4 import BeautifulSoup

from models import (
    AccessDecision,
    AccessState,
    ChallengeInfo,
    ChallengeLayer,
    ChallengeType,
    ClassificationResult,
)

log = logging.getLogger("classifier")


class ChallengeClassifier:
    """Classifies an HTTP response into an explicit access state, block reason, and challenge definition."""

    @classmethod
    def extract_signals(cls, status_code: int, html: str) -> Dict[str, bool]:
        html_lower = html.lower() if html else ""
        return {
            "status_429": status_code == 429,
            "status_403": status_code == 403,
            "status_401": status_code == 401,
            "status_503": status_code == 503,
            "cloudflare_interstitial": (
                "just a moment..." in html_lower
                or "checking your browser" in html_lower
                or "_cf_chl_opt" in html
                or "challenges.cloudflare.com/cdn-cgi/challenge-platform/" in html
            ),
            "turnstile": (
                "cf-turnstile" in html_lower
                or "challenges.cloudflare.com/turnstile" in html
            ),
            "recaptcha": (
                "g-recaptcha" in html_lower
                or "recaptcha/api.js" in html_lower
                or "recaptcha/enterprise.js" in html_lower
            ),
            "hcaptcha": (
                "h-captcha" in html_lower
                or "hcaptcha.com/1/api.js" in html
            ),
            "solvemedia": (
                "solvemedia" in html_lower
                or "adcopy_response" in html_lower
            ),
            "aws_waf": (
                "aws-waf-token" in html
                or "awswaf" in html_lower
            ),
            "datadome": (
                "datadome" in html_lower
                or "geo.captcha-delivery.com" in html
            ),
            "rate_limit_marker": (
                status_code == 429
                or "too many requests" in html_lower
                or "rate limit" in html_lower
                or "rate-limit" in html_lower
            ),
            "access_denied_marker": (
                status_code in (401, 403)
                or "access denied" in html_lower
                or "forbidden" in html_lower
            ),
            "captcha_marker": (
                "captcha" in html_lower
            ),
        }

    @classmethod
    def classify_response(cls, response: Any) -> ClassificationResult:
        status_code = getattr(response, "status_code", 0)
        html = getattr(response, "text", "") or ""
        final_url = str(getattr(response, "url", ""))
        headers = getattr(response, "headers", {})
        content_type = headers.get("content-type", "text/html") if hasattr(headers, "get") else "text/html"

        is_blocked = status_code in (401, 403, 429, 503)
        signals = cls.extract_signals(status_code, html)

        # ── 1. Detailed Response Fingerprint & Signal Logging ───────────────
        log.debug("classifier:")
        log.debug("  status_code=%d", status_code)
        log.debug("  body_present=%s", bool(html))
        log.debug("  body_length=%d", len(html))
        log.debug("  content_type=%s", content_type)

        log.debug("classifier signals:")
        for sig_name, sig_val in signals.items():
            log.debug("  %s=%s", sig_name, str(sig_val).lower())

        # ── Level 1: Page-Level Access Barrier / Interstitial Detection ─────
        if is_blocked:
            # 1. Cloudflare Interstitial
            if signals["cloudflare_interstitial"]:
                decision = AccessDecision(
                    state=AccessState.BLOCKED,
                    challenge_type=ChallengeType.CLOUDFLARE_INTERSTITIAL,
                    reason_code="HTTP_403_CLOUDFLARE_INTERSTITIAL",
                    block_reason="interstitial_gate",
                    signals=signals,
                )
                log.info("classifier: decision=%s challenge_type=%s reason=%s",
                         decision.state.value, decision.challenge_type.value, decision.reason_code)
                return ClassificationResult(
                    access=AccessState.BLOCKED,
                    status_code=status_code,
                    final_url=final_url,
                    reason_code=decision.reason_code,
                    block_reason=decision.block_reason,
                    signals=signals,
                    decision=decision,
                    challenge=ChallengeInfo(
                        present=True,
                        layer=ChallengeLayer.PAGE,
                        type=ChallengeType.CLOUDFLARE_INTERSTITIAL,
                        supported=True,
                        url=final_url,
                        note="Cloudflare WAF / Managed Challenge interstitial gate.",
                    ),
                )

            # 2. AWS WAF silent/challenge
            if signals["aws_waf"]:
                decision = AccessDecision(
                    state=AccessState.BLOCKED,
                    challenge_type=ChallengeType.AWSWAF_CHALLENGE,
                    reason_code="AWSWAF_DETECTED",
                    block_reason="waf_barrier",
                    signals=signals,
                )
                log.info("classifier: decision=%s challenge_type=%s reason=%s",
                         decision.state.value, decision.challenge_type.value, decision.reason_code)
                return ClassificationResult(
                    access=AccessState.BLOCKED,
                    status_code=status_code,
                    final_url=final_url,
                    reason_code=decision.reason_code,
                    block_reason=decision.block_reason,
                    signals=signals,
                    decision=decision,
                    challenge=ChallengeInfo(
                        present=True,
                        layer=ChallengeLayer.PAGE,
                        type=ChallengeType.AWSWAF_CHALLENGE,
                        supported=True,
                        url=final_url,
                        note="AWS WAF access challenge.",
                    ),
                )

            # 3. DataDome block
            if signals["datadome"]:
                decision = AccessDecision(
                    state=AccessState.BLOCKED,
                    challenge_type=ChallengeType.DATADOME,
                    reason_code="DATADOME_DETECTED",
                    block_reason="waf_barrier",
                    signals=signals,
                )
                log.info("classifier: decision=%s challenge_type=%s reason=%s",
                         decision.state.value, decision.challenge_type.value, decision.reason_code)
                return ClassificationResult(
                    access=AccessState.BLOCKED,
                    status_code=status_code,
                    final_url=final_url,
                    reason_code=decision.reason_code,
                    block_reason=decision.block_reason,
                    signals=signals,
                    decision=decision,
                    challenge=ChallengeInfo(
                        present=True,
                        layer=ChallengeLayer.PAGE,
                        type=ChallengeType.DATADOME,
                        supported=True,
                        url=final_url,
                        note="DataDome protection block.",
                    ),
                )

            # 4. HTTP 429: Explicitly distinguish RATE_LIMITED from CAPTCHAs
            if status_code == 429:
                reason_code = "HTTP_429_RATE_LIMITED"
                decision = AccessDecision(
                    state=AccessState.BLOCKED,
                    challenge_type=ChallengeType.NO_CHALLENGE,
                    reason_code=reason_code,
                    block_reason="rate_limited",
                    signals=signals,
                )
                log.info("classifier: decision=BLOCKED challenge_type=NONE reason=%s", reason_code)
                return ClassificationResult(
                    access=AccessState.BLOCKED,
                    status_code=status_code,
                    final_url=final_url,
                    reason_code=reason_code,
                    block_reason="rate_limited",
                    signals=signals,
                    decision=decision,
                    challenge=ChallengeInfo(
                        present=False,
                        type=ChallengeType.NO_CHALLENGE,
                        supported=False,
                        url=final_url,
                        note="HTTP 429 Rate Limited. No detectable CAPTCHA widget or WAF challenge present.",
                    ),
                )

            # 5. HTTP 401 / 403 / 503 without CAPTCHA
            if status_code == 401:
                reason_code = "HTTP_401_UNAUTHORIZED"
                block_reason = "unauthorized"
            elif status_code == 403:
                reason_code = "HTTP_403_ACCESS_DENIED"
                block_reason = "access_denied"
            elif status_code == 503:
                reason_code = "HTTP_503_SERVICE_UNAVAILABLE"
                block_reason = "service_unavailable"
            else:
                reason_code = "UNKNOWN_ACCESS_BLOCK"
                block_reason = "unknown_block"

            decision = AccessDecision(
                state=AccessState.BLOCKED,
                challenge_type=ChallengeType.UNKNOWN_ACCESS_BLOCK,
                reason_code=reason_code,
                block_reason=block_reason,
                signals=signals,
            )
            log.info("classifier: decision=BLOCKED challenge_type=NONE reason=%s", reason_code)
            return ClassificationResult(
                access=AccessState.BLOCKED,
                status_code=status_code,
                final_url=final_url,
                reason_code=reason_code,
                block_reason=block_reason,
                signals=signals,
                decision=decision,
                challenge=ChallengeInfo(
                    present=False,
                    type=ChallengeType.UNKNOWN_ACCESS_BLOCK,
                    supported=False,
                    url=final_url,
                    note=f"Ordinary HTTP {status_code} block without identifiable WAF or CAPTCHA signatures.",
                ),
            )

        # ── Level 2: DOM Challenge Detection (Access Allowed, HTTP 2xx) ─────
        soup = BeautifulSoup(html, "html.parser")

        # 1. Cloudflare Turnstile
        turnstile = soup.find(class_=re.compile(r"cf-turnstile", re.I))
        if not turnstile:
            for el in soup.find_all(lambda t: t.has_attr("data-sitekey")):
                cls_attr = el.get("class")
                cls_str = " ".join(cls_attr) if isinstance(cls_attr, list) else (cls_attr or "")
                if "turnstile" in cls_str.lower():
                    turnstile = el
                    break

        if (turnstile and turnstile.get("data-sitekey")) or "challenges.cloudflare.com/turnstile" in html:
            sitekey: Optional[str] = None
            if turnstile and turnstile.get("data-sitekey"):
                sitekey = str(turnstile["data-sitekey"])
            else:
                m = re.search(r'sitekey["\']?\s*[:=]\s*["\']([0-9a-zA-Z_\-]+)["\']', html)
                sitekey = m.group(1) if m else None

            decision = AccessDecision(
                state=AccessState.ALLOWED,
                challenge_type=ChallengeType.TURNSTILE,
                reason_code="TURNSTILE_DETECTED",
                signals=signals,
            )
            log.info("classifier: decision=ALLOWED challenge_type=TURNSTILE reason=TURNSTILE_DETECTED")
            return ClassificationResult(
                access=AccessState.ALLOWED,
                status_code=status_code,
                final_url=final_url,
                reason_code="TURNSTILE_DETECTED",
                signals=signals,
                decision=decision,
                challenge=ChallengeInfo(
                    present=True,
                    layer=ChallengeLayer.DOM,
                    type=ChallengeType.TURNSTILE,
                    supported=True,
                    sitekey=sitekey,
                    url=final_url,
                ),
            )

        # 2. Google reCAPTCHA
        recaptcha = soup.find(class_=re.compile(r"g-recaptcha", re.I))
        if recaptcha and recaptcha.get("data-sitekey"):
            decision = AccessDecision(
                state=AccessState.ALLOWED,
                challenge_type=ChallengeType.RECAPTCHA,
                reason_code="RECAPTCHA_DETECTED",
                signals=signals,
            )
            log.info("classifier: decision=ALLOWED challenge_type=RECAPTCHA reason=RECAPTCHA_DETECTED")
            return ClassificationResult(
                access=AccessState.ALLOWED,
                status_code=status_code,
                final_url=final_url,
                reason_code="RECAPTCHA_DETECTED",
                signals=signals,
                decision=decision,
                challenge=ChallengeInfo(
                    present=True,
                    layer=ChallengeLayer.DOM,
                    type=ChallengeType.RECAPTCHA,
                    version="v2",
                    supported=True,
                    sitekey=str(recaptcha["data-sitekey"]),
                    url=final_url,
                ),
            )

        recaptcha_script = re.search(
            r'recaptcha/(?:api|enterprise)\.js\?render=([0-9a-zA-Z_\-]+)', html
        )
        if recaptcha_script and recaptcha_script.group(1) != "explicit":
            decision = AccessDecision(
                state=AccessState.ALLOWED,
                challenge_type=ChallengeType.RECAPTCHA,
                reason_code="RECAPTCHA_DETECTED",
                signals=signals,
            )
            log.info("classifier: decision=ALLOWED challenge_type=RECAPTCHA reason=RECAPTCHA_DETECTED")
            return ClassificationResult(
                access=AccessState.ALLOWED,
                status_code=status_code,
                final_url=final_url,
                reason_code="RECAPTCHA_DETECTED",
                signals=signals,
                decision=decision,
                challenge=ChallengeInfo(
                    present=True,
                    layer=ChallengeLayer.DOM,
                    type=ChallengeType.RECAPTCHA,
                    version="v3",
                    supported=True,
                    sitekey=recaptcha_script.group(1),
                    url=final_url,
                ),
            )

        # 3. hCaptcha
        hcaptcha = soup.find(class_=re.compile(r"h-captcha", re.I))
        if hcaptcha and hcaptcha.get("data-sitekey"):
            decision = AccessDecision(
                state=AccessState.ALLOWED,
                challenge_type=ChallengeType.HCAPTCHA,
                reason_code="HCAPTCHA_DETECTED",
                signals=signals,
            )
            log.info("classifier: decision=ALLOWED challenge_type=HCAPTCHA reason=HCAPTCHA_DETECTED")
            return ClassificationResult(
                access=AccessState.ALLOWED,
                status_code=status_code,
                final_url=final_url,
                reason_code="HCAPTCHA_DETECTED",
                signals=signals,
                decision=decision,
                challenge=ChallengeInfo(
                    present=True,
                    layer=ChallengeLayer.DOM,
                    type=ChallengeType.HCAPTCHA,
                    supported=True,
                    sitekey=str(hcaptcha["data-sitekey"]),
                    url=final_url,
                ),
            )

        # 4. Solve Media
        solvemedia_iframe = soup.find("iframe", src=re.compile(r"solvemedia\.com", re.I))
        solvemedia_input = soup.find(lambda t: t.name in ("input", "textarea") and bool(re.search(r"adcopy_(?:response|challenge)", str(t.get("name", "")), re.I)))
        solvemedia_script = soup.find("script", src=re.compile(r"solvemedia\.com|solvemedia\.js", re.I))
        if solvemedia_iframe or solvemedia_input or solvemedia_script or signals["solvemedia"]:
            key = None
            for tag in [solvemedia_iframe, solvemedia_script]:
                if tag and tag.get("src"):
                    m = re.search(r'[?&]k=([^&"\']+)', str(tag.get("src", "")))
                    if m:
                        key = m.group(1)
                        break

            decision = AccessDecision(
                state=AccessState.ALLOWED,
                challenge_type=ChallengeType.SOLVEMEDIA,
                reason_code="SOLVEMEDIA_DETECTED",
                signals=signals,
            )
            log.info("classifier: decision=ALLOWED challenge_type=SOLVEMEDIA reason=SOLVEMEDIA_DETECTED")
            return ClassificationResult(
                access=AccessState.ALLOWED,
                status_code=status_code,
                final_url=final_url,
                reason_code="SOLVEMEDIA_DETECTED",
                signals=signals,
                decision=decision,
                challenge=ChallengeInfo(
                    present=True,
                    layer=ChallengeLayer.DOM,
                    type=ChallengeType.SOLVEMEDIA,
                    adapter_available=False,
                    supported=False,
                    sitekey=key,
                    url=final_url,
                    action_required="manual_or_external_handler_required",
                    note="Solve Media phrase image requires OCR/vision; unsupported by CloakBrowser sidecar.",
                ),
            )

        # 5. Clean Page
        decision = AccessDecision(
            state=AccessState.ALLOWED,
            challenge_type=ChallengeType.NO_CHALLENGE,
            reason_code="HTTP_SUCCESS",
            signals=signals,
        )
        log.info("classifier: decision=ALLOWED challenge_type=NONE reason=HTTP_SUCCESS")
        return ClassificationResult(
            access=AccessState.ALLOWED,
            status_code=status_code,
            final_url=final_url,
            reason_code="HTTP_SUCCESS",
            signals=signals,
            decision=decision,
            challenge=ChallengeInfo(
                present=False,
                type=ChallengeType.NO_CHALLENGE,
                supported=True,
                url=final_url,
            ),
        )

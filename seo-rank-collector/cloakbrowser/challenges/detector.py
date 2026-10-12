"""Non-invasive security challenge detection.

Inspects the DOM, iframe tree, and widget markers to detect the presence
and resolution state of challenges (reCAPTCHA, Turnstile, Cloudflare Managed, hCaptcha).
Does not interact with or attempt to defeat the challenges.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .status import ChallengeState, ChallengeStatus, ChallengeType

logger = logging.getLogger("cloakbrowser.challenges.detector")


_DETECTION_SCRIPT = """
(() => {
    // 1. Cloudflare Managed / Under Attack interstitial
    const title = document.title ? document.title.toLowerCase() : '';
    if (title.includes('just a moment') || document.querySelector('#challenge-running, #cf-challenge-running')) {
        return {
            type: 'cloudflare_managed',
            state: 'pending',
            token: null,
            meta: { title: document.title }
        };
    }

    // 2. Cloudflare Turnstile
    const turnstileFrame = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
    const turnstileInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (turnstileFrame || turnstileInput) {
        const token = turnstileInput ? turnstileInput.value : '';
        return {
            type: 'turnstile',
            state: token && token.length > 10 ? 'resolved' : 'detected',
            token: token || null,
            meta: { hasFrame: !!turnstileFrame }
        };
    }

    // 3. Google reCAPTCHA (v2 / v3)
    const recaptchaFrame = document.querySelector('iframe[src*="google.com/recaptcha"], iframe[src*="recaptcha.net/recaptcha"]');
    const recaptchaResponse = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
    if (recaptchaFrame || recaptchaResponse) {
        const token = recaptchaResponse ? recaptchaResponse.value : '';
        const bframe = document.querySelector('iframe[src*="recaptcha/api2/bframe"]');
        return {
            type: 'recaptcha_v2',
            state: token && token.length > 10 ? 'resolved' : (bframe ? 'pending' : 'detected'),
            token: token || null,
            meta: { hasBframe: !!bframe, hasAnchor: !!recaptchaFrame }
        };
    }

    // 4. hCaptcha
    const hcaptchaFrame = document.querySelector('iframe[src*="hcaptcha.com"]');
    const hcaptchaResponse = document.querySelector('textarea[name="h-captcha-response"]');
    if (hcaptchaFrame || hcaptchaResponse) {
        const token = hcaptchaResponse ? hcaptchaResponse.value : '';
        return {
            type: 'hcaptcha',
            state: token && token.length > 10 ? 'resolved' : 'detected',
            token: token || null,
            meta: { hasFrame: !!hcaptchaFrame }
        };
    }

    return null;
})()
"""


def detect_challenge(page: Any) -> ChallengeStatus:
    """Inspect the given page (sync) to detect active security challenges and their state."""
    try:
        raw = page.evaluate(_DETECTION_SCRIPT)
        if not raw or not isinstance(raw, dict):
            return ChallengeStatus(challenge_type=ChallengeType.UNKNOWN, state=ChallengeState.NOT_PRESENT)

        ctype_str = raw.get("type", "unknown")
        cstate_str = raw.get("state", "detected")
        token = raw.get("token")
        meta = raw.get("meta", {})

        ctype = ChallengeType(ctype_str) if ctype_str in ChallengeType.__members__.values() else ChallengeType.UNKNOWN
        cstate = ChallengeState(cstate_str) if cstate_str in ChallengeState.__members__.values() else ChallengeState.DETECTED

        status = ChallengeStatus(
            challenge_type=ctype,
            state=cstate,
            token=token,
            metadata=meta,
        )
        logger.debug("[challenge-detector] Detected %s in state=%s (resolved=%s)", ctype.value, cstate.value, status.resolved)
        return status
    except Exception as e:
        logger.debug("[challenge-detector] Detection evaluate failed: %s", e)
        return ChallengeStatus(challenge_type=ChallengeType.UNKNOWN, state=ChallengeState.NOT_PRESENT)


async def detect_challenge_async(page: Any) -> ChallengeStatus:
    """Inspect the given page (async) to detect active security challenges and their state."""
    try:
        raw = await page.evaluate(_DETECTION_SCRIPT)
        if not raw or not isinstance(raw, dict):
            return ChallengeStatus(challenge_type=ChallengeType.UNKNOWN, state=ChallengeState.NOT_PRESENT)

        ctype_str = raw.get("type", "unknown")
        cstate_str = raw.get("state", "detected")
        token = raw.get("token")
        meta = raw.get("meta", {})

        ctype = ChallengeType(ctype_str) if ctype_str in ChallengeType.__members__.values() else ChallengeType.UNKNOWN
        cstate = ChallengeState(cstate_str) if cstate_str in ChallengeState.__members__.values() else ChallengeState.DETECTED

        return ChallengeStatus(
            challenge_type=ctype,
            state=cstate,
            token=token,
            metadata=meta,
        )
    except Exception as e:
        logger.debug("[challenge-detector-async] Detection evaluate failed: %s", e)
        return ChallengeStatus(challenge_type=ChallengeType.UNKNOWN, state=ChallengeState.NOT_PRESENT)


class ChallengeDetector:
    """Class wrapper for challenge detection."""

    def detect_synchronous(self, page: Any) -> ChallengeStatus:
        return detect_challenge(page)

    async def detect_asynchronous(self, page: Any) -> ChallengeStatus:
        return await detect_challenge_async(page)


"""Resilient, origin-isolated HTTP session for SEO crawling.

Uses curl_cffi for Chrome-impersonated TLS fingerprinting so that cf_clearance cookies
obtained by the CloakBrowser solver can be replayed successfully.
Maintains per-origin cookie jars, clearance expiry metadata, and redacts sensitive credentials.
"""
import copy
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from curl_cffi import requests as cffi_requests

log = logging.getLogger("seo.session")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)

# Chrome TLS impersonation target — must align with what CloakBrowser uses
_IMPERSONATE = "chrome"


class OriginState:
    """Isolated session state for a specific origin (domain)."""

    def __init__(self, domain: str):
        self.domain = domain
        self.cookies: Dict[str, str] = {}
        self.user_agent: Optional[str] = None
        self.clearance_expires_at: Optional[float] = None
        self.tokens: Dict[str, str] = {}  # url -> token

    def is_clearance_expired(self) -> bool:
        if self.clearance_expires_at is None:
            return False
        return time.time() >= self.clearance_expires_at

    def invalidate_clearance(self):
        """Invalidate clearance when an access block reoccurs or expires."""
        self.cookies.pop("cf_clearance", None)
        self.cookies.pop("aws-waf-token", None)
        if "google." in self.domain:
            self.cookies.pop("NID", None)
            self.cookies.pop("AEC", None)
            self.cookies.pop("__Secure-ENID", None)
            # Re-seed clean baseline consent
            self.cookies["SOCS"] = "CAESEwgDEgk0ODE3Nzc0MjQaAmVuIAEaBgiA_LyaBg"
        self.clearance_expires_at = None


class FetchResponse:
    """Thin wrapper to give curl_cffi responses a consistent interface."""

    def __init__(self, status_code: int, text: str, url: str):
        self.status_code = status_code
        self.text = text
        self.url = url


class CrawlerSession:
    """Crawler session with strict origin isolation and Chrome TLS impersonation."""

    def __init__(
        self,
        default_headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
        default_clearance_ttl: float = 300.0,
    ):
        self.default_headers = default_headers or {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": '"Chromium";v="146", "Not?A_Brand";v="24", "Google Chrome";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
        }
        self.timeout = timeout
        self.default_clearance_ttl = default_clearance_ttl
        self._origins: Dict[str, OriginState] = {}

    @staticmethod
    def extract_domain(url: str) -> str:
        """Extract canonical domain from URL."""
        parsed = urlparse(url)
        return (parsed.netloc or parsed.path).lower().split(":")[0]

    def _get_origin_state(self, domain: str) -> OriginState:
        if domain not in self._origins:
            state = OriginState(domain)
            if "google." in domain:
                # Pre-seed consent cookie to avoid consent redirects and basic enablejs challenges
                state.cookies["SOCS"] = "CAESEwgDEgk0ODE3Nzc0MjQaAmVuIAEaBgiA_LyaBg"
            self._origins[domain] = state
        return self._origins[domain]

    def update_clearance(
        self,
        target_url: str,
        cookies: list,
        user_agent: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ):
        """Update clearance cookies and UA exclusively for the target origin."""
        domain = self.extract_domain(target_url)
        origin = self._get_origin_state(domain)

        # Google clearance cookies (AEC, NID, SOCS) last for weeks, not 300s
        default_ttl = 86400.0 * 30 if "google." in domain else self.default_clearance_ttl
        ttl = ttl_seconds or default_ttl
        origin.clearance_expires_at = time.time() + ttl

        for c in cookies:
            if isinstance(c, dict) and "name" in c and "value" in c:
                origin.cookies[c["name"]] = c["value"]

        # If this is Google, also mirror cookies to related Google origins (google.com and www.google.com)
        if "google." in domain:
            related_domains = {"google.com", "www.google.com"}
            if domain in related_domains:
                for rel in related_domains:
                    if rel != domain:
                        rel_origin = self._get_origin_state(rel)
                        rel_origin.clearance_expires_at = origin.clearance_expires_at
                        for c in cookies:
                            if isinstance(c, dict) and "name" in c and "value" in c:
                                rel_origin.cookies[c["name"]] = c["value"]

        if user_agent:
            origin.user_agent = user_agent
            import re
            m = re.search(r"Chrome/(\d+)", user_agent)
            if m:
                v = m.group(1)
                origin.sec_ch_ua = f'"Chromium";v="{v}", "Not?A_Brand";v="24", "Google Chrome";v="{v}"'

    def invalidate_origin_clearance(self, target_url: str):
        """Purge expired or invalid clearance cookies for this domain."""
        domain = self.extract_domain(target_url)
        if domain in self._origins:
            self._origins[domain].invalidate_clearance()
        if "google." in domain:
            for rel in ("google.com", "www.google.com"):
                if rel in self._origins:
                    self._origins[rel].invalidate_clearance()

    def store_token(self, target_url: str, token: str):
        """Privately store token within origin boundaries."""
        domain = self.extract_domain(target_url)
        origin = self._get_origin_state(domain)
        origin.tokens[target_url] = token

    def get_token(self, target_url: str) -> Optional[str]:
        domain = self.extract_domain(target_url)
        if domain in self._origins:
            tokens = self._origins[domain].tokens
            if target_url in tokens:
                return tokens[target_url]
            alt_url = target_url[:-1] if target_url.endswith("/") else target_url + "/"
            if alt_url in tokens:
                return tokens[alt_url]
            if tokens:
                return list(tokens.values())[-1]
        return None

    def fetch(self, url: str) -> FetchResponse:
        """Send GET request with Chrome-impersonated TLS, isolated cookies, and aligned UA."""
        domain = self.extract_domain(url)
        origin = self._get_origin_state(domain)

        # Invalidate if TTL expired
        if origin.is_clearance_expired():
            log.info("Clearance expired for %s, purging.", domain)
            origin.invalidate_clearance()

        headers = copy.deepcopy(self.default_headers)
        if origin.user_agent:
            headers["User-Agent"] = origin.user_agent
        if hasattr(origin, "sec_ch_ua") and origin.sec_ch_ua:
            headers["sec-ch-ua"] = origin.sec_ch_ua

        cookies = origin.cookies if origin.cookies else None

        log.debug("fetch %s (cookies=%s, impersonate=%s)",
                  url, list((cookies or {}).keys()), _IMPERSONATE)

        resp = cffi_requests.get(
            url,
            headers=headers,
            cookies=cookies,
            impersonate=_IMPERSONATE,
            timeout=self.timeout,
            allow_redirects=True,
        )

        final_url_str = str(resp.url)
        # Automatic Tainted Cookie Quarantine: Invalidate clearance if response triggered block or sorry redirect
        if resp.status_code in (403, 429) or "/sorry/index" in final_url_str:
            log.warning("Detected block / rate limit (HTTP %d, url=%s). Invalidating tainted clearance for %s.",
                        resp.status_code, final_url_str[:60], domain)
            self.invalidate_origin_clearance(url)

        return FetchResponse(
            status_code=resp.status_code,
            text=resp.text,
            url=final_url_str,
        )

    def post(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> FetchResponse:
        """Send POST request with Chrome-impersonated TLS, origin cookies, and aligned UA."""
        domain = self.extract_domain(url)
        origin = self._get_origin_state(domain)

        headers = copy.deepcopy(self.default_headers)
        if origin.user_agent:
            headers["User-Agent"] = origin.user_agent

        cookies = origin.cookies if origin.cookies else None

        resp = cffi_requests.post(
            url,
            data=data,
            json=json_data,
            headers=headers,
            cookies=cookies,
            impersonate=_IMPERSONATE,
            timeout=self.timeout,
            allow_redirects=True,
        )

        return FetchResponse(
            status_code=resp.status_code,
            text=resp.text,
            url=str(resp.url),
        )

    def get_sanitized_state(self, domain: Optional[str] = None) -> Dict[str, Any]:
        """Return safe-for-logging metadata without leaking sensitive credentials."""
        if domain:
            origin = self._origins.get(domain.lower())
            if not origin:
                return {"domain": domain, "tracked": False}
            return {
                "domain": domain,
                "tracked": True,
                "cookie_count": len(origin.cookies),
                "stored_cookie_names": list(origin.cookies.keys()),
                "has_cf_clearance": "cf_clearance" in origin.cookies,
                "clearance_expired": origin.is_clearance_expired(),
                "has_tokens": len(origin.tokens) > 0,
            }
        return {
            "tracked_origins_count": len(self._origins),
            "domains": list(self._origins.keys()),
        }

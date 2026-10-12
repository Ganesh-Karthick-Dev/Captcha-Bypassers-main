"""SERP Acquisition Layer for SEO Rank Collector with 4-Layer Diagnostic Logging.

Supports:
- HTTP mode: Fast curl_cffi acquisition with TLS impersonation
- Browser mode (Approach 1): Full CloakBrowser stealth browser rendering with automatic consent and challenge detection
- 4-layer diagnostic observability with detailed summary box
"""
import sys
import time
import uuid
import asyncio
import logging
from pathlib import Path
from typing import Tuple, Optional, Dict, List

# Ensure local vendored modules take precedence
import os
os.environ.setdefault("CLOAKBROWSER_AUTO_UPDATE", "false")

COLLECTOR_DIR = Path(__file__).resolve().parent
if str(COLLECTOR_DIR) not in sys.path:
    sys.path.insert(0, str(COLLECTOR_DIR))

# Import underlying detector & solver components
PROJECT_ROOT = COLLECTOR_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DETECTOR_DIR = PROJECT_ROOT / "seo-captcha-detector"
if str(DETECTOR_DIR) not in sys.path:
    sys.path.insert(0, str(DETECTOR_DIR))

from fetcher import SEOFetcher  # type: ignore
from models import AccessState, CrawlResult, ChallengeLayer, ChallengeType  # type: ignore
from classifier import ChallengeClassifier  # type: ignore

# Import 100% local vendored CloakBrowser
import cloakbrowser

log = logging.getLogger("seo.rank_collector.acquisition")


class SERPAcquisitionResult:
    def __init__(
        self,
        html: Optional[str],
        status: str,
        access_state: str,
        retries: int = 0,
        error: Optional[str] = None,
        status_code: Optional[int] = None,
        challenge_type: Optional[str] = None,
        reason_code: Optional[str] = None,
        block_reason: Optional[str] = None,
        signals: Optional[Dict[str, bool]] = None,
        elapsed_ms: Optional[int] = None,
        content_length: int = 0,
    ):
        self.html = html
        self.status = status  # "allowed", "challenge_resolved", "blocked", "failed"
        self.access_state = access_state
        self.retries = retries
        self.error = error
        self.status_code = status_code
        self.challenge_type = challenge_type
        self.reason_code = reason_code or ("HTTP_SUCCESS" if status == "allowed" else "UNKNOWN_BLOCK")
        self.block_reason = block_reason
        self.signals = signals or {}
        self.elapsed_ms = elapsed_ms
        self.content_length = content_length

    @property
    def is_success(self) -> bool:
        return self.status in ("allowed", "challenge_resolved") and bool(self.html)


class SERPAcquisitor:
    """Acquisition layer supporting both HTTP and CloakBrowser acquisition."""

    def __init__(
        self,
        solver_url: Optional[str] = None,
        max_retries: int = 2,
        timeout: float = 30.0,
        mode: str = "http",  # "http" or "browser"
        headless: bool = True,
        persist_cookies: bool = True,
        storage_dir: Optional[str] = None,
    ):
        self.mode = mode.lower()
        self.headless = headless
        self.persist_cookies = persist_cookies
        if not solver_url:
            solver_url = os.getenv("SOLVER_URL", f"http://127.0.0.1:{os.getenv('PORT', '5001')}")
        self.solver_url = solver_url
        if storage_dir:
            self.profile_dir = Path(storage_dir) / ".browser_profile"
        else:
            self.profile_dir = Path(__file__).resolve().parent / "data" / ".browser_profile"
        self.fetcher = SEOFetcher(
            solver_url=solver_url,
            max_retries=max_retries,
            timeout=timeout,
            solve_dom_widgets=True,
        )
        self._http_blocked_until = 0.0

    @property
    def shared_cookie_file(self) -> Path:
        return Path(__file__).resolve().parent / "data" / ".google_clearance_cookies.json"

    TRUSTED_GOOGLE_COOKIE_NAMES = {
        "SOCS", "CONSENT", "1P_JAR", "SEARCH_SAMESITE",
        "AEC", "__Secure-ENID", "NID", "DV", "__Secure-STRP",
        "GOOGLE_ABUSE_EXEMPTION",
    }
    EXCLUDED_GOOGLE_COOKIE_NAMES = {"__Host-GAPS", "S", "OGP", "SIDCC"}

    def _load_shared_cookies(self) -> List[dict]:
        try:
            if self.shared_cookie_file.exists():
                import json
                with open(self.shared_cookie_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and data:
                        clean_data = [
                            c for c in data
                            if isinstance(c, dict) and (
                                c.get("name") in self.TRUSTED_GOOGLE_COOKIE_NAMES or
                                "google" in str(c.get("domain", ""))
                            )
                            and c.get("name") not in self.EXCLUDED_GOOGLE_COOKIE_NAMES
                        ]
                        if clean_data:
                            return clean_data
        except Exception:
            pass
        # Default baseline Google consent cookie to prevent consent wall / basic enablejs
        return [
            {
                "name": "SOCS",
                "value": "CAESEwgDEgk0ODE3Nzc0MjQaAmVuIAEaBgiA_LyaBg",
                "domain": ".google.com",
                "path": "/",
                "secure": True,
            }
        ]

    def _load_shared_user_agent(self) -> Optional[str]:
        try:
            meta_file = self.shared_cookie_file.with_name(".google_clearance_meta.json")
            if meta_file.exists():
                import json
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data.get("user_agent")
        except Exception:
            pass
        return None

    def _purge_tainted_cookies(self):
        """Invalidates in-memory HTTP clearance without wiping clean CloakBrowser stealth cookies from disk."""
        try:
            self.fetcher.session.invalidate_origin_clearance("https://www.google.com")
            log.info("Invalidated in-memory HTTP clearance for Google; preserved disk browser clearance cookies.")
        except Exception as e:
            log.warning("Could not invalidate in-memory clearance: %s", e)

    def _save_shared_cookies(self, cookies: List[dict], user_agent: Optional[str] = None):
        try:
            import json
            existing_map = {}
            for c in self._load_shared_cookies():
                if isinstance(c, dict) and c.get("name"):
                    existing_map[c["name"]] = c
            for c in cookies:
                if isinstance(c, dict) and c.get("name") and c.get("value") is not None:
                    name = str(c["name"])
                    if name in self.EXCLUDED_GOOGLE_COOKIE_NAMES:
                        continue
                    if name in self.TRUSTED_GOOGLE_COOKIE_NAMES or "google" in str(c.get("domain", "")):
                        existing_map[name] = c
            merged = list(existing_map.values())
            self.shared_cookie_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.shared_cookie_file, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2)
            if user_agent:
                meta_file = self.shared_cookie_file.with_name(".google_clearance_meta.json")
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump({"user_agent": user_agent}, f, indent=2)
            log.info("Persisted %d clean clearance cookies to %s (UA=%s)", len(merged), self.shared_cookie_file.name, user_agent or "default")
        except Exception as e:
            log.warning("Could not persist shared cookies: %s", e)

    async def _inject_shared_cookies(self, browser_or_ctx):
        """Pre-seeds stored Google clearance cookies into CloakBrowser before navigation."""
        try:
            saved_cookies = self._load_shared_cookies()
            context = browser_or_ctx if hasattr(browser_or_ctx, "add_cookies") else (
                browser_or_ctx.contexts[0] if hasattr(browser_or_ctx, "contexts") and browser_or_ctx.contexts else None
            )
            if not context or not hasattr(context, "add_cookies"):
                return

            if saved_cookies:
                valid_cookies = []
                for c in saved_cookies:
                    if not isinstance(c, dict):
                        continue
                    name = c.get("name")
                    val = c.get("value")
                    if not name or val is None:
                        continue
                    cookie_dict = {
                        "name": str(name),
                        "value": str(val),
                        "domain": c.get("domain", ".google.com"),
                        "path": c.get("path", "/"),
                    }
                    if "secure" in c and isinstance(c["secure"], bool):
                        cookie_dict["secure"] = c["secure"]
                    if "httpOnly" in c and isinstance(c["httpOnly"], bool):
                        cookie_dict["httpOnly"] = c["httpOnly"]
                    if "sameSite" in c and c["sameSite"] in ("Strict", "Lax", "None"):
                        cookie_dict["sameSite"] = c["sameSite"]
                    valid_cookies.append(cookie_dict)

                if valid_cookies:
                    await context.add_cookies(valid_cookies)
                    log.info("Pre-seeded %d clearance cookies into CloakBrowser context.", len(valid_cookies))
        except Exception as e:
            log.warning("Could not pre-seed cookies into CloakBrowser: %s", e)

    async def _harvest_and_save_cookies(self, browser_or_ctx, search_url: str, current_url: str = ""):
        """Harvests active cookies from CloakBrowser and syncs them to disk and HTTP session, skipping if tainted."""
        if "/sorry/" in (current_url or ""):
            log.warning("Skipping cookie harvest: page URL is on /sorry/ (tainted).")
            return

        # Fix 1: Non-/sorry/ page confirms valid access — clear cooldown immediately
        self._http_blocked_until = 0.0

        try:
            context = browser_or_ctx if hasattr(browser_or_ctx, "cookies") else (
                browser_or_ctx.contexts[0] if hasattr(browser_or_ctx, "contexts") and browser_or_ctx.contexts else None
            )
            if context and hasattr(context, "cookies"):
                cookies = await context.cookies()
                if cookies:
                    clean_cookies = [
                        c for c in cookies
                        if isinstance(c, dict) and (
                            c.get("name") in self.TRUSTED_GOOGLE_COOKIE_NAMES or
                            "google" in c.get("domain", "")
                        )
                    ]
                    if clean_cookies:
                        ua = None
                        if hasattr(context, "pages") and context.pages:
                            try:
                                ua = await context.pages[0].evaluate("navigator.userAgent")
                            except Exception:
                                pass
                        self._save_shared_cookies(clean_cookies, user_agent=ua)
                        self.fetcher.session.update_clearance(search_url, clean_cookies, user_agent=ua, ttl_seconds=86400.0 * 30)
                        self._http_blocked_until = 0.0  # Clear cooldown: fresh cookies restored HTTP capability immediately
                        google_cookies = [c.get("name") for c in clean_cookies]
                        log.info("Harvested & synced %d verified Google clearance cookies (HTTP unblocked): %s (UA=%s)", len(clean_cookies), google_cookies[:8], ua)
        except Exception as e:
            log.warning("Could not harvest cookies from CloakBrowser: %s", e)

    def print_diagnostic_box(
        self,
        page_number: int,
        search_url: str,
        result: SERPAcquisitionResult,
        solver_invoked: bool = False,
    ):
        """Prints diagnostic summary after every SERP acquisition attempt (concise by default)."""
        import os
        result_display = f"PAGE {page_number} ACQUIRED" if result.is_success else f"PAGE {page_number} FAILED ({result.reason_code})"
        
        # Default: clean, readable single-line log
        if os.getenv("SERP_VERBOSE_BOX", "0") != "1":
            log.info(
                "SERP [P%d] %s | HTTP %s | %s | %dms | %s",
                page_number,
                result_display,
                result.status_code or 0,
                result.reason_code or "NONE",
                result.elapsed_ms if result.elapsed_ms is not None else 0,
                search_url[:90],
            )
            return

        # Full ASCII box only if SERP_VERBOSE_BOX=1
        signals = result.signals
        cf_sig = "YES" if signals.get("cloudflare_interstitial") else "NO"
        turnstile_sig = "YES" if signals.get("turnstile") else "NO"
        recaptcha_sig = "YES" if signals.get("recaptcha") else "NO"
        hcaptcha_sig = "YES" if signals.get("hcaptcha") else "NO"
        solvemedia_sig = "YES" if signals.get("solvemedia") else "NO"

        challenge_display = "NONE" if (not result.challenge_type or result.challenge_type in ("none_detected", "no_challenge")) else result.challenge_type.upper()
        retryable_display = "YES" if result.status_code in (429, 503) else ("NO" if result.is_success else "NO")

        print("\n" + "=" * 70)
        print("SERP ACQUISITION DIAGNOSTIC")
        print("=" * 70)
        print(f"Request:  Page {page_number} | Mode: {self.mode.upper()} | URL: {search_url}")
        print(f"Response: HTTP {result.status_code} | Length: {result.content_length} | Elapsed: {result.elapsed_ms or 0} ms")
        print(f"Status:   State: {result.access_state.upper()} | Challenge: {challenge_display} | Reason: {result.reason_code}")
        print(f"Signals:  Cloudflare: {cf_sig} | Turnstile: {turnstile_sig} | reCAPTCHA: {recaptcha_sig} | hCaptcha: {hcaptcha_sig}")
        print(f"Decision: Solver: {'YES' if solver_invoked else 'NO'} | Retryable: {retryable_display} | HTML: {'YES' if result.is_success else 'NO'}")
        print(f"Result:   {result_display}")
        print("=" * 70 + "\n")

    def acquire_serp(self, search_url: str, page_number: int = 1, geo_context: Optional[object] = None) -> SERPAcquisitionResult:
        """Acquires SERP HTML via browser, HTTP, or hybrid mode with automatic browser escalation."""
        now = time.time()
        # If in browser mode or local IP is in active HTTP 429 cooldown, fast-path straight to stealth CloakBrowser
        if self.mode == "browser" or (self.mode in ("hybrid", "auto", "fallback", "http") and now < self._http_blocked_until):
            if now < self._http_blocked_until:
                log.info("⚡ [Fast-Path] Local IP in HTTP 429 cooldown (%.0fs remaining). Routing directly to CloakBrowser stealth mode...", self._http_blocked_until - now)
            return self._acquire_serp_browser(search_url, page_number, geo_context=geo_context)

        result = self._acquire_serp_http(search_url, page_number)
        if not result.is_success and self.mode in ("hybrid", "auto", "fallback", "http"):
            if result.status_code == 429 or (result.reason_code and ("429" in result.reason_code or "JS_INTERSTITIAL" in result.reason_code)):
                # Set 300s cooldown so subsequent keywords in this batch skip the failed HTTP step
                self._http_blocked_until = time.time() + 300.0
                log.info("🔒 [HTTP Escalation] Setting 300s cooldown on HTTP acquisition. Subsequent batch keywords will route straight to CloakBrowser stealth mode.")
            log.info("HTTP acquisition challenge or block detected. Escalating to CloakBrowser stealth mode...")
            try:
                browser_result = self._acquire_serp_browser(search_url, page_number, geo_context=geo_context)
                if browser_result.is_success:
                    self._http_blocked_until = 0.0
                return browser_result
            except Exception as e:
                log.warning("Browser escalation failed: %s", e)
        return result

    def _acquire_serp_http(self, search_url: str, page_number: int = 1) -> SERPAcquisitionResult:
        """Acquires SERP page HTML via curl_cffi with explicit 4-layer diagnostic logging."""
        req_id = uuid.uuid4().hex[:8]
        start_time = time.time()

        # ── Layer 1: Acquisition Request Log ────────────────────────────────
        log.info("acquisition [request_id=%s]: page=%d method=GET mode=HTTP url=%s", req_id, page_number, search_url)

        # Seed stored clearance cookies into HTTP session if available
        saved_cookies = self._load_shared_cookies()
        saved_ua = self._load_shared_user_agent()
        if saved_cookies:
            self.fetcher.session.update_clearance(search_url, saved_cookies, user_agent=saved_ua)

        crawl_result: CrawlResult = self.fetcher.fetch_page(search_url)
        elapsed_ms = int((time.time() - start_time) * 1000)
        body_length = len(crawl_result.html) if crawl_result.html else 0

        # ── Layer 1: Acquisition Response Log ───────────────────────────────
        log.info("acquisition [request_id=%s]: response_status=%d elapsed_ms=%d body_length=%d url=%s",
                 req_id, crawl_result.status_code, elapsed_ms, body_length, search_url)

        if not crawl_result.classification:
            res = SERPAcquisitionResult(
                html=None,
                status="failed",
                access_state="network_error",
                retries=crawl_result.retries_used,
                error="Network or connection error during SERP fetch.",
                status_code=crawl_result.status_code,
                reason_code="NETWORK_ERROR",
                elapsed_ms=elapsed_ms,
                content_length=body_length,
            )
            self.print_diagnostic_box(page_number, search_url, res, solver_invoked=False)
            return res

        c = crawl_result.classification
        access = c.access
        challenge = c.challenge
        challenge_type_str = challenge.type.value if (challenge and hasattr(challenge.type, "value")) else str(challenge.type if challenge else None)
        reason_code = getattr(c, "reason_code", "UNKNOWN_ACCESS_BLOCK")
        block_reason = getattr(c, "block_reason", None)
        signals = getattr(c, "signals", {})

        # Check for Google JS execution challenge
        if crawl_result.html and ("enablejs" in crawl_result.html or "/httpservice/retry/enablejs" in crawl_result.html):
            log.info("Google enablejs interstitial detected in HTTP response.")
            access = AccessState.BLOCKED
            reason_code = "JS_INTERSTITIAL"

        # Branch 1: ALLOWED
        if access == AccessState.ALLOWED and crawl_result.status == "success":
            res = SERPAcquisitionResult(
                html=crawl_result.html,
                status="allowed",
                access_state="allowed",
                retries=crawl_result.retries_used,
                status_code=crawl_result.status_code,
                challenge_type=challenge_type_str,
                reason_code=reason_code or "HTTP_SUCCESS",
                block_reason=None,
                signals=signals,
                elapsed_ms=elapsed_ms,
                content_length=body_length,
            )
            self.print_diagnostic_box(page_number, search_url, res, solver_invoked=False)
            return res

        # Branch 2: CHALLENGE RESOLVED
        if crawl_result.resolution and crawl_result.resolution.success and crawl_result.status == "success":
            res = SERPAcquisitionResult(
                html=crawl_result.html,
                status="challenge_resolved",
                access_state="allowed",
                retries=crawl_result.retries_used,
                status_code=crawl_result.status_code,
                challenge_type=challenge_type_str,
                reason_code="CHALLENGE_RESOLVED",
                block_reason=None,
                signals=signals,
                elapsed_ms=elapsed_ms,
                content_length=body_length,
            )
            self.print_diagnostic_box(page_number, search_url, res, solver_invoked=True)
            return res

        # Branch 3: BLOCKED
        err_msg = crawl_result.resolution.error if crawl_result.resolution else (
            f"HTTP {crawl_result.status_code} ({reason_code})"
        )
        res = SERPAcquisitionResult(
            html=None,
            status="blocked",
            access_state="blocked",
            retries=crawl_result.retries_used,
            error=err_msg,
            status_code=crawl_result.status_code,
            challenge_type=challenge_type_str if (challenge and challenge.present) else "none_detected",
            reason_code=reason_code,
            block_reason=block_reason,
            signals=signals,
            elapsed_ms=elapsed_ms,
            content_length=body_length,
        )
        self.print_diagnostic_box(page_number, search_url, res, solver_invoked=False)
        return res

    def _acquire_serp_browser(self, search_url: str, page_number: int = 1, geo_context: Optional[object] = None) -> SERPAcquisitionResult:
        """Approach 1: Acquires SERP page using CloakBrowser with stealth JavaScript rendering."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self._acquire_serp_browser_async(search_url, page_number, geo_context=geo_context))
                return future.result()
        return asyncio.run(self._acquire_serp_browser_async(search_url, page_number, geo_context=geo_context))

    async def _acquire_serp_browser_async(self, search_url: str, page_number: int = 1, geo_context: Optional[object] = None) -> SERPAcquisitionResult:
        req_id = uuid.uuid4().hex[:8]
        start_time = time.time()
        worker_dir = None
        try:
            # Dynamically align browser locale and Accept-Language with target country
            browser_locale = "en-US"
            accept_lang = "en-US,en;q=0.9"

            if geo_context and getattr(geo_context, "locale", None):
                target_loc = getattr(geo_context, "locale")
                browser_locale = target_loc
                accept_lang = f"{target_loc},en;q=0.9"
            elif "gl=" in search_url:
                try:
                    import urllib.parse
                    parsed_u = urllib.parse.urlparse(search_url)
                    qs = urllib.parse.parse_qs(parsed_u.query)
                    gl_val = (qs.get("gl", [""])[0]).lower()
                    if gl_val == "in":
                        browser_locale = "en-IN"
                        accept_lang = "en-IN,en;q=0.9"
                    elif gl_val in ("gb", "uk"):
                        browser_locale = "en-GB"
                        accept_lang = "en-GB,en;q=0.9"
                    elif gl_val == "ca":
                        browser_locale = "en-CA"
                        accept_lang = "en-CA,en;q=0.9"
                    elif gl_val == "au":
                        browser_locale = "en-AU"
                        accept_lang = "en-AU,en;q=0.9"
                    elif gl_val == "de":
                        browser_locale = "de-DE"
                        accept_lang = "de-DE,de;q=0.9,en;q=0.8"
                    elif gl_val == "fr":
                        browser_locale = "fr-FR"
                        accept_lang = "fr-FR,fr;q=0.9,en;q=0.8"
                    elif gl_val == "es":
                        browser_locale = "es-ES"
                        accept_lang = "es-ES,es;q=0.9,en;q=0.8"
                    elif gl_val == "it":
                        browser_locale = "it-IT"
                        accept_lang = "it-IT,it;q=0.9,en;q=0.8"
                    elif gl_val == "br":
                        browser_locale = "pt-BR"
                        accept_lang = "pt-BR,pt;q=0.9,en;q=0.8"
                except Exception:
                    pass

            launch_kwargs = {
                "humanize": True,
                "headless": self.headless,
                "locale": browser_locale,
                "extra_http_headers": {"Accept-Language": accept_lang},
            }
            if geo_context:
                lat = getattr(geo_context, "latitude", None)
                lon = getattr(geo_context, "longitude", None)
                tz = getattr(geo_context, "timezone_id", None)
                loc = getattr(geo_context, "locale", None)
                if lat is not None and lon is not None:
                    launch_kwargs["geolocation"] = {"latitude": float(lat), "longitude": float(lon), "accuracy": 10.0}
                    launch_kwargs["permissions"] = ["geolocation"]
                if tz:
                    launch_kwargs["timezone"] = str(tz)
                log.info("[GPS Emulation] Active target: Lat=%.4f, Lon=%.4f, TZ=%s, TargetLocale=%s, BrowserLocale=%s", float(lat or 0), float(lon or 0), tz, loc, browser_locale)

            if self.persist_cookies:
                import os
                worker_id = f"proc_{os.getpid()}_w_{uuid.uuid4().hex[:8]}_{int(time.time() * 1000)}"
                worker_dir = self.profile_dir / worker_id
                worker_dir.mkdir(parents=True, exist_ok=True)
                ctx_mgr = await cloakbrowser.launch_persistent_context_async(
                    user_data_dir=str(worker_dir),
                    **launch_kwargs,
                )
            else:
                ctx_mgr = await cloakbrowser.launch_async(
                    humanize=launch_kwargs.get("humanize", True),
                    headless=launch_kwargs.get("headless", True),
                )

            async with ctx_mgr as browser_or_ctx:
                if hasattr(browser_or_ctx, "new_context"):
                    # Non-persistent browser instance: create context with GPS and permissions
                    ctx = await browser_or_ctx.new_context(
                        geolocation=launch_kwargs.get("geolocation"),
                        permissions=launch_kwargs.get("permissions"),
                        timezone_id=launch_kwargs.get("timezone"),
                        locale=launch_kwargs.get("locale"),
                        extra_http_headers=launch_kwargs.get("extra_http_headers"),
                    )
                    page = await ctx.new_page()
                else:
                    # Persistent context instance
                    ctx = browser_or_ctx
                    page = browser_or_ctx.pages[0] if browser_or_ctx.pages else await browser_or_ctx.new_page()

                if geo_context:
                    try:
                        if hasattr(ctx, "grant_permissions"):
                            await ctx.grant_permissions(["geolocation"], origin="https://www.google.com")
                        if hasattr(ctx, "set_geolocation") and "geolocation" in launch_kwargs:
                            await ctx.set_geolocation(launch_kwargs["geolocation"])
                    except Exception as geo_perm_err:
                        log.debug("Could not grant origin geolocation: %s", geo_perm_err)

                try:
                    await self._inject_shared_cookies(browser_or_ctx)
                    await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
                    try:
                        await page.wait_for_selector("div#search, #rso, #center_col, #res, #main, form#captcha-form", timeout=4000)
                    except Exception:
                        pass

                    # Handle Google Cookie Consent dialog if present
                    consent_btn = page.locator("button#L2AGLb, button:has-text('Accept all'), button:has-text('I agree')")
                    if await consent_btn.count() > 0:
                        log.info("Clicking cookie consent button...")
                        await consent_btn.first.click()
                        try:
                            await page.wait_for_selector("div#search, #rso, #center_col, #res", timeout=2500)
                        except Exception:
                            await asyncio.sleep(0.3)

                    final_url = page.url
                    content = await page.content()
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    body_length = len(content)

                    # Save crawler visual verification screenshot (opt-in only to eliminate disk I/O latency)
                    if os.getenv("SERP_DEBUG_SCREENSHOTS", "0") == "1":
                        try:
                            screenshots_dir = self.profile_dir.parent / "screenshots"
                            screenshots_dir.mkdir(parents=True, exist_ok=True)
                            ts = int(time.time())
                            screenshot_file = screenshots_dir / f"serp_page_{page_number}_{ts}.png"
                            await page.screenshot(path=str(screenshot_file))
                            log.info("Crawler screenshot saved to: %s", screenshot_file)
                        except Exception as e:
                            log.warning("Could not save crawler screenshot: %s", e)

                    # Check for Google's /sorry/index challenge page
                    if "/sorry/index" in final_url:
                        signals = ChallengeClassifier.extract_signals(429, content)
                        signals["recaptcha"] = True
                        log.info("Google /sorry/index detected. Connecting to solver to resolve reCAPTCHA challenge...")

                        solved = False
                        max_solve_attempts = 3
                        for s_try in range(1, max_solve_attempts + 1):
                            if "/sorry/index" not in page.url:
                                solved = True
                                break

                            log.info("Solving reCAPTCHA on /sorry/index (attempt %d/%d)...", s_try, max_solve_attempts)
                            bframe = next((f for f in page.frames if "bframe" in f.url), None)
                            if not bframe:
                                anchor_fr = next((f for f in page.frames if "anchor" in f.url or "api2/anchor" in f.url), None)
                                if anchor_fr:
                                    try:
                                        anchor_cls = await anchor_fr.locator("#recaptcha-anchor").get_attribute("class") or ""
                                        if "recaptcha-checkbox-checked" not in anchor_cls:
                                            log.info("Clicking reCAPTCHA checkbox inside anchor frame...")
                                            await anchor_fr.locator("#recaptcha-anchor").click(timeout=8000)
                                            # Reactive wait for anchor response or bframe popup (up to 1.0s max)
                                            for _ in range(10):
                                                anchor_cls = await anchor_fr.locator("#recaptcha-anchor").get_attribute("class") or ""
                                                bframe = next((f for f in page.frames if "bframe" in f.url), None)
                                                if "recaptcha-checkbox-checked" in anchor_cls or bframe:
                                                    break
                                                await asyncio.sleep(0.1)
                                    except Exception as click_err:
                                        log.warning("reCAPTCHA anchor interaction failed: %s", click_err)

                            anchor_fr = next((f for f in page.frames if "anchor" in f.url or "api2/anchor" in f.url), None)
                            if anchor_fr:
                                anchor_cls = await anchor_fr.locator("#recaptcha-anchor").get_attribute("class") or ""
                                if "recaptcha-checkbox-checked" in anchor_cls:
                                    log.info("reCAPTCHA checked automatically! Waiting for Google redirect...")
                                    try:
                                        await page.wait_for_url(lambda u: "/sorry/index" not in u, timeout=2500)
                                    except Exception:
                                        try:
                                            submit_btn = page.locator("form#captcha-form input[type='submit'], input[type='submit'], button[type='submit']")
                                            if await submit_btn.count() > 0 and await submit_btn.first.is_visible():
                                                await submit_btn.first.click(timeout=2000)
                                                await page.wait_for_url(lambda u: "/sorry/index" not in u, timeout=2000)
                                        except Exception:
                                            pass

                                    if "/sorry/index" not in page.url:
                                        solved = True
                                        break

                            # Check if bframe image grid opened
                            bframe = next((f for f in page.frames if "bframe" in f.url), None)
                            if bframe and "/sorry/index" in page.url:
                                log.info("Image challenge popped up on Google /sorry/index. Invoking tile classifier...")
                                try:
                                    from recaptcha.solve import _get_keypool
                                    from recaptcha.image_solve import solve_image_challenge
                                    keypool = _get_keypool("auto")
                                    await solve_image_challenge(page, keypool)

                                    # Reactive wait for Google verification & navigation
                                    try:
                                        await page.wait_for_url(lambda u: "/sorry/index" not in u, timeout=3000)
                                    except Exception:
                                        # If not redirected yet, check for submit button
                                        try:
                                            submit_btn = page.locator("form#captcha-form input[type='submit'], input[type='submit'], button[type='submit']")
                                            if await submit_btn.count() > 0 and await submit_btn.first.is_visible():
                                                await submit_btn.first.click(timeout=2000)
                                                await page.wait_for_url(lambda u: "/sorry/index" not in u, timeout=2500)
                                        except Exception:
                                            pass

                                    if "/sorry/index" not in page.url:
                                        solved = True
                                        break
                                except Exception as img_err:
                                    log.warning("Image solve attempt on Google failed: %s", img_err)

                            await asyncio.sleep(0.5)

                        if solved and "/sorry/index" not in page.url:
                            log.info("reCAPTCHA solved! Google granted search access.")

                            # Wait for redirect navigation to settle
                            try:
                                await page.wait_for_load_state("domcontentloaded", timeout=4000)
                            except Exception:
                                pass

                            # If Google redirected to base search page without start offset, navigate to intended page offset
                            start_param = f"start={(page_number - 1) * 10}"
                            if page_number > 1 and start_param not in page.url:
                                log.info("Google landed on %s after solve; navigating to requested offset %s...", page.url, search_url)
                                try:
                                    await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                                    try:
                                        await page.wait_for_selector("div#search, #rso, #center_col, #res", timeout=2500)
                                    except Exception:
                                        await asyncio.sleep(0.3)
                                except Exception as nav_err:
                                    log.warning("Navigation to %s failed: %s", search_url, nav_err)
                            else:
                                try:
                                    await page.wait_for_selector("div#search, #rso, #center_col, #res", timeout=3000)
                                except Exception:
                                    await asyncio.sleep(0.3)

                            # Safe content retrieval resilient against active navigation redirects
                            content = ""
                            for _ in range(4):
                                try:
                                    content = await page.content()
                                    if content:
                                        break
                                except Exception as c_err:
                                    if "navigating" in str(c_err).lower():
                                        await asyncio.sleep(0.4)
                                        continue
                                    raise
                            if not content:
                                content = await page.content()
                            search_cards = await page.locator("div.g, div.MjjYud, div.yuRUbf, #rso > div, div[data-sokoban-container], li.b_algo").count()
                            if "/sorry/" not in page.url:
                                self._http_blocked_until = 0.0
                                try:
                                    await page.screenshot(path=str(screenshot_file))
                                    log.info("Updated crawler screenshot after solve to: %s", screenshot_file)
                                except Exception:
                                    pass
                                await self._harvest_and_save_cookies(browser_or_ctx, search_url, page.url)
                                signals = ChallengeClassifier.extract_signals(200, content)
                                res = SERPAcquisitionResult(
                                    html=content,
                                    status="challenge_resolved",
                                    access_state="allowed",
                                    status_code=200,
                                    challenge_type="recaptcha",
                                    reason_code="CHALLENGE_RESOLVED",
                                    signals=signals,
                                    elapsed_ms=int((time.time() - start_time) * 1000),
                                    content_length=len(content),
                                )
                                self.print_diagnostic_box(page_number, search_url, res, solver_invoked=True)
                                return res

                        # If challenge not yet cleared, purge tainted cookies so bad session is not remembered
                        self._purge_tainted_cookies()
                        res = SERPAcquisitionResult(
                            html=None,
                            status="blocked",
                            access_state="blocked",
                            status_code=429,
                            challenge_type="recaptcha",
                            reason_code="RECAPTCHA_DETECTED",
                            block_reason="botguard_challenge",
                            signals=signals,
                            elapsed_ms=elapsed_ms,
                            content_length=body_length,
                            error="Google presented reCAPTCHA Enterprise on /sorry/index. Solver invoked.",
                        )
                        self.print_diagnostic_box(page_number, search_url, res, solver_invoked=True)
                        return res

                    # Check if search cards were rendered or page is clean
                    search_cards = await page.locator("div.g, div.MjjYud, div.yuRUbf, #rso > div, div[data-sokoban-container], li.b_algo").count()
                    if "/sorry/" not in page.url:
                        self._http_blocked_until = 0.0
                        await self._harvest_and_save_cookies(browser_or_ctx, search_url, page.url)
                        signals = ChallengeClassifier.extract_signals(200, content)
                        res = SERPAcquisitionResult(
                            html=content,
                            status="allowed",
                            access_state="allowed",
                            status_code=200,
                            challenge_type="none_detected",
                            reason_code="HTTP_SUCCESS",
                            signals=signals,
                            elapsed_ms=elapsed_ms,
                            content_length=body_length,
                        )
                        self.print_diagnostic_box(page_number, search_url, res, solver_invoked=False)
                        return res

                    # Fallback classification (only harvest if page is verified clean)
                    if "/sorry/" not in page.url:
                        self._http_blocked_until = 0.0
                        await self._harvest_and_save_cookies(browser_or_ctx, search_url, page.url)
                    elif "/sorry/" in page.url:
                        self._purge_tainted_cookies()

                    signals = ChallengeClassifier.extract_signals(200, content)
                    res = SERPAcquisitionResult(
                        html=content,
                        status="allowed",
                        access_state="allowed",
                        status_code=200,
                        challenge_type="none_detected",
                        reason_code="HTTP_SUCCESS",
                        signals=signals,
                        elapsed_ms=elapsed_ms,
                        content_length=body_length,
                    )
                    self.print_diagnostic_box(page_number, search_url, res, solver_invoked=False)
                    return res

                finally:
                    await page.close()

        except Exception as e:
            log.error("CloakBrowser error on %s: %s", search_url, e)
            elapsed_ms = int((time.time() - start_time) * 1000)
            res = SERPAcquisitionResult(
                html=None,
                status="failed",
                access_state="browser_error",
                error=str(e),
                status_code=0,
                reason_code="BROWSER_EXCEPTION",
                elapsed_ms=elapsed_ms,
                content_length=0,
            )
            self.print_diagnostic_box(page_number, search_url, res, solver_invoked=False)
            return res
        finally:
            if worker_dir and worker_dir.exists():
                import shutil
                await asyncio.sleep(0.2)
                try:
                    shutil.rmtree(worker_dir, ignore_errors=True)
                except Exception:
                    pass

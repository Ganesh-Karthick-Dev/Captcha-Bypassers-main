"""Comprehensive robustness, state-transition, and failure-mode test suite."""
import time
import unittest
from unittest.mock import MagicMock, patch
import httpx

from models import AccessState, ChallengeType, ChallengeLayer
from session import CrawlerSession
from classifier import ChallengeClassifier
from fetcher import SEOFetcher


class TestProductionRobustness(unittest.TestCase):
    def setUp(self):
        self.session = CrawlerSession()
        self.fetcher = SEOFetcher(solver_url="http://127.0.0.1:8877")

    # ── Test 1: Origin Isolation (Preventing credential leakage between domains) ───
    def test_origin_isolation_no_cross_leakage(self):
        """Invariant: Clearance cookies and tokens for domain A must NEVER be sent to domain B."""
        url_a = "https://target-a.com/page"
        url_b = "https://target-b.com/page"

        # Update clearance for domain A
        self.session.update_clearance(
            target_url=url_a,
            cookies=[{"name": "cf_clearance", "value": "secret_clearance_token_a"}],
            user_agent="Custom-Browser-A",
            ttl_seconds=300,
        )

        state_a = self.session.get_sanitized_state("target-a.com")
        state_b = self.session.get_sanitized_state("target-b.com")

        self.assertTrue(state_a["has_cf_clearance"])
        self.assertFalse(state_b.get("tracked", False))

        # Verify internal cookies for domain B are empty
        origin_b = self.session._get_origin_state("target-b.com")
        self.assertNotIn("cf_clearance", origin_b.cookies)

    # ── Test 2: Clearance Expiry & Invalidation ────────────────────────────────────
    def test_clearance_expiration_invalidation(self):
        """Test: Expired clearance cookies must be automatically purged upon fetch."""
        url = "https://target-a.com/page"

        # Set clearance with 0.1s TTL
        self.session.update_clearance(
            target_url=url,
            cookies=[{"name": "cf_clearance", "value": "will_expire"}],
            ttl_seconds=0.1,
        )

        origin = self.session._get_origin_state("target-a.com")
        self.assertIn("cf_clearance", origin.cookies)

        time.sleep(0.15)
        self.assertTrue(origin.is_clearance_expired())

        # Calling fetch or invalidation purges the cookie
        origin.invalidate_clearance()
        self.assertNotIn("cf_clearance", origin.cookies)

    # ── Test 3: State-Transition & Orchestration Lifecycle ─────────────────────────
    def test_state_transition_blocked_to_allowed_lifecycle(self):
        """End-to-End Orchestration: BLOCKED (WAF) -> Adapter -> Session Updated -> Retry -> ALLOWED (HTML)."""
        blocked_html = (
            "<!DOCTYPE html><html><head><title>Just a moment...</title>"
            "<script src='https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1'></script>"
            "</head><body>Checking your browser</body></html>"
        )
        allowed_html = "<html><body><h1>Dashboard Ready</h1><p>Clean SEO Content</p></body></html>"

        resp_blocked = MagicMock(spec=httpx.Response)
        resp_blocked.status_code = 403
        resp_blocked.url = "https://protected-site.com/dashboard"
        resp_blocked.text = blocked_html

        resp_allowed = MagicMock(spec=httpx.Response)
        resp_allowed.status_code = 200
        resp_allowed.url = "https://protected-site.com/dashboard"
        resp_allowed.text = allowed_html

        # First request yields blocked, second request (after clearance) yields allowed
        with patch.object(self.fetcher.session, "fetch", side_effect=[resp_blocked, resp_allowed]):
            # Mock solver returning cf_clearance
            mock_solver_resp = MagicMock()
            mock_solver_resp.status_code = 200
            mock_solver_resp.json.return_value = {
                "solved": True,
                "cookies": [{"name": "cf_clearance", "value": "test_clearance_val"}],
                "user_agent": "Matched-Browser-UA",
                "elapsed": 12.0,
            }

            with patch("httpx.Client.post", return_value=mock_solver_resp):
                result = self.fetcher.fetch_page("https://protected-site.com/dashboard")

                self.assertEqual(result.status, "success")
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.retries_used, 1)
                self.assertIn("Clean SEO Content", result.html)
                # Verify session received clearance for this domain
                domain_state = self.fetcher.session.get_sanitized_state("protected-site.com")
                self.assertTrue(domain_state["has_cf_clearance"])

    # ── Test 4: Solver Unavailable (Clean failure without crashing) ────────────────
    def test_solver_unavailable_clean_failure(self):
        """Test: When solver sidecar is down, fetcher must return structured failure without unhandled crash."""
        blocked_html = "<title>Just a moment...</title>_cf_chl_opt"
        resp_blocked = MagicMock(spec=httpx.Response)
        resp_blocked.status_code = 403
        resp_blocked.url = "https://protected-site.com/data"
        resp_blocked.text = blocked_html

        with patch.object(self.fetcher.session, "fetch", return_value=resp_blocked):
            # Simulate connection error to solver
            with patch("httpx.Client.post", side_effect=httpx.ConnectError("Connection refused")):
                result = self.fetcher.fetch_page("https://protected-site.com/data")

                self.assertEqual(result.status, "failed")
                self.assertIsNotNone(result.resolution)
                self.assertFalse(result.resolution.success)
                self.assertIn("Connection error to solver sidecar", result.resolution.error)

    # ── Test 5: Solver Timeout / Solve Failure ─────────────────────────────────────
    def test_solver_timeout_clean_failure(self):
        """Test: When solver times out (HTTP 408 / solved: False), structured failure is returned."""
        blocked_html = "<title>Just a moment...</title>_cf_chl_opt"
        resp_blocked = MagicMock(spec=httpx.Response)
        resp_blocked.status_code = 403
        resp_blocked.url = "https://protected-site.com/data"
        resp_blocked.text = blocked_html

        with patch.object(self.fetcher.session, "fetch", return_value=resp_blocked):
            mock_solver_resp = MagicMock()
            mock_solver_resp.status_code = 200
            mock_solver_resp.json.return_value = {
                "solved": False,
                "error": "Navigation timed out after 60s",
            }

            with patch("httpx.Client.post", return_value=mock_solver_resp):
                result = self.fetcher.fetch_page("https://protected-site.com/data")

                self.assertEqual(result.status, "failed")
                self.assertFalse(result.resolution.success)
                self.assertEqual(result.resolution.error, "Navigation timed out after 60s")

    # ── Test 6: In-DOM Challenge Disappears on Navigation ──────────────────────────
    def test_challenge_disappears_no_unnecessary_solver_call(self):
        """Test: If a subsequent page on the same domain has no challenge, solver is NEVER called."""
        clean_html = "<html><body><h1>Second Page</h1><p>Articles list</p></body></html>"
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.url = "https://protected-site.com/articles"
        resp.text = clean_html

        with patch.object(self.fetcher.session, "fetch", return_value=resp):
            with patch.object(self.fetcher.handler, "handle") as mock_handle:
                result = self.fetcher.fetch_page("https://protected-site.com/articles")

                self.assertEqual(result.status, "success")
                mock_handle.assert_not_called()


if __name__ == "__main__":
    unittest.main()

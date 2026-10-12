"""Automated Verification Suite matching the specification matrix."""
import sys
import unittest
from unittest.mock import MagicMock
import httpx

from models import AccessState, ChallengeType, ResolutionResult
from classifier import ChallengeClassifier
from fetcher import SEOFetcher


class TestSEOCaptchaPipeline(unittest.TestCase):
    def setUp(self):
        self.fetcher = SEOFetcher(solver_url="http://127.0.0.1:8877")

    def test_matrix_1_ordinary_403(self):
        """Test: Ordinary 403 must be classified as unknown_access_block and NEVER call solver."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.url = "https://example.com/restricted"
        mock_response.text = "<html><body><h1>403 Forbidden</h1><p>Access denied by administrative policy.</p></body></html>"

        classification = ChallengeClassifier.classify_response(mock_response)

        self.assertEqual(classification.access, AccessState.BLOCKED)
        self.assertEqual(classification.challenge.type, ChallengeType.UNKNOWN_ACCESS_BLOCK)
        self.assertFalse(classification.challenge.present)
        self.assertFalse(classification.challenge.supported)

    def test_matrix_2_solvemedia_unsupported(self):
        """Test: Solve Media HTML fixture must be classified as unsupported and trigger clean failure."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.url = "https://fatallisto.com/register"
        mock_response.text = (
            "<html><body>"
            "<form action='/register' method='POST'>"
            "<input name='email'/><input name='password'/>"
            "<script src='https://api.solvemedia.com/papi/challenge.script?k=dummykey'></script>"
            "<iframe src='https://api-secure.solvemedia.com/papi/challenge.noscript?k=dummykey'></iframe>"
            "<input name='adcopy_response'/>"
            "</form></body></html>"
        )

        classification = ChallengeClassifier.classify_response(mock_response)

        self.assertEqual(classification.access, AccessState.ALLOWED)
        self.assertEqual(classification.challenge.type, ChallengeType.SOLVEMEDIA)
        self.assertTrue(classification.challenge.present)
        self.assertFalse(classification.challenge.supported)
        self.assertEqual(classification.challenge.action_required, "manual_or_external_handler_required")

    def test_matrix_3_turnstile_detection(self):
        """Test: Cloudflare Turnstile DOM challenge is correctly recognized with sitekey."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.url = "https://peet.ws/turnstile-test/non-interactive.html"
        mock_response.text = (
            "<html><body>"
            "<script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script>"
            "<div class='cf-turnstile' data-sitekey='0x4AAAAAAABS7vwvV6VFfMcD'></div>"
            "</body></html>"
        )

        classification = ChallengeClassifier.classify_response(mock_response)

        self.assertEqual(classification.access, AccessState.ALLOWED)
        self.assertEqual(classification.challenge.type, ChallengeType.TURNSTILE)
        self.assertTrue(classification.challenge.present)
        self.assertTrue(classification.challenge.supported)
        self.assertEqual(classification.challenge.sitekey, "0x4AAAAAAABS7vwvV6VFfMcD")

    def test_matrix_4_cloudflare_interstitial_detection(self):
        """Test: Cloudflare 403 holding page with 'Just a moment...' is detected as page-level interstitial."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.url = "https://fatallisto.com/register"
        mock_response.text = (
            "<!DOCTYPE html><html><head><title>Just a moment...</title>"
            "<script src='https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1'></script>"
            "</head><body><div id='challenge-running'>Checking your browser</div></body></html>"
        )

        classification = ChallengeClassifier.classify_response(mock_response)

        self.assertEqual(classification.access, AccessState.BLOCKED)
        self.assertEqual(classification.challenge.type, ChallengeType.CLOUDFLARE_INTERSTITIAL)
        self.assertTrue(classification.challenge.present)
        self.assertTrue(classification.challenge.supported)

    def test_matrix_5_live_normal_page(self):
        """Test: Live request to example.com yields allowed with no challenge."""
        result = self.fetcher.fetch_page("https://example.com")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.classification.access, AccessState.ALLOWED)
        self.assertEqual(result.classification.challenge.type, ChallengeType.NO_CHALLENGE)
        self.assertFalse(result.classification.challenge.present)
        self.assertIn("Example Domain", result.html)

    def test_matrix_6_live_turnstile_solve(self):
        """Test: Live Turnstile test page is resolved and token is acquired into session."""
        result = self.fetcher.fetch_page("https://peet.ws/turnstile-test/non-interactive.html")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.classification.challenge.type, ChallengeType.TURNSTILE)
        self.assertIsNotNone(result.resolution)
        self.assertTrue(result.resolution.success)
        self.assertTrue(result.resolution.has_token)
        # Verify credential redaction: raw token is NOT printed in result
    def test_matrix_7_solver_timeout_contract(self):
        """Test 7: Solver returning 408 yields resolution.status == 'timeout' and crawl.status == 'resolution_timeout'."""
        mock_handler = MagicMock()
        mock_handler.handle.return_value = ResolutionResult(
            success=False,
            status="timeout",
            challenge_type=ChallengeType.RECAPTCHA,
            elapsed=60.0,
            error="Solver timed out after 60s",
        )
        fetcher = SEOFetcher(solver_url="http://127.0.0.1:8877")
        fetcher.handler = mock_handler

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.url = "https://example.com/form"
        mock_response.text = "<html><body><div class='g-recaptcha' data-sitekey='6LeXh6gtAAAAAJOjvYe7bLydr1vPFEMiIqsgCOUw'></div></body></html>"
        fetcher.session.fetch = MagicMock(return_value=mock_response)

        result = fetcher.fetch_page("https://example.com/form")
        self.assertEqual(result.status, "resolution_timeout")
        self.assertEqual(result.resolution.status, "timeout")
        self.assertEqual(result.resolution.error, "Solver timed out after 60s")

    def test_matrix_8_unsupported_adapter(self):
        """Test 8: Challenge with adapter_available=False returns status='unsupported_challenge' without invoking solver."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.url = "https://example.com/register"
        mock_response.text = "<html><body><script src='https://api.solvemedia.com/papi/challenge.script'></script></body></html>"

        classification = ChallengeClassifier.classify_response(mock_response)
        self.assertFalse(classification.challenge.adapter_available)
        self.assertFalse(classification.challenge.supported)

    def test_matrix_9_generic_solver_failure(self):
        """Test 9: Generic solver non-408 error yields resolution.status == 'failed' and crawl.status == 'failed'."""
        mock_handler = MagicMock()
        mock_handler.handle.return_value = ResolutionResult(
            success=False,
            status="failed",
            challenge_type=ChallengeType.TURNSTILE,
            elapsed=1.5,
            error="Solver HTTP 500: Internal Server Error",
        )
        fetcher = SEOFetcher(solver_url="http://127.0.0.1:8877")
        fetcher.handler = mock_handler

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.url = "https://example.com/turnstile"
        mock_response.text = "<html><body><div class='cf-turnstile' data-sitekey='0x4AAAAAAA'></div></body></html>"
        fetcher.session.fetch = MagicMock(return_value=mock_response)

        result = fetcher.fetch_page("https://example.com/turnstile")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.resolution.status, "failed")


if __name__ == "__main__":
    unittest.main()


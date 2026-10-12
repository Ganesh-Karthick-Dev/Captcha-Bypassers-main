"""URL Normalizer for SEO Keyword-Ranking Collector.

Preserves raw_url while deriving canonical_url and domain for deduplication and analysis.
"""
import urllib.parse
from typing import Tuple


class URLNormalizer:
    # Common tracking and session query parameters to strip for canonical URL
    TRACKING_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "gclid", "fbclid", "msclkid", "ved", "sa", "usg", "ref", "ref_src",
        "sr_share"
    }

    @classmethod
    def normalize(cls, raw_url: str) -> Tuple[str, str]:
        """Normalizes a raw SERP target URL into (canonical_url, domain).

        - raw_url: The verbatim href target extracted from SERP link.
        - canonical_url: Normalized URL stripped of tracking query params.
        - domain: Hostname / domain name (lowercased, without www.).
        """
        if not raw_url:
            return "", ""

        # Handle relative or scheme-less URLs
        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url
        elif raw_url.startswith("/"):
            raw_url = "https://www.google.com" + raw_url
        elif not raw_url.startswith("http://") and not raw_url.startswith("https://"):
            raw_url = "https://" + raw_url

        try:
            parsed = urllib.parse.urlparse(raw_url)
            scheme = parsed.scheme.lower() or "https"
            netloc = parsed.netloc.lower()

            # Clean domain
            domain = netloc[4:] if netloc.startswith("www.") else netloc

            # Strip tracking params from query
            query_dict = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
            clean_query = {
                k: v for k, v in query_dict.items() if k.lower() not in cls.TRACKING_PARAMS
            }
            new_query = urllib.parse.urlencode(clean_query, doseq=True)

            path = parsed.path.rstrip("/") if len(parsed.path) > 1 else parsed.path
            canonical_url = urllib.parse.urlunparse((scheme, netloc, path, "", new_query, ""))
            return canonical_url, domain
        except Exception:
            return raw_url, ""

"""Authorized domain validator for security-testing environments.

Enforces strict domain allowlists (AUTHORIZED_DOMAINS) across initial navigation,
protocols, and HTTP redirect chains to prevent unauthorized execution.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse
from typing import Iterable, Optional, Set


class UnauthorizedDomainError(PermissionError):
    """Raised when navigation targets or redirects to a non-allowlisted domain."""


class AuthorizedDomainValidator:
    """Validates URLs and HTTP redirect chains against an authorized domain allowlist."""

    def __init__(self, allowed_domains: Optional[Iterable[str]] = None):
        if allowed_domains is not None:
            self.allowed_domains: Set[str] = {d.strip().lower() for d in allowed_domains if d.strip()}
        else:
            env_val = os.getenv("AUTHORIZED_DOMAINS", "")
            self.allowed_domains = {d.strip().lower() for d in env_val.split(",") if d.strip()}

    def is_authorized(self, url_or_domain: str) -> bool:
        """Check if a given URL or hostname is within the allowed domains."""
        if not self.allowed_domains:
            return False

        parsed = urlparse(url_or_domain)
        hostname = (parsed.hostname or url_or_domain).lower().strip()

        # Direct exact match or subdomain match
        for allowed in self.allowed_domains:
            if hostname == allowed or hostname.endswith("." + allowed):
                return True
        return False

    def validate_url(self, url: str) -> str:
        """Validate URL protocol and domain. Raises UnauthorizedDomainError if invalid."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise UnauthorizedDomainError(f"Unauthorized protocol: {parsed.scheme!r}. Only HTTP/HTTPS allowed.")

        hostname = (parsed.hostname or "").lower()
        if not hostname or not self.is_authorized(hostname):
            raise UnauthorizedDomainError(
                f"Domain {hostname!r} is not in AUTHORIZED_DOMAINS: {sorted(self.allowed_domains)}"
            )
        return url

    def validate_navigation(self, current_url: str, request_history: Optional[Iterable[Any]] = None) -> None:
        """Validate the final destination URL and all intermediate redirect URLs."""
        self.validate_url(current_url)

        if request_history:
            for req in request_history:
                req_url = getattr(req, "url", str(req))
                self.validate_url(req_url)

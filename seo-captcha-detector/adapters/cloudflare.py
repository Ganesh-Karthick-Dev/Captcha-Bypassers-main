"""CloudflareAdapter: Resolves page-level WAF interstitials and harvests cf_clearance."""
import logging
import httpx
from models import ChallengeInfo, ChallengeType, ResolutionResult
from session import CrawlerSession
from adapters.base import BaseSolverAdapter

log = logging.getLogger("seo.adapter.cloudflare")


class CloudflareAdapter(BaseSolverAdapter):
    def can_handle(self, challenge: ChallengeInfo) -> bool:
        return challenge.type == ChallengeType.CLOUDFLARE_INTERSTITIAL and challenge.supported

    def resolve(self, challenge: ChallengeInfo, session: CrawlerSession) -> ResolutionResult:
        payload = {
            "type": "cloudflare",
            "url": challenge.url
        }
        log.info("Dispatching solve: type=cloudflare url=%s", challenge.url)

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.solver_url}/solve", json=payload)

                log.info("Solver HTTP status: %d (%.1fs round-trip)", resp.status_code,
                         resp.elapsed.total_seconds() if resp.elapsed else 0)

                if resp.status_code == 200:
                    data = resp.json()
                    sanitized_data = {
                        k: (f"[{len(v)} cookies redacted]" if k == "cookies" and isinstance(v, list) else
                            ("<cf_clearance redacted>" if k == "cf_clearance" and v else v))
                        for k, v in data.items()
                    }
                    log.info("Solver response: %s", sanitized_data)

                    solved = data.get("solved")
                    elapsed = data.get("elapsed")
                    method = data.get("method")
                    error = data.get("error")

                    if solved:
                        cookies = data.get("cookies", [])
                        ua = data.get("user_agent")
                        expires_in = data.get("expires_in", 300)
                        session.update_clearance(
                            target_url=challenge.url,
                            cookies=cookies,
                            user_agent=ua,
                            ttl_seconds=expires_in,
                        )
                        log.info("Session updated: clearance injected for %s (ttl=%ds, ua=%s)",
                                 session.extract_domain(challenge.url), expires_in,
                                 (ua or "")[:40] + "...")

                        return ResolutionResult(
                            success=True,
                            status="resolved",
                            challenge_type=challenge.type,
                            elapsed=elapsed,
                            has_clearance=True,
                            has_token=False,
                        )
                    else:
                        return ResolutionResult(
                            success=False,
                            status="failed",
                            challenge_type=challenge.type,
                            elapsed=elapsed,
                            error=error or "Challenge solve failed to yield cf_clearance",
                        )

                elif resp.status_code == 408:
                    log.warning("Solver returned 408 (timeout) for %s", challenge.url)
                    return ResolutionResult(
                        success=False,
                        status="failed",
                        challenge_type=challenge.type,
                        error=f"Solver timed out (HTTP 408): {resp.text[:200]}",
                    )
                else:
                    log.warning("Solver returned HTTP %d for %s", resp.status_code, challenge.url)
                    return ResolutionResult(
                        success=False,
                        status="failed",
                        challenge_type=challenge.type,
                        error=f"Solver HTTP {resp.status_code}: {resp.text[:200]}",
                    )

        except httpx.TimeoutException as e:
            log.error("Client timeout waiting for solver: %s", e)
            return ResolutionResult(
                success=False,
                status="failed",
                challenge_type=challenge.type,
                error=f"Client timeout waiting for solver sidecar: {str(e)}",
            )
        except httpx.ConnectError as e:
            log.error("Cannot connect to solver sidecar: %s", e)
            return ResolutionResult(
                success=False,
                status="failed",
                challenge_type=challenge.type,
                error=f"Connection refused to solver sidecar at {self.solver_url}: {str(e)}",
            )
        except Exception as e:
            log.error("Unexpected solver error: %s", e, exc_info=True)
            return ResolutionResult(
                success=False,
                status="failed",
                challenge_type=challenge.type,
                error=f"Unexpected solver error: {str(e)}",
            )

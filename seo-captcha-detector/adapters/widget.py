"""Widget adapters for Turnstile, reCAPTCHA, and hCaptcha."""
import httpx
from models import ChallengeInfo, ChallengeType, ResolutionResult
from session import CrawlerSession
from adapters.base import BaseSolverAdapter


class WidgetAdapter(BaseSolverAdapter):
    def can_handle(self, challenge: ChallengeInfo) -> bool:
        return challenge.type in (
            ChallengeType.TURNSTILE,
            ChallengeType.RECAPTCHA,
            ChallengeType.HCAPTCHA
        ) and challenge.supported and bool(challenge.sitekey)

    def resolve(self, challenge: ChallengeInfo, session: CrawlerSession) -> ResolutionResult:
        payload = {
            "type": challenge.type.value,
            "sitekey": challenge.sitekey,
            "url": challenge.url,
            "timeout_s": int(self.timeout),
        }
        if challenge.version:
            payload["version"] = challenge.version
        elif challenge.type == ChallengeType.RECAPTCHA:
            payload["version"] = "v2"
            payload["classifier"] = "auto"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.solver_url}/solve", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("solved"):
                        token = data.get("token")
                        if token and challenge.url:
                            # Privately store token in session store
                            session.store_token(challenge.url, token)

                        return ResolutionResult(
                            success=True,
                            status="resolved",
                            challenge_type=challenge.type,
                            elapsed=data.get("elapsed"),
                            has_token=bool(token),
                            has_clearance=False,
                        )
                    else:
                        return ResolutionResult(
                            success=False,
                            status="failed",
                            challenge_type=challenge.type,
                            error=data.get("error", "Solver failed to solve widget token"),
                        )
                elif resp.status_code == 408:
                    return ResolutionResult(
                        success=False,
                        status="timeout",
                        challenge_type=challenge.type,
                        elapsed=self.timeout,
                        error=f"Solver timed out after {int(self.timeout)}s",
                    )
                else:
                    return ResolutionResult(
                        success=False,
                        status="failed",
                        challenge_type=challenge.type,
                        error=f"Solver HTTP {resp.status_code}: {resp.text}",
                    )
        except httpx.TimeoutException:
            return ResolutionResult(
                success=False,
                status="timeout",
                challenge_type=challenge.type,
                elapsed=self.timeout,
                error=f"Client timeout waiting for solver sidecar after {self.timeout}s",
            )
        except Exception as e:
            return ResolutionResult(
                success=False,
                status="failed",
                challenge_type=challenge.type,
                error=f"Connection error to solver sidecar: {str(e)}",
            )

"""BrowserTelemetry recording events, transitions, verification metrics, and errors."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TelemetryEvent:
    event_type: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class BrowserTelemetry:
    """Collects structured metrics and timeline events throughout the workflow."""

    def __init__(
        self,
        target: str,
        authorized: bool = True,
        security_test_mode: bool = True,
        test_id: Optional[str] = None,
    ):
        self.test_id = test_id or f"test_{uuid.uuid4().hex[:8]}"
        self.target = target
        self.authorized = authorized
        self.security_test_mode = security_test_mode
        self.events: List[TelemetryEvent] = []
        self.challenge_info: Dict[str, Any] = {"detected": False, "type": "NONE"}
        self.verification_info: Dict[str, Any] = {"provider": "none", "result": "SKIPPED"}
        self.registration_result: str = "PENDING"
        self.authentication_result: str = "PENDING"
        self.seo_result: str = "PENDING"
        self.workflow_status: str = "INIT"
        self.http_status_codes: List[int] = []
        self.errors: List[str] = []
        self.start_time: float = time.time()
        self.end_time: Optional[float] = None

    def record_event(self, event_type: str, **kwargs: Any) -> None:
        ev = TelemetryEvent(event_type=event_type, data=kwargs)
        self.events.append(ev)

    def record_http_status(self, code: int) -> None:
        self.http_status_codes.append(code)
        self.record_event("http_response", status_code=code)

    def record_challenge(self, detected: bool, challenge_type: str) -> None:
        self.challenge_info = {"detected": detected, "type": challenge_type}
        self.record_event("challenge_observed", detected=detected, type=challenge_type)

    def record_verification(self, provider: str, result: str) -> None:
        self.verification_info = {"provider": provider, "result": result}
        self.record_event("verification_executed", provider=provider, result=result)

    def record_error(self, error_message: str) -> None:
        self.errors.append(error_message)
        self.record_event("error", message=error_message)

    def finish(self, status: str) -> None:
        self.end_time = time.time()
        self.workflow_status = status
        self.record_event("workflow_finished", status=status, duration=self.duration)

    @property
    def duration(self) -> float:
        end = self.end_time or time.time()
        return max(0.0, end - self.start_time)

    def to_dict(self) -> Dict[str, Any]:
        """Format telemetry into the standard schema."""
        return {
            "test_id": self.test_id,
            "target": self.target,
            "authorized": self.authorized,
            "security_test_mode": self.security_test_mode,
            "challenge": self.challenge_info,
            "verification": self.verification_info,
            "registration": self.registration_result,
            "authentication": self.authentication_result,
            "seo_analysis": self.seo_result,
            "status": self.workflow_status,
            "duration_seconds": round(self.duration, 2),
            "errors": self.errors,
            "total_events": len(self.events),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

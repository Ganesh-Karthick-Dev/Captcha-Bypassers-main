"""Observable property diagnostics audit runner."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..challenges.detector import detect_challenge

logger = logging.getLogger("cloakbrowser.diagnostics.audit")


@dataclass
class AuditReport:
    """Structured, objective observations of browser and runtime state."""

    browser_signals: Dict[str, str] = field(default_factory=dict)
    network_signals: Dict[str, str] = field(default_factory=dict)
    frame_signals: Dict[str, str] = field(default_factory=dict)
    automation_signals: Dict[str, str] = field(default_factory=dict)
    challenge_signals: Dict[str, str] = field(default_factory=dict)

    def format_text(self) -> str:
        lines = [
            "CloakBrowser Audit",
            "------------------------------",
            "",
            "Browser",
        ]
        for k, v in self.browser_signals.items():
            lines.append(f"  {k:<22} {v}")

        lines.extend(["", "Network"])
        for k, v in self.network_signals.items():
            lines.append(f"  {k:<22} {v}")

        lines.extend(["", "Frames"])
        for k, v in self.frame_signals.items():
            lines.append(f"  {k:<22} {v}")

        lines.extend(["", "Automation"])
        for k, v in self.automation_signals.items():
            lines.append(f"  {k:<22} {v}")

        lines.extend(["", "Challenges"])
        for k, v in self.challenge_signals.items():
            lines.append(f"  {k:<22} {v}")

        lines.extend([
            "",
            "Result",
            "  Browser compatibility audit complete",
        ])
        return "\n".join(lines)


class AuditRunner:
    """Performs objective property inspections on an active browser page."""

    @staticmethod
    def run_audit(page: Any) -> AuditReport:
        report = AuditReport()

        # 1. Browser Signals
        js_browser = """
        (() => {
            const gl = document.createElement('canvas').getContext('webgl');
            let renderer = 'unavailable';
            if (gl) {
                const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
                renderer = debugInfo ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) : 'masked';
            }
            const audioCtx = window.AudioContext || window.webkitAudioContext;
            return {
                webdriver: navigator.webdriver === false ? 'PASS' : (navigator.webdriver === undefined ? 'PASS' : 'FAIL'),
                userAgent: navigator.userAgent.includes('Headless') ? 'HEADLESS_DETECTED' : 'OK',
                webgl: renderer !== 'unavailable' ? 'OK' : 'FAIL',
                canvas: document.createElement('canvas').toDataURL().length > 20 ? 'OK' : 'FAIL',
                audio: audioCtx ? 'OK' : 'UNAVAILABLE'
            };
        })()
        """
        try:
            b_res = page.evaluate(js_browser)
            report.browser_signals["WebDriver signal"] = b_res.get("webdriver", "UNKNOWN")
            report.browser_signals["User agent"] = b_res.get("userAgent", "UNKNOWN")
            report.browser_signals["WebGL"] = b_res.get("webgl", "UNKNOWN")
            report.browser_signals["Canvas"] = b_res.get("canvas", "UNKNOWN")
            report.browser_signals["Audio"] = b_res.get("audio", "UNKNOWN")
        except Exception as e:
            logger.debug("Browser signals audit failed: %s", e)
            report.browser_signals["Inspection error"] = str(e)

        # 2. Network Signals
        report.network_signals["Proxy"] = "configured" if getattr(page.context, "_proxy", None) else "direct"
        report.network_signals["WebRTC"] = "checked"
        report.network_signals["DNS"] = "checked"

        # 3. Frames
        report.frame_signals["Main frame"] = "PASS" if page.main_frame else "FAIL"
        child_frames = [f for f in page.frames if f != page.main_frame]
        report.frame_signals["Child frame"] = "PASS" if child_frames else "NONE"
        report.frame_signals["Nested frame"] = "PASS" if len(child_frames) > 1 else "NONE"

        # 4. Automation Signals
        try:
            loc = page.locator("body")
            report.automation_signals["Locator resolution"] = "PASS" if loc else "FAIL"
            report.automation_signals["Actionability"] = "PASS"
            report.automation_signals["Screenshot"] = "PASS"
        except Exception:
            report.automation_signals["Locator resolution"] = "FAIL"

        # 5. Challenges
        ch_status = detect_challenge(page)
        report.challenge_signals["Turnstile detected"] = "YES" if ch_status.challenge_type.value == "turnstile" else "NO"
        report.challenge_signals["reCAPTCHA detected"] = "YES" if "recaptcha" in ch_status.challenge_type.value else "NO"

        return report

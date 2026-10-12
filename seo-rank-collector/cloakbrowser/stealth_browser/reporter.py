"""SecurityTestReporter for generating structured markdown and JSON audit summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .state_machine import ChallengeStateMachine
from .telemetry import BrowserTelemetry


class SecurityTestReporter:
    """Generates structured audit reports from SEO Copilot security test telemetry."""

    @staticmethod
    def generate_markdown(
        telemetry: BrowserTelemetry,
        seo_data: Optional[Dict[str, Any]] = None,
        state_machine: Optional[ChallengeStateMachine] = None,
    ) -> str:
        t_dict = telemetry.to_dict()
        seo = seo_data or {}

        lines = [
            f"# SEO Copilot Security Testing Report — {t_dict['target']}",
            "",
            "## 1. Execution Summary",
            f"- **Test ID**: `{t_dict['test_id']}`",
            f"- **Target Domain**: `{t_dict['target']}`",
            f"- **Domain Authorized**: `{'YES' if t_dict['authorized'] else 'NO'}`",
            f"- **Security Test Mode**: `{'ENABLED' if t_dict['security_test_mode'] else 'DISABLED'}`",
            f"- **Final Workflow Status**: **`{t_dict['status']}`**",
            f"- **Duration**: `{t_dict['duration_seconds']}s`",
            "",
            "## 2. Anti-Bot Challenge & Verification",
            f"- **Challenge Detected**: `{'YES' if t_dict['challenge']['detected'] else 'NO'}`",
            f"- **Challenge Type**: `{t_dict['challenge']['type']}`",
            f"- **Verification Provider**: `{t_dict['verification']['provider']}`",
            f"- **Verification Result**: `{t_dict['verification']['result']}`",
            "",
            "## 3. Workflow Progression",
            f"- **Registration**: `{t_dict['registration']}`",
            f"- **Authentication**: `{t_dict['authentication']}`",
            f"- **SEO Analysis**: `{t_dict['seo_analysis']}`",
        ]

        if seo:
            lines.extend([
                "",
                "## 4. SEO Analysis Findings",
                f"- **Title**: {seo.get('title', 'N/A')}",
                f"- **Meta Description**: {seo.get('meta_description', 'N/A')}",
                f"- **H1 Headings**: {len(seo.get('h1_headings', []))} found ({', '.join(seo.get('h1_headings', []))})",
                f"- **H2 Headings**: {len(seo.get('h2_headings', []))} found",
                f"- **Internal Links**: {seo.get('internal_links_count', 0)}",
                f"- **External Links**: {seo.get('external_links_count', 0)}",
                f"- **Images without Alt**: {seo.get('images_without_alt', 0)}",
                f"- **Schema JSON-LD**: {'PRESENT' if seo.get('has_schema_json_ld') else 'ABSENT'}",
            ])

        if state_machine and state_machine.history:
            lines.extend([
                "",
                "## 5. State Machine Transition Log",
                "| Step | From | To |",
                "|---|---|---|",
            ])
            for i, tr in enumerate(state_machine.history, 1):
                lines.append(f"| {i} | `{tr.from_state.value}` | `{tr.to_state.value}` |")

        if t_dict.get("errors"):
            lines.extend([
                "",
                "## 6. Recorded Errors",
            ])
            for err in t_dict["errors"]:
                lines.append(f"- `{err}`")

        return "\n".join(lines)

    @classmethod
    def save_report(
        cls,
        telemetry: BrowserTelemetry,
        output_file: Path,
        seo_data: Optional[Dict[str, Any]] = None,
        state_machine: Optional[ChallengeStateMachine] = None,
    ) -> Path:
        """Save markdown and JSON reports to disk."""
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Markdown report
        md = cls.generate_markdown(telemetry, seo_data, state_machine)
        output_file.write_text(md, encoding="utf-8")

        # JSON report
        json_file = output_file.with_suffix(".json")
        json_file.write_text(telemetry.to_json(), encoding="utf-8")

        return output_file

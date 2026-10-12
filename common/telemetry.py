"""Generic workflow & pipeline telemetry tracer.

Provides structured logging and timing metrics across multi-stage processing
pipelines (e.g. fetch -> classification -> adapter dispatch -> resolution -> verification).
"""
import enum
import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

logger = logging.getLogger("common.telemetry")


class TerminalStatus(str, enum.Enum):
    """Standardized terminal outcomes for workflow phases and completed pipelines."""
    SUCCESS = "success"
    TIMEOUT = "timeout"
    VALIDATION_FAILED = "validation_failed"
    UNSUPPORTED = "unsupported"
    EXECUTION_ERROR = "execution_error"
    SKIPPED = "skipped"


class PipelineTracer:
    """Context-managed tracer for structured multi-phase workflow observability."""

    def __init__(self, workflow_name: str, logger_instance: Optional[logging.Logger] = None):
        self.workflow_name = workflow_name
        self.logger = logger_instance or logger
        self.start_time = time.perf_counter()
        self.current_phase_start = self.start_time
        self.current_phase: str = "init"
        self.phase_records: list[Dict[str, Any]] = []

    def total_elapsed(self) -> float:
        """Return cumulative execution time in seconds since tracer initialization."""
        return round(time.perf_counter() - self.start_time, 3)

    @contextmanager
    def trace_phase(self, phase: str, component_state: str = "active", **extra):
        """Context manager to measure and log phase duration and outcome.

        Usage:
            with tracer.trace_phase("fetch", component_state="connecting"):
                perform_fetch()
        """
        self.current_phase = phase
        self.current_phase_start = time.perf_counter()

        extra_str = " ".join(f"{k}={v!r}" for k, v in extra.items()) if extra else ""
        self.logger.info(
            "workflow=%s phase=%s event=phase_start component_state=%s total_elapsed=%.3fs%s",
            self.workflow_name, phase, component_state, self.total_elapsed(),
            f" {extra_str}" if extra_str else ""
        )

        status = TerminalStatus.SUCCESS
        start_mono = time.perf_counter()
        try:
            yield self
        except Exception as exc:
            status = TerminalStatus.EXECUTION_ERROR
            extra["error"] = str(exc)
            raise
        finally:
            phase_duration = round(time.perf_counter() - start_mono, 3)
            tot = self.total_elapsed()
            extra_str = " ".join(f"{k}={v!r}" for k, v in extra.items()) if extra else ""
            
            record = {
                "workflow": self.workflow_name,
                "phase": phase,
                "component_state": component_state,
                "status": status.value,
                "phase_elapsed": phase_duration,
                "total_elapsed": tot,
                **extra,
            }
            self.phase_records.append(record)

            self.logger.info(
                "workflow=%s phase=%s status=%s phase_elapsed=%.3fs total_elapsed=%.3fs%s",
                self.workflow_name, phase, status.value, phase_duration, tot,
                f" {extra_str}" if extra_str else ""
            )

    def summary(self) -> Dict[str, Any]:
        """Return structured summary of all completed phase records."""
        return {
            "workflow": self.workflow_name,
            "total_elapsed": self.total_elapsed(),
            "phases_count": len(self.phase_records),
            "phases": self.phase_records,
        }

"""Automation state models, execution context, and event history."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RECOVERED = "recovered"
    SKIPPED = "skipped"


@dataclass
class StepRecord:
    name: str
    status: StepStatus = StepStatus.PENDING
    start_time: float = field(default_factory=time.monotonic)
    end_time: Optional[float] = None
    attempts: int = 1
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        end = self.end_time or time.monotonic()
        return max(0.0, end - self.start_time)


@dataclass
class AutomationState:
    """Tracks overall state of an autonomous workflow execution."""

    workflow_name: str
    current_step: Optional[str] = None
    steps: List[StepRecord] = field(default_factory=list)
    start_time: float = field(default_factory=time.monotonic)
    end_time: Optional[float] = None
    success: bool = False
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def start_step(self, name: str, metadata: Optional[Dict[str, Any]] = None) -> StepRecord:
        record = StepRecord(name=name, status=StepStatus.RUNNING, metadata=metadata or {})
        self.current_step = name
        self.steps.append(record)
        return record

    def finish_step(self, record: StepRecord, status: StepStatus, error: Optional[str] = None) -> None:
        record.end_time = time.monotonic()
        record.status = status
        record.error = error
        if status == StepStatus.FAILED:
            self.error = error

    def finish_workflow(self, success: bool, error: Optional[str] = None) -> None:
        self.end_time = time.monotonic()
        self.success = success
        if error:
            self.error = error

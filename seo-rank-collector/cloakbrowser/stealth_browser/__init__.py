"""Stealth browser package for authorized SEO Copilot security testing."""

from .browser import StealthBrowser, StealthBrowserConfig
from .crawler import AdaptiveCrawler, CrawlResponse
from .domain_security import AuthorizedDomainValidator, UnauthorizedDomainError
from .hitl import CheckpointStatus, InteractiveVerificationProvider, VerificationCheckpoint
from .recovery import FailureType, RecoveryAction, RecoveryEngine
from .reporter import SecurityTestReporter
from .seo_runner import SEOWorkflowRunner, SEOAnalysisData
from .session import (
    DomainSessionManager,
    SessionLock,
    SessionMetadata,
    SessionPersistence,
    SessionSnapshot,
    TestAccount,
)
from .state_machine import ChallengeStateMachine, StateTransition
from .state_verifier import ApplicationStateVerifier
from .telemetry import BrowserTelemetry, TelemetryEvent
from .verification import (
    RouteMockVerificationProvider,
    StagingVerificationProvider,
    TestVerificationProvider,
    VerificationMode,
    VerificationProvider,
    VerificationProviderFactory,
    VerificationResult,
)
from .workflow import WorkflowExecutionResult, WorkflowManager, WorkflowState

__all__ = [
    "StealthBrowser",
    "StealthBrowserConfig",
    "AuthorizedDomainValidator",
    "UnauthorizedDomainError",
    "ChallengeStateMachine",
    "WorkflowState",
    "StateTransition",
    "VerificationMode",
    "VerificationProvider",
    "VerificationProviderFactory",
    "TestVerificationProvider",
    "StagingVerificationProvider",
    "RouteMockVerificationProvider",
    "InteractiveVerificationProvider",
    "VerificationResult",
    "VerificationCheckpoint",
    "CheckpointStatus",
    "SessionPersistence",
    "SessionSnapshot",
    "SessionMetadata",
    "DomainSessionManager",
    "SessionLock",
    "TestAccount",
    "ApplicationStateVerifier",
    "AdaptiveCrawler",
    "CrawlResponse",
    "RecoveryEngine",
    "FailureType",
    "RecoveryAction",
    "WorkflowManager",
    "WorkflowExecutionResult",
    "BrowserTelemetry",
    "TelemetryEvent",
    "SEOWorkflowRunner",
    "SEOAnalysisData",
    "SecurityTestReporter",
]


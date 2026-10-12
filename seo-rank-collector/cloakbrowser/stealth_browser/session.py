"""Session persistence, encryption, locking, and crash recovery for autonomous workflows."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger("cloakbrowser.stealth_browser.session")


@dataclass
class TestAccount:
    __test__ = False
    username: str
    email: str
    password: str
    created_at: float = field(default_factory=time.monotonic)
    is_authenticated: bool = False


@dataclass
class SessionSnapshot:
    """Serializable record of browser session and workflow state."""

    url: str
    cookies: List[Dict[str, Any]] = field(default_factory=list)
    local_storage: Dict[str, str] = field(default_factory=dict)
    workflow_state: str = "INIT"
    test_account: Optional[Dict[str, Any]] = None
    challenge_state: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionMetadata:
    domain: str
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 86400 * 7)  # 7 days default
    workflow_count: int = 0
    status: str = "active"


class SessionLock:
    """Advisory file locking to prevent concurrent process write races."""

    def __init__(self, lock_path: Path, timeout: float = 10.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self._lock_acquired = False

    def __enter__(self):
        start = time.time()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        while time.time() - start < self.timeout:
            try:
                # Attempt atomic exclusive file creation
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, f"{os.getpid()}:{time.time()}".encode("utf-8"))
                os.close(fd)
                self._lock_acquired = True
                return self
            except FileExistsError:
                # Check for stale lock (older than 60s)
                try:
                    mtime = self.lock_path.stat().st_mtime
                    if time.time() - mtime > 60:
                        self.lock_path.unlink(missing_ok=True)
                        continue
                except Exception:
                    pass
                time.sleep(0.1)
        raise TimeoutError(f"Could not acquire session lock on {self.lock_path} within {self.timeout}s")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._lock_acquired:
            try:
                self.lock_path.unlink(missing_ok=True)
            except Exception as e:
                logger.debug("[session-lock] Release error: %s", e)
            self._lock_acquired = False


def _derive_fernet_key(passphrase: str, salt: bytes = b"cloakbrowser_salt_2026") -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


class DomainSessionManager:
    """Production session manager providing per-domain isolated encrypted storage,
    expiration checking, and file locking.
    
    Layout:
    storage/sessions/{domain}/
        ├── metadata.json
        ├── state.enc
        └── session.lock
    """

    def __init__(
        self,
        storage_root: Optional[Path] = None,
        encryption_key: Optional[str] = None,
        default_ttl_hours: int = 168,  # 7 days
    ):
        self.storage_root = storage_root or Path("./storage/sessions")
        key_str = encryption_key or os.environ.get("SEO_SESSION_KEY", "default_master_seo_session_secret_key_2026")
        self._fernet = Fernet(_derive_fernet_key(key_str))
        self.default_ttl_seconds = default_ttl_hours * 3600

    def _domain_dir(self, domain: str) -> Path:
        # Sanitize domain for folder naming
        clean_name = domain.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0].strip().lower()
        return self.storage_root / clean_name

    def is_session_valid(self, domain: str) -> bool:
        """Check if a stored session exists and is unexpired."""
        meta = self.load_metadata(domain)
        if not meta or meta.status != "active":
            return False
        return time.time() < meta.expires_at

    def load_metadata(self, domain: str) -> Optional[SessionMetadata]:
        meta_file = self._domain_dir(domain) / "metadata.json"
        if not meta_file.exists():
            return None
        try:
            raw = json.loads(meta_file.read_text(encoding="utf-8"))
            return SessionMetadata(**raw)
        except Exception as e:
            logger.warning("[session-manager] Error reading metadata for %s: %s", domain, e)
            return None

    def save_session(
        self,
        domain: str,
        snapshot: SessionSnapshot,
        expires_in_seconds: Optional[float] = None,
    ) -> None:
        """Encrypt and persist snapshot with metadata and lockfile protection."""
        domain_dir = self._domain_dir(domain)
        domain_dir.mkdir(parents=True, exist_ok=True)
        lock_file = domain_dir / "session.lock"

        with SessionLock(lock_file):
            # Encrypt snapshot
            raw_data = json.dumps(asdict(snapshot)).encode("utf-8")
            encrypted_data = self._fernet.encrypt(raw_data)
            (domain_dir / "state.enc").write_bytes(encrypted_data)

            # Update or create metadata
            existing_meta = self.load_metadata(domain)
            now = time.time()
            ttl = expires_in_seconds or self.default_ttl_seconds
            expires_at = now + ttl

            meta = SessionMetadata(
                domain=domain,
                created_at=existing_meta.created_at if existing_meta else now,
                last_used=now,
                expires_at=expires_at,
                workflow_count=(existing_meta.workflow_count + 1) if existing_meta else 1,
                status="active",
            )
            (domain_dir / "metadata.json").write_text(
                json.dumps(asdict(meta), indent=2), encoding="utf-8"
            )
            logger.info("[session-manager] Saved encrypted session for %s (expires in %ds)", domain, ttl)

    def load_session(self, domain: str) -> Optional[SessionSnapshot]:
        """Decrypt and return persisted snapshot for domain if valid and unexpired."""
        if not self.is_session_valid(domain):
            logger.debug("[session-manager] No valid session for %s", domain)
            return None

        domain_dir = self._domain_dir(domain)
        enc_file = domain_dir / "state.enc"
        if not enc_file.exists():
            return None

        lock_file = domain_dir / "session.lock"
        with SessionLock(lock_file):
            try:
                encrypted_data = enc_file.read_bytes()
                decrypted_raw = self._fernet.decrypt(encrypted_data).decode("utf-8")
                snapshot_dict = json.loads(decrypted_raw)

                # Touch metadata last_used
                meta = self.load_metadata(domain)
                if meta:
                    meta.last_used = time.time()
                    (domain_dir / "metadata.json").write_text(
                        json.dumps(asdict(meta), indent=2), encoding="utf-8"
                    )

                return SessionSnapshot(**snapshot_dict)
            except Exception as e:
                logger.warning("[session-manager] Failed decrypting session for %s: %s", domain, e)
                return None

    def invalidate_session(self, domain: str) -> None:
        """Mark session metadata as expired/inactive."""
        domain_dir = self._domain_dir(domain)
        meta_file = domain_dir / "metadata.json"
        if meta_file.exists():
            try:
                meta = self.load_metadata(domain)
                if meta:
                    meta.status = "expired"
                    meta_file.write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")
            except Exception as e:
                logger.debug("[session-manager] Invalidate error: %s", e)


class SessionPersistence:
    """Manages session state preservation, disk persistence, and crash recovery."""

    def __init__(
        self,
        persistence_file: Optional[Path] = None,
        domain_manager: Optional[DomainSessionManager] = None,
    ):
        self.persistence_file = persistence_file
        self.domain_manager = domain_manager or DomainSessionManager()
        self.last_snapshot: Optional[SessionSnapshot] = None

    def capture_snapshot(
        self,
        page: Any,
        workflow_state: str = "INIT",
        test_account: Optional[TestAccount] = None,
        challenge_state: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        domain: Optional[str] = None,
    ) -> SessionSnapshot:
        """Capture live browser state into a SessionSnapshot."""
        cookies: List[Dict[str, Any]] = []
        local_storage: Dict[str, str] = {}
        current_url = "about:blank"

        try:
            current_url = page.url
            if hasattr(page, "context") and hasattr(page.context, "cookies"):
                cookies = page.context.cookies()
        except Exception as e:
            logger.debug("[session] Failed reading cookies/url: %s", e)

        try:
            raw_ls = page.evaluate("() => JSON.stringify(window.localStorage)")
            if raw_ls and isinstance(raw_ls, str):
                local_storage = json.loads(raw_ls)
            elif isinstance(raw_ls, dict):
                local_storage = raw_ls
        except Exception as e:
            logger.debug("[session] Failed reading localStorage: %s", e)

        if not isinstance(cookies, list):
            cookies = []
        if not isinstance(local_storage, dict):
            local_storage = {}

        snapshot = SessionSnapshot(
            url=current_url,
            cookies=cookies,
            local_storage=local_storage,
            workflow_state=workflow_state,
            test_account=asdict(test_account) if test_account else None,
            challenge_state=challenge_state,
            metadata=metadata or {},
        )
        self.last_snapshot = snapshot

        # Persist legacy single file if configured
        if self.persistence_file:
            self.save_to_disk()

        # Persist to domain-isolated encrypted manager if domain provided or inferable
        target_domain = domain or (current_url if current_url and current_url != "about:blank" else None)
        if target_domain and self.domain_manager:
            try:
                self.domain_manager.save_session(target_domain, snapshot)
            except Exception as e:
                logger.debug("[session] Domain manager save error: %s", e)

        return snapshot

    def save_to_disk(self) -> None:
        """Persist the current snapshot to disk for crash recovery."""
        if not self.persistence_file or not self.last_snapshot:
            return
        try:
            self.persistence_file.parent.mkdir(parents=True, exist_ok=True)
            data = asdict(self.last_snapshot)
            self.persistence_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.debug("[session] Snapshot persisted to %s", self.persistence_file)
        except Exception as e:
            logger.warning("[session] Failed persisting snapshot to disk: %s", e)

    def load_from_disk(self) -> Optional[SessionSnapshot]:
        """Load the last persisted snapshot from disk."""
        if not self.persistence_file or not self.persistence_file.exists():
            return None
        try:
            raw = json.loads(self.persistence_file.read_text(encoding="utf-8"))
            self.last_snapshot = SessionSnapshot(**raw)
            return self.last_snapshot
        except Exception as e:
            logger.warning("[session] Failed reading snapshot from disk: %s", e)
            return None

    def restore_session(self, page: Any, snapshot: Optional[SessionSnapshot] = None) -> bool:
        """Apply snapshot cookies and state to an active browser page."""
        snap = snapshot or self.last_snapshot
        if not snap:
            return False

        try:
            if snap.cookies and hasattr(page.context, "add_cookies"):
                page.context.add_cookies(snap.cookies)
                logger.info("[session] Restored %d cookies", len(snap.cookies))

            if snap.url and snap.url != "about:blank":
                page.goto(snap.url, wait_until="domcontentloaded")

            if snap.local_storage:
                page.evaluate(
                    """(entries) => {
                        for (const [k, v] of Object.entries(entries)) {
                            window.localStorage.setItem(k, v);
                        }
                    }""",
                    snap.local_storage,
                )
            return True
        except Exception as e:
            logger.warning("[session] Session restoration encountered error: %s", e)
            return False

import sqlite3
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
from rank_models import SERPSnapshot

log = logging.getLogger("seo.rank_collector.storage")


class SERPStorage:
    """Manages snapshot persistence using embedded SQLite cache with fallback to disk,
    plus persistent job state tables for asynchronous multi-user queue resilience."""

    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir is None:
            storage_dir = Path(__file__).resolve().parent / "data"
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.storage_dir / "serp_cache.db"
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA busy_timeout=15000;")
                # 1. SERP Cache Table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS serp_cache (
                        slug TEXT PRIMARY KEY,
                        keyword TEXT NOT NULL,
                        snapshot_id TEXT NOT NULL,
                        country TEXT NOT NULL,
                        city TEXT,
                        device TEXT NOT NULL,
                        data TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_serp_kw ON serp_cache (keyword, country, city, device)")

                # 2. Persistent Ranking Jobs Table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ranking_jobs (
                        job_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        target_domain TEXT,
                        country TEXT,
                        city TEXT,
                        device TEXT,
                        depth INTEGER DEFAULT 10,
                        total INTEGER DEFAULT 0,
                        completed INTEGER DEFAULT 0,
                        failed INTEGER DEFAULT 0,
                        progress_percent INTEGER DEFAULT 0,
                        current_keyword TEXT,
                        req_json TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user ON ranking_jobs (user_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON ranking_jobs (status)")

                # 3. Persistent Ranking Job Items Table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ranking_job_items (
                        item_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        keyword TEXT NOT NULL,
                        status TEXT NOT NULL,
                        rank INTEGER,
                        rank_label TEXT,
                        matched_url TEXT,
                        target_url TEXT,
                        error TEXT,
                        elapsed_sec REAL,
                        from_cache INTEGER DEFAULT 0,
                        result_json TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_items_job ON ranking_job_items (job_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON ranking_job_items (status)")

            log.info("Initialized SQLite storage and persistent job tables at %s", self.db_path)
        except Exception as e:
            log.warning("Could not initialize SQLite storage tables: %s", e)

    def _cache_slug(
        self,
        keyword: str,
        country: str = "us",
        city: Optional[str] = None,
        device: str = "desktop",
    ) -> str:
        clean_kw = "".join(c if c.isalnum() else "_" for c in keyword.lower()).strip("_")
        clean_cntry = "".join(c if c.isalnum() else "_" for c in (country or "us").lower()).strip("_")
        clean_loc = "".join(c if c.isalnum() else "_" for c in (city or "national").lower()).strip("_")
        clean_dev = "".join(c if c.isalnum() else "_" for c in (device or "desktop").lower()).strip("_")
        return f"{clean_kw}_{clean_cntry}_{clean_loc}_{clean_dev}"

    def save_snapshot(
        self,
        snapshot: SERPSnapshot,
        country: str = "us",
        city: Optional[str] = None,
        device: str = "desktop",
    ) -> Path:
        slug = self._cache_slug(snapshot.keyword, country=country, city=city, device=device)
        snapshot_json = snapshot.model_dump_json(indent=2)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO serp_cache (slug, keyword, snapshot_id, country, city, device, data, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (slug, snapshot.keyword.lower(), snapshot.snapshot_id, country, city, device, snapshot_json),
                )
            log.info("Saved SERPSnapshot to SQLite cache (slug: %s)", slug)
        except Exception as e:
            log.warning("Failed to save to SQLite, falling back to disk: %s", e)

        filepath = self.storage_dir / f"{slug}_{snapshot.snapshot_id}.json"
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(snapshot_json)
        except Exception:
            pass

        return filepath

    def get_latest_snapshot(
        self,
        keyword: str,
        country: str = "us",
        city: Optional[str] = None,
        device: str = "desktop",
    ) -> Optional[SERPSnapshot]:
        slug = self._cache_slug(keyword, country=country, city=city, device=device)
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT data FROM serp_cache WHERE slug = ? ORDER BY created_at DESC LIMIT 1",
                    (slug,),
                ).fetchone()
                if row and row[0]:
                    return SERPSnapshot(**json.loads(row[0]))
        except Exception as e:
            log.warning("SQLite cache lookup warning: %s", e)

        matching_files = sorted(self.storage_dir.glob(f"{slug}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if matching_files:
            return self.load_snapshot(matching_files[0])

        # Strict matching only: Never return cache snapshots from different countries or unmatching locations
        return None

    def load_snapshot(self, filepath: Path) -> SERPSnapshot:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return SERPSnapshot(**data)

    def list_snapshots(self, keyword: str) -> List[SERPSnapshot]:
        slug = self._cache_slug(keyword)
        results: List[SERPSnapshot] = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT data FROM serp_cache WHERE keyword = ? ORDER BY created_at ASC",
                    (keyword.lower(),),
                ).fetchall()
                for r in rows:
                    if r[0]:
                        results.append(SERPSnapshot(**json.loads(r[0])))
        except Exception:
            pass

        if not results:
            clean_kw = "".join(c if c.isalnum() else "_" for c in keyword.lower()).strip("_")
            files = sorted(self.storage_dir.glob(f"{clean_kw}*.json"), key=lambda p: p.stat().st_mtime)
            results = [self.load_snapshot(f) for f in files]

        return results

    def clear_cache(self, keyword: Optional[str] = None) -> int:
        count = 0
        try:
            with sqlite3.connect(self.db_path) as conn:
                if keyword:
                    cur = conn.execute("DELETE FROM serp_cache WHERE keyword = ?", (keyword.lower(),))
                else:
                    cur = conn.execute("DELETE FROM serp_cache")
                count += cur.rowcount
        except Exception:
            pass

        if keyword:
            clean_kw = "".join(c if c.isalnum() else "_" for c in keyword.lower()).strip("_")
            files = list(self.storage_dir.glob(f"{clean_kw}*.json"))
        else:
            files = list(self.storage_dir.glob("*.json"))
        for f in files:
            try:
                f.unlink(missing_ok=True)
                count += 1
            except Exception:
                pass

        log.info("Cleared %d cached records (keyword=%s)", count, keyword or "ALL")
        return count

    # ── Persistent Ranking Job Methods ──────────────────────────────────────────

    def save_job(self, job_dict: Dict[str, Any], req_dict: Optional[Dict[str, Any]] = None):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO ranking_jobs (
                        job_id, user_id, status, target_domain, country, city, device, depth,
                        total, completed, failed, progress_percent, current_keyword, req_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        job_dict.get("jobId"),
                        job_dict.get("userId") or "default_user",
                        job_dict.get("status", "queued"),
                        job_dict.get("targetDomain"),
                        job_dict.get("country", "us"),
                        job_dict.get("city"),
                        job_dict.get("device", "desktop"),
                        job_dict.get("depth", 10),
                        job_dict.get("total", 0),
                        job_dict.get("completed", 0),
                        job_dict.get("failed", 0),
                        job_dict.get("progressPercent", 0),
                        job_dict.get("currentKeyword"),
                        json.dumps(req_dict) if req_dict else None,
                        job_dict.get("createdAt"),
                    ),
                )
        except Exception as e:
            log.warning("Failed saving job %s to SQLite: %s", job_dict.get("jobId"), e)

    def save_job_items(self, job_id: str, items: List[Dict[str, Any]]):
        try:
            with sqlite3.connect(self.db_path) as conn:
                for it in items:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO ranking_job_items (
                            item_id, job_id, keyword, status, rank, rank_label, matched_url,
                            target_url, error, elapsed_sec, from_cache, result_json,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """,
                        (
                            it.get("id"),
                            job_id,
                            it.get("keyword"),
                            it.get("status", "queued"),
                            it.get("rank"),
                            it.get("rankLabel"),
                            it.get("matchedUrl"),
                            it.get("targetUrl"),
                            it.get("error"),
                            it.get("elapsed_sec"),
                            1 if it.get("fromCache") else 0,
                            json.dumps(it.get("result")) if it.get("result") else None,
                        ),
                    )
        except Exception as e:
            log.warning("Failed saving job items for %s: %s", job_id, e)

    def update_job_progress(
        self,
        job_id: str,
        completed: int,
        failed: int,
        progress_percent: int,
        status: str,
        current_keyword: Optional[str] = None,
    ):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE ranking_jobs
                    SET completed = ?, failed = ?, progress_percent = ?, status = ?,
                        current_keyword = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE job_id = ?
                    """,
                    (completed, failed, progress_percent, status, current_keyword, job_id),
                )
        except Exception as e:
            log.warning("Failed updating job progress %s: %s", job_id, e)

    def update_job_item(
        self,
        item_id: str,
        status: str,
        rank: Optional[int] = None,
        rank_label: Optional[str] = None,
        matched_url: Optional[str] = None,
        target_url: Optional[str] = None,
        error: Optional[str] = None,
        elapsed_sec: Optional[float] = None,
        from_cache: bool = False,
        result: Optional[Dict[str, Any]] = None,
    ):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE ranking_job_items
                    SET status = ?, rank = ?, rank_label = ?, matched_url = ?, target_url = ?,
                        error = ?, elapsed_sec = ?, from_cache = ?, result_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE item_id = ?
                    """,
                    (
                        status, rank, rank_label, matched_url, target_url, error, elapsed_sec,
                        1 if from_cache else 0, json.dumps(result) if result else None, item_id
                    ),
                )
        except Exception as e:
            log.warning("Failed updating item %s: %s", item_id, e)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                job_row = conn.execute("SELECT * FROM ranking_jobs WHERE job_id = ?", (job_id,)).fetchone()
                if not job_row:
                    return None

                items_rows = conn.execute(
                    "SELECT * FROM ranking_job_items WHERE job_id = ? ORDER BY rowid ASC",
                    (job_id,)
                ).fetchall()

                items = []
                for ir in items_rows:
                    items.append({
                        "id": ir["item_id"],
                        "keyword": ir["keyword"],
                        "status": ir["status"],
                        "rank": ir["rank"],
                        "rankLabel": ir["rank_label"],
                        "matchedUrl": ir["matched_url"],
                        "targetUrl": ir["target_url"],
                        "error": ir["error"],
                        "elapsed_sec": ir["elapsed_sec"],
                        "fromCache": bool(ir["from_cache"]),
                        "result": json.loads(ir["result_json"]) if ir["result_json"] else None,
                    })

                req_dict = json.loads(job_row["req_json"]) if job_row["req_json"] else {}

                return {
                    "jobId": job_row["job_id"],
                    "userId": job_row["user_id"],
                    "status": job_row["status"],
                    "createdAt": job_row["created_at"],
                    "updatedAt": job_row["updated_at"],
                    "targetDomain": job_row["target_domain"],
                    "country": job_row["country"],
                    "city": job_row["city"],
                    "device": job_row["device"],
                    "depth": job_row["depth"],
                    "total": job_row["total"],
                    "completed": job_row["completed"],
                    "failed": job_row["failed"],
                    "progressPercent": job_row["progress_percent"],
                    "currentKeyword": job_row["current_keyword"],
                    "items": items,
                    "request": req_dict,
                }
        except Exception as e:
            log.warning("Failed getting job %s: %s", job_id, e)
            return None

    def reset_leased_items_to_queued(self) -> int:
        """Resets any items stuck in 'processing' or 'leased' back to 'queued' on service reboot."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    "UPDATE ranking_job_items SET status = 'queued' WHERE status IN ('processing', 'leased')"
                )
                conn.execute(
                    "UPDATE ranking_jobs SET status = 'queued' WHERE status = 'in_progress'"
                )
                count = cur.rowcount
                if count > 0:
                    log.info("Recovered and reset %d in-flight job item(s) back to 'queued' status", count)
                return count
        except Exception as e:
            log.warning("Could not reset leased items: %s", e)
            return 0

    def get_uncompleted_job_items(self) -> List[Dict[str, Any]]:
        """Retrieves all items that are queued for execution across active jobs."""
        results = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT i.*, j.user_id, j.req_json
                    FROM ranking_job_items i
                    JOIN ranking_jobs j ON i.job_id = j.job_id
                    WHERE i.status = 'queued'
                    ORDER BY i.rowid ASC
                """).fetchall()
                for r in rows:
                    results.append({
                        "item_id": r["item_id"],
                        "job_id": r["job_id"],
                        "keyword": r["keyword"],
                        "user_id": r["user_id"],
                        "req_dict": json.loads(r["req_json"]) if r["req_json"] else {},
                    })
        except Exception as e:
            log.warning("Failed retrieving uncompleted items: %s", e)
        return results

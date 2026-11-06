"""
Simplified progress service - fixed stages + fixed weights
Based on the "keep it dumb and stable" approach you proposed
"""

import time
import json
import logging
from typing import List, Tuple, Optional, Dict, Any
import sqlite3
import threading
import os
try:
    import redis  # optional dependency
except Exception:
    redis = None

logger = logging.getLogger(__name__)

# Fixed stage definitions - adjust according to your actual project
STAGES: List[Tuple[str, int]] = [
    ("INGEST", 10),        # Download/ready
    ("SUBTITLE", 15),      # Subtitles/alignment
    ("ANALYZE", 20),       # Semantic analysis/outline
    ("HIGHLIGHT", 25),     # Clip localization/scoring
    ("EXPORT", 20),        # Export/packaging
    ("DONE", 10),          # Validation/archiving
]

# Stage weight mapping
WEIGHTS = {name: w for name, w in STAGES}
# Stage order
ORDER = [name for name, _ in STAGES]

class ProgressStore:
    def save(self, project_id: str, stage: str, percent: int, message: str, ts: int):
        raise NotImplementedError

    def get(self, project_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def delete(self, project_id: str):
        raise NotImplementedError

    def get_many(self, project_ids: List[str]) -> List[Dict[str, Any]]:
        return [s for pid in project_ids if (s := self.get(pid))]


class RedisProgressStore(ProgressStore):
    def __init__(self, redis_client):
        self.r = redis_client

    def save(self, project_id: str, stage: str, percent: int, message: str, ts: int):
        self.r.hset(f"progress:project:{project_id}", mapping={
            "stage": stage,
            "percent": str(percent),
            "message": message,
            "ts": str(ts)
        })
        payload = {"project_id": project_id, "stage": stage, "percent": percent, "message": message, "ts": ts}
        try:
            self.r.publish(f"progress:project:{project_id}", json.dumps(payload))
        except Exception:
            pass

    def get(self, project_id: str) -> Optional[Dict[str, Any]]:
        h = self.r.hgetall(f"progress:project:{project_id}")
        if not h:
            return None
        return {
            "project_id": project_id,
            "stage": h.get("stage", ""),
            "percent": int(h.get("percent", 0)),
            "message": h.get("message", ""),
            "ts": int(h.get("ts", 0))
        }

    def delete(self, project_id: str):
        self.r.delete(f"progress:project:{project_id}")


class SqliteProgressStore(ProgressStore):
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_snapshots (
                project_id TEXT PRIMARY KEY,
                stage TEXT,
                percent INTEGER,
                message TEXT,
                ts INTEGER
            )
            """
        )
        self._conn.commit()

    def save(self, project_id: str, stage: str, percent: int, message: str, ts: int):
        with self._lock:
            self._conn.execute(
                "REPLACE INTO progress_snapshots (project_id, stage, percent, message, ts) VALUES (?, ?, ?, ?, ?)",
                (project_id, stage, int(percent), message, int(ts))
            )
            self._conn.commit()

    def get(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT stage, percent, message, ts FROM progress_snapshots WHERE project_id = ?",
                (project_id,)
            )
            row = cur.fetchone()
        if not row:
            return None
        stage, percent, message, ts = row
        return {
            "project_id": project_id,
            "stage": stage or "",
            "percent": int(percent or 0),
            "message": message or "",
            "ts": int(ts or 0)
        }

    def delete(self, project_id: str):
        with self._lock:
            self._conn.execute("DELETE FROM progress_snapshots WHERE project_id = ?", (project_id,))
            self._conn.commit()

    def get_many(self, project_ids: List[str]) -> List[Dict[str, Any]]:
        if not project_ids:
            return []
        placeholders = ",".join(["?"] * len(project_ids))
        with self._lock:
            cur = self._conn.execute(
                f"SELECT project_id, stage, percent, message, ts FROM progress_snapshots WHERE project_id IN ({placeholders})",
                project_ids
            )
            rows = cur.fetchall()
        results = []
        for pid, stage, percent, message, ts in rows:
            results.append({
                "project_id": pid,
                "stage": stage or "",
                "percent": int(percent or 0),
                "message": message or "",
                "ts": int(ts or 0)
            })
        return results


# Store selection: Desktop mode forces SQLite; Server mode prefers Redis and automatically falls back to SQLite on failure
store: ProgressStore
try:
    from backend.core.desktop_config import is_desktop_mode, get_desktop_paths
    if is_desktop_mode():
        paths = get_desktop_paths()
        db_file = os.path.join(str(paths.data_dir), "progress.db")
        store = SqliteProgressStore(db_file)
        logger.info(f"Desktop mode using SQLite progress store: {db_file}")
    else:
        if redis is None:
            raise RuntimeError("redis is not installed")
        # Get the Redis URL from the environment variable, defaulting to the local address
        redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
        r_client = redis.Redis.from_url(redis_url, decode_responses=True)
        r_client.ping()
        store = RedisProgressStore(r_client)
        logger.info("Server mode using Redis progress store")
except Exception as e:
    # Automatically fall back to SQLite
    try:
        # Prefer the desktop data directory; if unavailable, fall back to the project root data directory
        db_file = None
        try:
            from backend.core.desktop_config import get_desktop_paths
            db_file = os.path.join(str(get_desktop_paths().data_dir), "progress.db")
        except Exception:
            from pathlib import Path
            db_file = str((Path(__file__).parent.parent.parent / 'data' / 'progress.db').resolve())
        store = SqliteProgressStore(db_file)
        logger.warning(f"Redis unavailable or not installed, automatically falling back to SQLite progress store: {db_file}, reason: {e}")
    except Exception as e2:
        logger.error(f"Failed to initialize progress store: {e2}")
        store = None


def compute_percent(stage: str, subpercent: Optional[float] = None) -> int:
    """
    Compute the percentage corresponding to a stage
    
    Args:
        stage: Current stage name
        subpercent: Sub-progress percentage (0-100), optional
        
    Returns:
        Total progress percentage (0-100)
    """
    # Accumulate the weights of previous stages
    done = 0
    for s in ORDER:
        if s == stage:
            break
        done += WEIGHTS[s]
    
    # Current stage
    cur = WEIGHTS.get(stage, 0)
    
    if subpercent is None:
        # When switching stages, show up to the start of the current stage
        return min(100, done + cur) if stage == "DONE" else min(99, done)
    else:
        # With sub-progress, convert linearly by weight
        subpercent = max(0, min(100, subpercent))
        return min(99, done + int(cur * subpercent / 100))


# In-process listeners (CLI / MCP use this to print progress in real time; not needed by the API process)
_listeners: List[Any] = []


def add_progress_listener(fn) -> None:
    """Register an in-process progress callback: fn(payload: dict). payload contains project_id / stage / percent / message / ts."""
    if fn not in _listeners:
        _listeners.append(fn)


def remove_progress_listener(fn) -> None:
    try:
        _listeners.remove(fn)
    except ValueError:
        pass


def emit_progress(project_id: str, stage: str, message: str = "", subpercent: Optional[float] = None):
    """
    Emit a progress event
    
    Args:
        project_id: Project ID
        stage: Current stage
        message: Progress message
        subpercent: Sub-progress percentage, optional
    """
    percent = compute_percent(stage, subpercent)
    payload = {
        "project_id": project_id,
        "stage": stage,
        "percent": percent,
        "message": message,
        "ts": int(time.time())
    }

    for fn in list(_listeners):
        try:
            fn(payload)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Progress listener exception: {e}")

    if not store:
        logger.warning("Progress store not initialized, skipping progress emit")
        return
    
    try:
        store.save(project_id, stage, percent, message, payload["ts"])
        logger.info(f"Progress event emitted: {project_id} - {stage} ({percent}%) - {message}")
    except Exception as e:
        logger.error(f"Failed to emit progress event: {e}")


def get_progress_snapshot(project_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a project progress snapshot
    
    Args:
        project_id: Project ID
        
    Returns:
        Progress snapshot data, or None if it does not exist
    """
    if not store:
        return None
        
    try:
        return store.get(project_id)
    except Exception as e:
        logger.error(f"Failed to get progress snapshot: {e}")
        return None


def get_multiple_progress_snapshots(project_ids: List[str]) -> List[Dict[str, Any]]:
    """
    Batch get progress snapshots for multiple projects
    
    Args:
        project_ids: List of project IDs
        
    Returns:
        List of progress snapshots
    """
    if not store:
        return []
    try:
        return store.get_many(project_ids)
    except Exception as e:
        logger.error(f"Failed to batch get progress snapshots: {e}")
        return []


def clear_progress(project_id: str):
    """
    Clear project progress data
    
    Args:
        project_id: Project ID
    """
    if not store:
        return
    try:
        store.delete(project_id)
        logger.info(f"Cleared project progress data: {project_id}")
    except Exception as e:
        logger.error(f"Failed to clear progress data: {e}")


# Stage name mapping (for display)
STAGE_NAMES = {
    "INGEST": "Material preparation",
    "SUBTITLE": "Subtitle processing",
    "ANALYZE": "Content analysis",
    "HIGHLIGHT": "Clip localization",
    "EXPORT": "Video export",
    "DONE": "Processing complete"
}

def get_stage_display_name(stage: str) -> str:
    """Get the display name of a stage"""
    return STAGE_NAMES.get(stage, stage)

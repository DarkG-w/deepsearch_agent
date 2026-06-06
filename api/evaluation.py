import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).parents[1].resolve()
DEFAULT_EVAL_DB = PROJECT_ROOT / "data" / "evaluation.sqlite3"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def make_cache_key(namespace: str, payload: Dict[str, Any]) -> str:
    raw = f"{namespace}:{stable_json(payload)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SearchQualityEvaluator:
    """Small deterministic evaluator for search-result usefulness.

    This is intentionally heuristic: it gives the system measurable signals
    without adding another LLM call to every search.
    """

    @staticmethod
    def _terms(text: str) -> set[str]:
        cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
        return {part for part in cleaned.split() if len(part) >= 2}

    def evaluate(self, query: str, response: Any) -> Dict[str, Any]:
        results = []
        if isinstance(response, dict):
            raw_results = response.get("results", [])
            if isinstance(raw_results, list):
                results = [row for row in raw_results if isinstance(row, dict)]

        query_terms = self._terms(query)
        if not results:
            return {
                "score": 0.0,
                "result_count": 0,
                "metrics": {
                    "coverage": 0.0,
                    "avg_provider_score": 0.0,
                    "source_diversity": 0.0,
                    "has_content_ratio": 0.0,
                },
            }

        provider_scores = []
        matched_terms = set()
        domains = set()
        content_hits = 0

        for row in results:
            title = str(row.get("title", ""))
            content = str(row.get("content", "") or row.get("raw_content", ""))
            url = str(row.get("url", ""))
            text = f"{title} {content} {url}"
            matched_terms.update(query_terms & self._terms(text))
            if row.get("score") is not None:
                try:
                    provider_scores.append(float(row["score"]))
                except (TypeError, ValueError):
                    pass
            if content.strip():
                content_hits += 1
            if url:
                domain = url.split("/")[2].lower() if "://" in url and len(url.split("/")) > 2 else url
                domains.add(domain)

        coverage = len(matched_terms) / max(len(query_terms), 1)
        avg_provider_score = sum(provider_scores) / len(provider_scores) if provider_scores else 0.0
        source_diversity = min(len(domains) / max(len(results), 1), 1.0)
        has_content_ratio = content_hits / max(len(results), 1)

        score = (
            coverage * 0.4
            + avg_provider_score * 0.25
            + source_diversity * 0.2
            + has_content_ratio * 0.15
        )
        return {
            "score": round(max(0.0, min(score, 1.0)), 4),
            "result_count": len(results),
            "metrics": {
                "coverage": round(coverage, 4),
                "avg_provider_score": round(avg_provider_score, 4),
                "source_diversity": round(source_diversity, 4),
                "has_content_ratio": round(has_content_ratio, 4),
            },
        }


class EvaluationStore:
    def __init__(self, db_path: Path = DEFAULT_EVAL_DB):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._available = True
        try:
            self._ensure_database()
        except Exception as exc:
            self._available = False
            print(f"[Evaluation] Disabled: {type(exc).__name__}: {exc}")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_database(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_traces (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    thread_id TEXT,
                    query TEXT,
                    status TEXT NOT NULL,
                    retry_of TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    duration_ms INTEGER,
                    error TEXT,
                    final_result_preview TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_task_traces_started
                    ON task_traces(started_at DESC);

                CREATE TABLE IF NOT EXISTS trace_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    thread_id TEXT,
                    event_type TEXT NOT NULL,
                    message TEXT,
                    payload_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trace_events_task
                    ON trace_events(task_id, created_at);

                CREATE TABLE IF NOT EXISTS search_evaluations (
                    search_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    thread_id TEXT,
                    query TEXT NOT NULL,
                    topic TEXT,
                    provider TEXT NOT NULL,
                    cached INTEGER NOT NULL DEFAULT 0,
                    quality_score REAL NOT NULL,
                    result_count INTEGER NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_search_evaluations_created
                    ON search_evaluations(created_at DESC);

                CREATE TABLE IF NOT EXISTS tool_cache (
                    cache_key TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_tool_cache_namespace
                    ON tool_cache(namespace);
                """
            )

    def _guard(self) -> bool:
        return self._available

    def start_task(self, task_id: str, session_id: str, thread_id: str, query: str, retry_of: Optional[str] = None):
        if not self._guard():
            return
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO task_traces
                (task_id, session_id, thread_id, query, status, retry_of, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, session_id, thread_id, query, "running", retry_of, now_iso()),
            )

    def finish_task(self, task_id: str, status: str, error: Optional[str] = None, final_result: str = ""):
        if not self._guard():
            return
        finished = now_iso()
        with self._lock, self._connection() as conn:
            row = conn.execute("SELECT started_at FROM task_traces WHERE task_id = ?", (task_id,)).fetchone()
            duration_ms = None
            if row and row["started_at"]:
                try:
                    started_dt = datetime.fromisoformat(row["started_at"])
                    duration_ms = int((datetime.fromisoformat(finished) - started_dt).total_seconds() * 1000)
                except Exception:
                    duration_ms = None
            conn.execute(
                """
                UPDATE task_traces
                SET status = ?, finished_at = ?, duration_ms = ?, error = ?, final_result_preview = ?
                WHERE task_id = ?
                """,
                (status, finished, duration_ms, error, (final_result or "")[:1000], task_id),
            )

    def record_event(
        self,
        event_type: str,
        message: str,
        payload: Dict[str, Any],
        task_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ):
        if not self._guard():
            return
        event_id = hashlib.sha256(f"{time.time_ns()}:{task_id}:{event_type}".encode("utf-8")).hexdigest()
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO trace_events
                (event_id, task_id, thread_id, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, task_id, thread_id, event_type, message, stable_json(payload), now_iso()),
            )

    def record_search_evaluation(
        self,
        query: str,
        topic: str,
        provider: str,
        response: Any,
        cached: bool,
        task_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        evaluator = SearchQualityEvaluator()
        evaluation = evaluator.evaluate(query, response)
        if not self._guard():
            return evaluation
        search_id = hashlib.sha256(f"{time.time_ns()}:{query}:{topic}".encode("utf-8")).hexdigest()
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO search_evaluations
                (search_id, task_id, thread_id, query, topic, provider, cached,
                 quality_score, result_count, metrics_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    search_id,
                    task_id,
                    thread_id,
                    query,
                    topic,
                    provider,
                    1 if cached else 0,
                    evaluation["score"],
                    evaluation["result_count"],
                    stable_json(evaluation["metrics"]),
                    now_iso(),
                ),
            )
        return evaluation

    def cache_get(self, namespace: str, request_payload: Dict[str, Any]) -> Optional[Any]:
        if not self._guard():
            return None
        key = make_cache_key(namespace, request_payload)
        current_ts = time.time()
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                SELECT response_json, expires_at FROM tool_cache
                WHERE cache_key = ? AND namespace = ?
                """,
                (key, namespace),
            ).fetchone()
            if not row or float(row["expires_at"]) < current_ts:
                if row:
                    conn.execute("DELETE FROM tool_cache WHERE cache_key = ?", (key,))
                return None
            conn.execute("UPDATE tool_cache SET hit_count = hit_count + 1 WHERE cache_key = ?", (key,))
            return json.loads(row["response_json"])

    def cache_set(self, namespace: str, request_payload: Dict[str, Any], response: Any, ttl_seconds: int):
        if not self._guard():
            return
        key = make_cache_key(namespace, request_payload)
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO tool_cache
                (cache_key, namespace, request_json, response_json, created_at, expires_at, hit_count)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(cache_key) DO UPDATE SET
                    response_json = excluded.response_json,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (
                    key,
                    namespace,
                    stable_json(request_payload),
                    stable_json(response),
                    now_iso(),
                    time.time() + ttl_seconds,
                ),
            )

    def list_traces(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self._guard():
            return []
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM task_traces
                ORDER BY COALESCE(started_at, finished_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_trace(self, task_id: str) -> Optional[Dict[str, Any]]:
        if not self._guard():
            return None
        with self._lock, self._connection() as conn:
            trace = conn.execute("SELECT * FROM task_traces WHERE task_id = ?", (task_id,)).fetchone()
            if not trace:
                return None
            events = conn.execute(
                """
                SELECT * FROM trace_events
                WHERE task_id = ?
                ORDER BY created_at ASC
                """,
                (task_id,),
            ).fetchall()
            searches = conn.execute(
                """
                SELECT * FROM search_evaluations
                WHERE task_id = ?
                ORDER BY created_at ASC
                """,
                (task_id,),
            ).fetchall()
        payload = dict(trace)
        payload["events"] = [dict(row) for row in events]
        payload["search_evaluations"] = [dict(row) for row in searches]
        return payload

    def list_search_evaluations(self, limit: int = 100) -> List[Dict[str, Any]]:
        if not self._guard():
            return []
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM search_evaluations
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> Dict[str, Any]:
        if not self._guard():
            return {"enabled": False}
        with self._lock, self._connection() as conn:
            task_row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                       AVG(duration_ms) AS avg_duration_ms
                FROM task_traces
                """
            ).fetchone()
            search_row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       AVG(quality_score) AS avg_quality_score,
                       SUM(CASE WHEN cached = 1 THEN 1 ELSE 0 END) AS cache_hits
                FROM search_evaluations
                """
            ).fetchone()
            cache_row = conn.execute(
                "SELECT COUNT(*) AS entries, SUM(hit_count) AS hit_count FROM tool_cache"
            ).fetchone()
        search_total = int(search_row["total"] or 0)
        cache_hits = int(search_row["cache_hits"] or 0)
        return {
            "enabled": True,
            "tasks": {
                "total": int(task_row["total"] or 0),
                "completed": int(task_row["completed"] or 0),
                "avg_duration_ms": int(task_row["avg_duration_ms"] or 0),
            },
            "search": {
                "total": search_total,
                "avg_quality_score": round(float(search_row["avg_quality_score"] or 0.0), 4),
                "cache_hit_rate": round(cache_hits / search_total, 4) if search_total else 0.0,
            },
            "cache": {
                "entries": int(cache_row["entries"] or 0),
                "hit_count": int(cache_row["hit_count"] or 0),
            },
        }


evaluation_store = EvaluationStore()


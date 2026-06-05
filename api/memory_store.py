import asyncio
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import OpenAIEmbeddings


class EmbeddingService:
    def __init__(self):
        self.dimension = int(os.getenv("MEMORY_EMBEDDING_DIMENSION", "1024"))
        self.model = os.getenv("MEMORY_EMBEDDING_MODEL", "embedding-3")
        self.enabled = os.getenv("MEMORY_EMBEDDING_ENABLED", "1") != "0"
        self._embeddings = None

    def _client(self):
        if not self.enabled:
            return None
        if self._embeddings is None:
            self._embeddings = OpenAIEmbeddings(
                model=self.model,
                dimensions=self.dimension,
                tiktoken_enabled=False,
            )
        return self._embeddings

    async def embed_query(self, text: str) -> Optional[List[float]]:
        client = self._client()
        if client is None:
            return None
        try:
            return await asyncio.to_thread(client.embed_query, text)
        except Exception as exc:
            print(f"[Memory] Embedding failed: {type(exc).__name__}: {exc}")
            return None


class MilvusMemoryIndex:
    def __init__(self, embedding_dimension: int):
        self.enabled = os.getenv("MEMORY_MILVUS_ENABLED", "1") != "0"
        self.collection_name = os.getenv("MEMORY_MILVUS_COLLECTION", "deep_search_memories")
        self.uri = os.getenv("MILVUS_URI", "http://127.0.0.1:19530")
        self.token = os.getenv("MILVUS_TOKEN") or None
        self.embedding_dimension = embedding_dimension
        self._client = None
        self._ready = False

    def _connect(self):
        if not self.enabled:
            return None
        if self._client is not None:
            return self._client
        try:
            from pymilvus import MilvusClient
        except ImportError:
            print("[Memory] pymilvus is not installed; Milvus memory search is disabled.")
            self.enabled = False
            return None

        try:
            kwargs = {"uri": self.uri}
            if self.token:
                kwargs["token"] = self.token
            self._client = MilvusClient(**kwargs)
            self._ensure_collection()
            return self._client
        except Exception as exc:
            print(f"[Memory] Milvus connection failed: {type(exc).__name__}: {exc}")
            self.enabled = False
            return None

    def _ensure_collection(self):
        if self._ready or self._client is None:
            return
        if not self._client.has_collection(self.collection_name):
            self._client.create_collection(
                collection_name=self.collection_name,
                dimension=self.embedding_dimension,
                metric_type="COSINE",
                auto_id=False,
            )
        self._ready = True

    async def upsert_memory(self, memory: Dict[str, Any], embedding: Optional[List[float]]):
        if not embedding:
            return
        client = await asyncio.to_thread(self._connect)
        if client is None:
            return
        data = {
            "id": memory["memory_id"],
            "vector": embedding,
            "memory_id": memory["memory_id"],
            "session_id": memory.get("source_session_id", ""),
            "user_id": memory.get("user_id", "default"),
            "content": memory.get("content", ""),
        }
        try:
            await asyncio.to_thread(client.upsert, self.collection_name, [data])
        except Exception as exc:
            print(f"[Memory] Milvus upsert failed: {type(exc).__name__}: {exc}")

    async def search(self, embedding: Optional[List[float]], user_id: str, top_k: int) -> List[str]:
        if not embedding:
            return []
        client = await asyncio.to_thread(self._connect)
        if client is None:
            return []
        try:
            rows = await asyncio.to_thread(
                client.search,
                collection_name=self.collection_name,
                data=[embedding],
                limit=top_k,
                filter=f'user_id == "{user_id}"',
                output_fields=["memory_id"],
            )
        except Exception as exc:
            print(f"[Memory] Milvus search failed: {type(exc).__name__}: {exc}")
            return []

        memory_ids = []
        for hit in rows[0] if rows else []:
            entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
            memory_id = entity.get("memory_id") or hit.get("id")
            if memory_id:
                memory_ids.append(str(memory_id))
        return memory_ids

    async def delete_by_session(self, session_id: str):
        client = await asyncio.to_thread(self._connect)
        if client is None:
            return
        try:
            await asyncio.to_thread(
                client.delete,
                collection_name=self.collection_name,
                filter=f'session_id == "{session_id}"',
            )
        except Exception as exc:
            print(f"[Memory] Milvus delete failed: {type(exc).__name__}: {exc}")

    async def delete_memory(self, memory_id: str):
        client = await asyncio.to_thread(self._connect)
        if client is None:
            return
        try:
            await asyncio.to_thread(
                client.delete,
                collection_name=self.collection_name,
                ids=[memory_id],
            )
        except Exception as exc:
            print(f"[Memory] Milvus memory delete failed: {type(exc).__name__}: {exc}")


class AdvancedMemoryStore:
    def __init__(self, db_path: str, legacy_json_path: Optional[str] = None):
        self.db_path = Path(db_path)
        self.legacy_json_path = Path(legacy_json_path) if legacy_json_path else None
        self._lock = asyncio.Lock()
        self.embedding_service = EmbeddingService()
        self.vector_index = MilvusMemoryIndex(self.embedding_service.dimension)
        self._ensure_database()
        self._migrate_legacy_json()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _make_title_from_query(query: str) -> str:
        compact = " ".join((query or "").strip().split())
        return compact[:40] if compact else "New Chat"

    @staticmethod
    def _message_preview(content: str) -> str:
        return " ".join((content or "").strip().split())[:120]

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
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_preview TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
                    ON chat_messages(session_id, created_at);
                CREATE TABLE IF NOT EXISTS session_summaries (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS long_term_memories (
                    memory_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    scope TEXT NOT NULL DEFAULT 'global',
                    type TEXT NOT NULL DEFAULT 'fact',
                    content TEXT NOT NULL,
                    importance REAL NOT NULL DEFAULT 0.5,
                    confidence REAL NOT NULL DEFAULT 0.7,
                    source_session_id TEXT,
                    source_message_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memories_user_updated
                    ON long_term_memories(user_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_memories_source_session
                    ON long_term_memories(source_session_id);
                """
            )

    def _migrate_legacy_json(self):
        if not self.legacy_json_path or not self.legacy_json_path.exists():
            return
        with self._connection() as conn:
            existing = conn.execute("SELECT COUNT(*) AS count FROM chat_sessions").fetchone()["count"]
            if existing:
                return
            try:
                data = json.loads(self.legacy_json_path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[Memory] Legacy JSON migration skipped: {type(exc).__name__}: {exc}")
                return
            for session in data.get("sessions", {}).values():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO chat_sessions
                    (session_id, thread_id, title, created_at, updated_at, last_message_preview)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["session_id"],
                        session["thread_id"],
                        session.get("title", "New Chat"),
                        session.get("created_at") or self._now_iso(),
                        session.get("updated_at") or self._now_iso(),
                        session.get("last_message_preview", ""),
                    ),
                )
                for msg in session.get("messages", []):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO chat_messages
                        (message_id, session_id, role, content, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            session["session_id"],
                            msg.get("role", "user"),
                            msg.get("content", ""),
                            msg.get("timestamp") or self._now_iso(),
                        ),
                    )

    async def list_sessions(self) -> List[Dict[str, Any]]:
        async with self._lock:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT s.*, COUNT(m.message_id) AS message_count
                    FROM chat_sessions s
                    LEFT JOIN chat_messages m ON m.session_id = s.session_id
                    GROUP BY s.session_id
                    ORDER BY s.updated_at DESC
                    """
                ).fetchall()
        return [dict(row) for row in rows]

    async def create_session(self, title: Optional[str] = None, thread_id: Optional[str] = None) -> Dict[str, Any]:
        now = self._now_iso()
        session = {
            "session_id": str(uuid.uuid4()),
            "thread_id": thread_id or str(uuid.uuid4()),
            "title": (title or "").strip() or "New Chat",
            "created_at": now,
            "updated_at": now,
            "last_message_preview": "",
        }
        async with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO chat_sessions
                    (session_id, thread_id, title, created_at, updated_at, last_message_preview)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["session_id"],
                        session["thread_id"],
                        session["title"],
                        session["created_at"],
                        session["updated_at"],
                        session["last_message_preview"],
                    ),
                )
        return session

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            with self._connection() as conn:
                session = conn.execute(
                    "SELECT * FROM chat_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if not session:
                    return None
                messages = conn.execute(
                    """
                    SELECT role, content, created_at AS timestamp
                    FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY created_at ASC
                    """,
                    (session_id,),
                ).fetchall()
        payload = dict(session)
        payload["messages"] = [dict(row) for row in messages]
        payload["message_count"] = len(messages)
        return payload

    async def delete_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = await self.get_session(session_id)
        if not session:
            return None
        async with self._lock:
            with self._connection() as conn:
                conn.execute("DELETE FROM long_term_memories WHERE source_session_id = ?", (session_id,))
                conn.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
        await self.vector_index.delete_by_session(session_id)
        return session

    async def resolve_session(self, session_id: Optional[str], thread_id: Optional[str]) -> Dict[str, Any]:
        async with self._lock:
            with self._connection() as conn:
                if session_id:
                    row = conn.execute(
                        "SELECT * FROM chat_sessions WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()
                    if not row:
                        raise KeyError(f"Session not found: {session_id}")
                    if thread_id and row["thread_id"] != thread_id:
                        now = self._now_iso()
                        conn.execute(
                            "UPDATE chat_sessions SET thread_id = ?, updated_at = ? WHERE session_id = ?",
                            (thread_id, now, session_id),
                        )
                        row = conn.execute(
                            "SELECT * FROM chat_sessions WHERE session_id = ?",
                            (session_id,),
                        ).fetchone()
                    return dict(row)

                if thread_id:
                    row = conn.execute(
                        "SELECT * FROM chat_sessions WHERE thread_id = ?",
                        (thread_id,),
                    ).fetchone()
                    if row:
                        return dict(row)

        return await self.create_session(thread_id=thread_id)

    async def append_message(self, session_id: str, role: str, content: str) -> str:
        now = self._now_iso()
        message_id = str(uuid.uuid4())
        async with self._lock:
            with self._connection() as conn:
                session = conn.execute(
                    "SELECT title FROM chat_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if not session:
                    raise KeyError(session_id)
                title = session["title"]
                if role == "user" and title == "New Chat":
                    title = self._make_title_from_query(content)
                conn.execute(
                    """
                    INSERT INTO chat_messages
                    (message_id, session_id, role, content, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (message_id, session_id, role, content, now),
                )
                conn.execute(
                    """
                    UPDATE chat_sessions
                    SET title = ?, updated_at = ?, last_message_preview = ?
                    WHERE session_id = ?
                    """,
                    (title, now, self._message_preview(content), session_id),
                )
        return message_id

    async def get_recent_messages(self, session_id: str, limit: int = 8) -> List[Dict[str, Any]]:
        async with self._lock:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT role, content, created_at AS timestamp
                    FROM chat_messages
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
        return [dict(row) for row in reversed(rows)]

    async def get_session_summary(self, session_id: str) -> str:
        async with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT summary FROM session_summaries WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
        return row["summary"] if row else ""

    async def search_relevant_memories(self, query: str, user_id: str = "default", top_k: int = 6) -> List[Dict[str, Any]]:
        embedding = await self.embedding_service.embed_query(query)
        memory_ids = await self.vector_index.search(embedding, user_id=user_id, top_k=top_k)

        async with self._lock:
            with self._connection() as conn:
                if memory_ids:
                    placeholders = ",".join("?" for _ in memory_ids)
                    rows = conn.execute(
                        f"SELECT * FROM long_term_memories WHERE memory_id IN ({placeholders})",
                        memory_ids,
                    ).fetchall()
                    by_id = {row["memory_id"]: dict(row) for row in rows}
                    ordered = [by_id[mid] for mid in memory_ids if mid in by_id]
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM long_term_memories
                        WHERE user_id = ?
                        ORDER BY importance DESC, updated_at DESC
                        LIMIT ?
                        """,
                        (user_id, top_k),
                    ).fetchall()
                    ordered = [dict(row) for row in rows]

                if ordered:
                    now = self._now_iso()
                    conn.executemany(
                        "UPDATE long_term_memories SET last_used_at = ? WHERE memory_id = ?",
                        [(now, row["memory_id"]) for row in ordered],
                    )
        return ordered

    async def list_memories(self, user_id: str = "default", limit: int = 100) -> List[Dict[str, Any]]:
        async with self._lock:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM long_term_memories
                    WHERE user_id = ?
                    ORDER BY importance DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    async def delete_memory(self, memory_id: str) -> bool:
        async with self._lock:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT memory_id FROM long_term_memories WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()
                if not row:
                    return False
                conn.execute("DELETE FROM long_term_memories WHERE memory_id = ?", (memory_id,))
        await self.vector_index.delete_memory(memory_id)
        return True

    async def build_memory_context(
        self,
        session_id: str,
        user_query: str,
        max_recent_messages: int = 8,
        max_relevant_memories: int = 6,
    ) -> str:
        recent, summary, memories = await asyncio.gather(
            self.get_recent_messages(session_id, max_recent_messages),
            self.get_session_summary(session_id),
            self.search_relevant_memories(user_query, top_k=max_relevant_memories),
        )

        sections = []
        if summary:
            sections.append("[会话摘要]\n" + summary)
        if memories:
            lines = [
                f"- ({row.get('type', 'fact')}, importance={row.get('importance', 0.5):.2f}) {row.get('content', '')}"
                for row in memories
                if row.get("content")
            ]
            if lines:
                sections.append("[相关长期记忆]\n" + "\n".join(lines))
        if recent:
            lines = []
            for row in recent:
                prefix = "用户" if row.get("role") == "user" else "助手"
                content = str(row.get("content", "")).strip()
                if content:
                    lines.append(f"{prefix}: {content}")
            if lines:
                sections.append("[最近对话]\n" + "\n".join(lines))
        return "\n\n".join(sections)

    async def update_memory_after_turn(self, session_id: str, user_query: str, assistant_answer: str, llm):
        messages = await self.get_recent_messages(session_id, limit=16)
        if not messages:
            return
        await self._update_session_summary(session_id, messages, llm)
        memories = await self._extract_memories(session_id, user_query, assistant_answer, llm)
        for memory in memories:
            await self._store_memory(memory)

    async def _update_session_summary(self, session_id: str, messages: List[Dict[str, Any]], llm):
        existing = await self.get_session_summary(session_id)
        transcript = "\n".join(
            f"{'用户' if row.get('role') == 'user' else '助手'}: {row.get('content', '')}"
            for row in messages
        )
        prompt = (
            "请把已有摘要和最近对话压缩成一段持续会话摘要。"
            "保留用户目标、项目背景、已经做过的决定、未完成事项。"
            "不要超过 300 字。\n\n"
            f"已有摘要:\n{existing or '无'}\n\n最近对话:\n{transcript}"
        )
        try:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            summary = str(getattr(response, "content", response)).strip()
        except Exception as exc:
            print(f"[Memory] Summary update failed: {type(exc).__name__}: {exc}")
            return
        if not summary:
            return
        now = self._now_iso()
        async with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    INSERT INTO session_summaries (session_id, summary, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        summary = excluded.summary,
                        updated_at = excluded.updated_at
                    """,
                    (session_id, summary[:2000], now),
                )

    async def _extract_memories(self, session_id: str, user_query: str, assistant_answer: str, llm) -> List[Dict[str, Any]]:
        prompt = (
            "从下面这一轮对话中抽取值得长期保存的记忆。"
            "只保存用户偏好、长期目标、项目背景、稳定事实、明确要求、未完成任务。"
            "不要保存一次性寒暄、临时错误、敏感密钥、完整长答案。"
            "返回 JSON 数组，最多 5 条。每条包含 type, content, importance, confidence。"
            "type 只能是 preference/goal/project/fact/task。没有可保存内容就返回 []。\n\n"
            f"用户: {user_query}\n\n助手: {assistant_answer[:4000]}"
        )
        try:
            response = await llm.ainvoke(
                [
                    SystemMessage(content="你是严谨的长期记忆抽取器，只输出 JSON。"),
                    HumanMessage(content=prompt),
                ]
            )
            raw = str(getattr(response, "content", response)).strip()
            start = raw.find("[")
            end = raw.rfind("]")
            if start >= 0 and end >= start:
                raw = raw[start : end + 1]
            parsed = json.loads(raw)
        except Exception as exc:
            print(f"[Memory] Memory extraction failed: {type(exc).__name__}: {exc}")
            return []

        if not isinstance(parsed, list):
            return []
        now = self._now_iso()
        memories = []
        for item in parsed[:5]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            memories.append(
                {
                    "memory_id": str(uuid.uuid4()),
                    "user_id": "default",
                    "scope": "global",
                    "type": str(item.get("type", "fact"))[:32],
                    "content": content[:1000],
                    "importance": float(item.get("importance", 0.5)),
                    "confidence": float(item.get("confidence", 0.7)),
                    "source_session_id": session_id,
                    "source_message_id": None,
                    "created_at": now,
                    "updated_at": now,
                    "last_used_at": None,
                }
            )
        return memories

    async def _store_memory(self, memory: Dict[str, Any]):
        async with self._lock:
            with self._connection() as conn:
                existing = conn.execute(
                    """
                    SELECT memory_id FROM long_term_memories
                    WHERE user_id = ? AND content = ?
                    """,
                    (memory["user_id"], memory["content"]),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE long_term_memories
                        SET importance = MAX(importance, ?),
                            confidence = MAX(confidence, ?),
                            updated_at = ?
                        WHERE memory_id = ?
                        """,
                        (
                            memory["importance"],
                            memory["confidence"],
                            memory["updated_at"],
                            existing["memory_id"],
                        ),
                    )
                    memory["memory_id"] = existing["memory_id"]
                else:
                    conn.execute(
                        """
                        INSERT INTO long_term_memories
                        (memory_id, user_id, scope, type, content, importance, confidence,
                         source_session_id, source_message_id, created_at, updated_at, last_used_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            memory["memory_id"],
                            memory["user_id"],
                            memory["scope"],
                            memory["type"],
                            memory["content"],
                            memory["importance"],
                            memory["confidence"],
                            memory["source_session_id"],
                            memory["source_message_id"],
                            memory["created_at"],
                            memory["updated_at"],
                            memory["last_used_at"],
                        ),
                    )
        embedding = await self.embedding_service.embed_query(memory["content"])
        await self.vector_index.upsert_memory(memory, embedding)

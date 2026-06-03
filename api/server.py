import asyncio
import os
import json
import shutil
import socket
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Any, Dict, List, Optional

# Force UTF-8 stdio on Windows to avoid gbk encoding crashes in runtime logs.
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    # Never block service startup due to logging configuration.
    pass

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

# Add project root to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

from agent.main_agent import run_deep_agent
from api.monitor import monitor

BUILD_TAG = "fix-gbk-2026-05-31-2"
SESSION_CLEANUP_DAYS = int(os.getenv("SESSION_CLEANUP_DAYS", "2"))
SESSION_CLEANUP_INTERVAL_SECONDS = int(os.getenv("SESSION_CLEANUP_INTERVAL_SECONDS", "3600"))

# Mount output dir for static access
output_dir = os.path.join(project_root, "output")
os.makedirs(output_dir, exist_ok=True)

# Upload dir
updated_dir = os.path.join(project_root, "updated")
os.makedirs(updated_dir, exist_ok=True)

# Persistent chat sessions store
data_dir = os.path.join(project_root, "data")
os.makedirs(data_dir, exist_ok=True)
sessions_store_file = os.path.join(data_dir, "chat_sessions.json")


def cleanup_empty_session_dirs() -> int:
    """
    Delete empty `session_*` directories in output dir older than configured days.
    Returns the number of deleted directories.
    """
    deleted_count = 0
    now_ts = datetime.now(timezone.utc).timestamp()
    threshold_seconds = SESSION_CLEANUP_DAYS * 24 * 60 * 60
    output_path = Path(output_dir).resolve()

    for child in output_path.iterdir():
        if not child.is_dir():
            continue
        if not child.name.startswith("session_"):
            continue

        try:
            resolved = child.resolve()
        except OSError:
            continue

        # Safety check: ensure target stays inside output root.
        if output_path not in resolved.parents and resolved != output_path:
            continue

        try:
            if any(resolved.iterdir()):
                continue
        except OSError:
            # Skip unreadable directories.
            continue

        try:
            age_seconds = now_ts - resolved.stat().st_mtime
        except OSError:
            continue

        if age_seconds < threshold_seconds:
            continue

        try:
            resolved.rmdir()
            deleted_count += 1
            print(f"[Cleanup] Deleted empty session dir: {resolved}")
        except OSError:
            # Directory may become non-empty between checks.
            continue

    return deleted_count


async def run_session_dir_cleanup_loop():
    while True:
        try:
            deleted = cleanup_empty_session_dirs()
            if deleted:
                print(
                    f"[Cleanup] Removed {deleted} empty session dirs "
                    f"(older than {SESSION_CLEANUP_DAYS} days)."
                )
        except Exception as exc:
            print(f"[Cleanup] Session cleanup failed: {type(exc).__name__}: {exc}")

        await asyncio.sleep(max(60, SESSION_CLEANUP_INTERVAL_SECONDS))


class ChatSessionStore:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self._lock = asyncio.Lock()
        self._ensure_store_file()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_store_file(self):
        if self.file_path.exists():
            return
        payload = {"version": 1, "sessions": {}, "thread_index": {}}
        self.file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_unlocked(self) -> Dict[str, Any]:
        try:
            text = self.file_path.read_text(encoding="utf-8")
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("Invalid sessions store format.")
            data.setdefault("version", 1)
            data.setdefault("sessions", {})
            data.setdefault("thread_index", {})
            return data
        except Exception:
            payload = {"version": 1, "sessions": {}, "thread_index": {}}
            self.file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return payload

    def _save_unlocked(self, data: Dict[str, Any]):
        self.file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _make_title_from_query(query: str) -> str:
        stripped = (query or "").strip()
        if not stripped:
            return "New Chat"
        compact = " ".join(stripped.split())
        return compact[:40]

    @staticmethod
    def _message_preview(content: str) -> str:
        compact = " ".join((content or "").strip().split())
        return compact[:120]

    async def list_sessions(self) -> List[Dict[str, Any]]:
        async with self._lock:
            data = self._load_unlocked()
            sessions = list(data["sessions"].values())

        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        result = []
        for row in sessions:
            result.append(
                {
                    "session_id": row["session_id"],
                    "thread_id": row["thread_id"],
                    "title": row.get("title", "New Chat"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "last_message_preview": row.get("last_message_preview", ""),
                    "message_count": len(row.get("messages", [])),
                }
            )
        return result

    async def create_session(self, title: Optional[str] = None) -> Dict[str, Any]:
        now = self._now_iso()
        session_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())
        resolved_title = (title or "").strip() or "New Chat"
        session = {
            "session_id": session_id,
            "thread_id": thread_id,
            "title": resolved_title,
            "created_at": now,
            "updated_at": now,
            "last_message_preview": "",
            "messages": [],
        }
        Path(output_dir, f"session_{thread_id}").mkdir(parents=True, exist_ok=True)
        async with self._lock:
            data = self._load_unlocked()
            data["sessions"][session_id] = session
            data["thread_index"][thread_id] = session_id
            self._save_unlocked(data)
        return session

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            data = self._load_unlocked()
            session = data["sessions"].get(session_id)
            if not session:
                return None
            return dict(session)

    async def delete_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            data = self._load_unlocked()
            session = data["sessions"].pop(session_id, None)
            if not session:
                return None
            thread_id = session.get("thread_id")
            if thread_id:
                data["thread_index"].pop(thread_id, None)
            self._save_unlocked(data)
            return dict(session)

    async def resolve_session(self, session_id: Optional[str], thread_id: Optional[str]) -> Dict[str, Any]:
        async with self._lock:
            data = self._load_unlocked()
            sessions = data["sessions"]
            thread_index = data["thread_index"]

            # Prefer explicit session id.
            if session_id:
                session = sessions.get(session_id)
                if not session:
                    raise KeyError(f"Session not found: {session_id}")
                if thread_id and session["thread_id"] != thread_id:
                    old_thread = session["thread_id"]
                    thread_index.pop(old_thread, None)
                    session["thread_id"] = thread_id
                    thread_index[thread_id] = session_id
                    session["updated_at"] = self._now_iso()
                    self._save_unlocked(data)
                return dict(session)

            # If no session id, try by thread id.
            if thread_id and thread_id in thread_index:
                sid = thread_index[thread_id]
                session = sessions.get(sid)
                if session:
                    return dict(session)

            # Legacy fallback: create a new session.
            now = self._now_iso()
            new_session_id = str(uuid.uuid4())
            new_thread_id = thread_id or str(uuid.uuid4())
            session = {
                "session_id": new_session_id,
                "thread_id": new_thread_id,
                "title": "New Chat",
                "created_at": now,
                "updated_at": now,
                "last_message_preview": "",
                "messages": [],
            }
            Path(output_dir, f"session_{new_thread_id}").mkdir(parents=True, exist_ok=True)
            sessions[new_session_id] = session
            thread_index[new_thread_id] = new_session_id
            self._save_unlocked(data)
            return dict(session)

    async def append_message(self, session_id: str, role: str, content: str):
        now = self._now_iso()
        msg = {"role": role, "content": content, "timestamp": now}
        async with self._lock:
            data = self._load_unlocked()
            session = data["sessions"].get(session_id)
            if not session:
                raise KeyError(session_id)
            session.setdefault("messages", []).append(msg)
            session["updated_at"] = now
            session["last_message_preview"] = self._message_preview(content)
            if role == "user" and session.get("title") == "New Chat":
                session["title"] = self._make_title_from_query(content)
            self._save_unlocked(data)

    async def build_history_context(self, session_id: str, max_messages: int = 12) -> str:
        async with self._lock:
            data = self._load_unlocked()
            session = data["sessions"].get(session_id)
            if not session:
                return ""
            messages = session.get("messages", [])

        tail = messages[-max_messages:]
        if not tail:
            return ""

        lines = []
        for row in tail:
            role = row.get("role", "user")
            content = str(row.get("content", "")).strip()
            if not content:
                continue
            prefix = "用户" if role == "user" else "助手"
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines)


session_store = ChatSessionStore(sessions_store_file)


def to_session_response(session: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(session)
    payload["session_dir"] = str(Path(output_dir) / f"session_{session['thread_id']}")
    return payload


def safe_remove_tree(base_dir: str, dir_name: str):
    base_path = Path(base_dir).resolve()
    target = (base_path / dir_name).resolve()
    if base_path not in target.parents:
        raise ValueError(f"Refusing to delete path outside base dir: {target}")
    if not target.exists():
        return
    shutil.rmtree(target)


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.loop = None

    def set_loop(self, loop):
        self.loop = loop
        monitor.set_websocket_manager(self)

    async def connect(self, websocket: WebSocket, thread_id: str):
        await websocket.accept()
        self.active_connections[thread_id] = websocket
        print(f"Client connected: {thread_id}")

    def disconnect(self, websocket: WebSocket, thread_id: str):
        if thread_id in self.active_connections:
            del self.active_connections[thread_id]
        print(f"Client disconnected: {thread_id}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def send_to_thread(self, message: dict, thread_id: str):
        if thread_id in self.active_connections:
            websocket = self.active_connections[thread_id]
            await websocket.send_json(message)


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bind the running loop at startup so monitor callbacks use the right loop.
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)
    print(f"[Server] WebSocket Manager bound to loop: {id(loop)}")
    print(f"[Server] stdout={sys.stdout.encoding}, stderr={sys.stderr.encoding}")
    print(
        "[Cleanup] Empty session dir cleanup enabled: "
        f"days={SESSION_CLEANUP_DAYS}, interval={SESSION_CLEANUP_INTERVAL_SECONDS}s"
    )
    cleanup_task = asyncio.create_task(run_session_dir_cleanup_loop(), name="session-dir-cleanup")
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="DeepAgents API", lifespan=lifespan)
app.mount("/outputs", StaticFiles(directory=output_dir), name="outputs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_utf8_charset(request, call_next):
    """Ensure text responses explicitly declare UTF-8 charset."""
    response = await call_next(request)
    content_type = response.headers.get("content-type")
    if not content_type:
        return response

    normalized = content_type.lower()
    if "charset=" not in normalized and (
        normalized.startswith("text/")
        or normalized.startswith("application/json")
        or normalized.startswith("application/javascript")
        or normalized.startswith("application/xml")
    ):
        response.headers["content-type"] = f"{content_type}; charset=utf-8"

    return response


class TaskRequest(BaseModel):
    query: str
    thread_id: str = None
    session_id: Optional[str] = None


class RetryTaskRequest(BaseModel):
    thread_id: Optional[str] = None
    session_id: Optional[str] = None


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


class TaskLifecycleManager:
    TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

    def __init__(self):
        self._records: Dict[str, Dict[str, Any]] = {}
        self._runtime_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _copy(record: Dict[str, Any]) -> Dict[str, Any]:
        return dict(record)

    async def create(
        self,
        query: str,
        thread_id: str,
        session_id: str,
        history_context: str = "",
        retry_of: Optional[str] = None,
    ) -> Dict[str, Any]:
        task_id = str(uuid.uuid4())
        record = {
            "task_id": task_id,
            "thread_id": thread_id,
            "session_id": session_id,
            "query": query,
            "history_context": history_context,
            "status": "queued",
            "created_at": self._now_iso(),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "retry_of": retry_of,
            "cancel_requested_at": None,
        }

        async with self._lock:
            self._records[task_id] = record
            runtime_task = asyncio.create_task(
                self._execute(task_id),
                name=f"deep-search-task-{task_id}",
            )
            self._runtime_tasks[task_id] = runtime_task

        return self._copy(record)

    async def _execute(self, task_id: str):
        async with self._lock:
            record = self._records.get(task_id)
            if not record:
                return
            record["status"] = "running"
            record["started_at"] = self._now_iso()
            query = record["query"]
            thread_id = record["thread_id"]
            session_id = record["session_id"]
            history_context = record.get("history_context", "")

        try:
            result = await run_deep_agent(query, thread_id, history_context=history_context)
        except asyncio.CancelledError:
            cancel_msg = "Task cancelled by user."
            async with self._lock:
                record = self._records.get(task_id)
                if record:
                    record["status"] = "cancelled"
                    record["finished_at"] = self._now_iso()
                    if not record.get("error"):
                        record["error"] = cancel_msg
            try:
                await session_store.append_message(session_id, "assistant", cancel_msg)
            except Exception:
                pass
            raise
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            async with self._lock:
                record = self._records.get(task_id)
                if record:
                    record["status"] = "failed"
                    record["finished_at"] = self._now_iso()
                    record["error"] = error_text
            try:
                await session_store.append_message(session_id, "assistant", error_text)
            except Exception:
                pass
        else:
            async with self._lock:
                record = self._records.get(task_id)
                if record:
                    record["status"] = "completed"
                    record["finished_at"] = self._now_iso()
            if result:
                try:
                    await session_store.append_message(session_id, "assistant", str(result))
                except Exception as exc:
                    print(f"[SessionStore] Failed to persist assistant message: {type(exc).__name__}: {exc}")
        finally:
            async with self._lock:
                self._runtime_tasks.pop(task_id, None)

    async def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            record = self._records.get(task_id)
            if not record:
                return None
            return self._copy(record)

    async def list(self, thread_id: Optional[str] = None) -> List[Dict[str, Any]]:
        async with self._lock:
            rows = []
            for record in self._records.values():
                if thread_id and record["thread_id"] != thread_id:
                    continue
                rows.append(self._copy(record))
        rows.sort(key=lambda item: item["created_at"], reverse=True)
        return rows

    async def cancel(self, task_id: str) -> Dict[str, Any]:
        async with self._lock:
            record = self._records.get(task_id)
            if not record:
                raise KeyError(task_id)

            if record["status"] in self.TERMINAL_STATUSES:
                return self._copy(record)

            record["cancel_requested_at"] = self._now_iso()
            record["status"] = "cancelling"
            runtime_task = self._runtime_tasks.get(task_id)
            if runtime_task and not runtime_task.done():
                runtime_task.cancel()
            else:
                record["status"] = "cancelled"
                record["finished_at"] = self._now_iso()
                if not record.get("error"):
                    record["error"] = "Task cancelled by user."
            return self._copy(record)

    async def cancel_by_session(self, session_id: str) -> int:
        cancelled = 0
        async with self._lock:
            for task_id, record in self._records.items():
                if record.get("session_id") != session_id:
                    continue
                if record["status"] in self.TERMINAL_STATUSES:
                    continue
                record["cancel_requested_at"] = self._now_iso()
                record["status"] = "cancelling"
                runtime_task = self._runtime_tasks.get(task_id)
                if runtime_task and not runtime_task.done():
                    runtime_task.cancel()
                else:
                    record["status"] = "cancelled"
                    record["finished_at"] = self._now_iso()
                    if not record.get("error"):
                        record["error"] = "Task cancelled by user."
                cancelled += 1
        return cancelled

    async def retry(
        self,
        task_id: str,
        thread_id: Optional[str] = None,
        session_id: Optional[str] = None,
        history_context: str = "",
    ) -> Dict[str, Any]:
        async with self._lock:
            source = self._records.get(task_id)
            if not source:
                raise KeyError(task_id)
            query = source["query"]
            target_thread_id = thread_id or source["thread_id"]
            target_session_id = session_id or source["session_id"]

        return await self.create(
            query=query,
            thread_id=target_thread_id,
            session_id=target_session_id,
            history_context=history_context,
            retry_of=task_id,
        )


task_lifecycle = TaskLifecycleManager()


@app.get("/api/sessions")
async def list_sessions():
    sessions = await session_store.list_sessions()
    return {"sessions": [to_session_response(s) for s in sessions]}


@app.post("/api/sessions", status_code=201)
async def create_session(request: Optional[CreateSessionRequest] = None):
    title = request.title if request else None
    session = await session_store.create_session(title=title)
    return to_session_response(session)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    session = await session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return to_session_response(session)


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    session = await session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    cancelled_tasks = await task_lifecycle.cancel_by_session(session_id)
    deleted = await session_store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    thread_id = deleted["thread_id"]
    session_dir_name = f"session_{thread_id}"
    try:
        safe_remove_tree(output_dir, session_dir_name)
    except Exception as exc:
        print(f"[SessionDelete] Failed to delete output dir {session_dir_name}: {type(exc).__name__}: {exc}")

    try:
        safe_remove_tree(updated_dir, session_dir_name)
    except Exception as exc:
        print(f"[SessionDelete] Failed to delete updated dir {session_dir_name}: {type(exc).__name__}: {exc}")

    return {
        "status": "deleted",
        "session_id": session_id,
        "thread_id": thread_id,
        "cancelled_tasks": cancelled_tasks,
    }


@app.get("/api/version")
async def api_version():
    return {
        "build_tag": BUILD_TAG,
        "stdout_encoding": sys.stdout.encoding,
        "stderr_encoding": sys.stderr.encoding,
        "pythonioencoding": os.environ.get("PYTHONIOENCODING"),
        "pythonutf8": os.environ.get("PYTHONUTF8"),
    }


@app.post("/api/task", status_code=202)
async def run_task(request: TaskRequest):
    try:
        resolved_session = await session_store.resolve_session(
            session_id=request.session_id,
            thread_id=request.thread_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    session_id = resolved_session["session_id"]
    thread_id = resolved_session["thread_id"]

    history_context = await session_store.build_history_context(session_id, max_messages=12)
    await session_store.append_message(session_id, "user", request.query)

    record = await task_lifecycle.create(
        query=request.query,
        thread_id=thread_id,
        session_id=session_id,
        history_context=history_context,
    )
    return {
        "status": "accepted",
        "task_id": record["task_id"],
        "thread_id": thread_id,
        "session_id": session_id,
        "session_dir": str(Path(output_dir) / f"session_{thread_id}"),
    }


@app.get("/api/task/{task_id}")
async def get_task(task_id: str):
    record = await task_lifecycle.get(task_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return record


@app.get("/api/tasks")
async def list_tasks(thread_id: Optional[str] = Query(default=None)):
    records = await task_lifecycle.list(thread_id=thread_id)
    return {"tasks": records}


@app.post("/api/task/{task_id}/cancel")
async def cancel_task(task_id: str):
    try:
        record = await task_lifecycle.cancel(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return record


@app.post("/api/task/{task_id}/retry", status_code=202)
async def retry_task(task_id: str, request: Optional[RetryTaskRequest] = None):
    thread_id = request.thread_id if request else None
    session_id = request.session_id if request else None

    try:
        resolved_session = await session_store.resolve_session(
            session_id=session_id,
            thread_id=thread_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    resolved_thread_id = resolved_session["thread_id"]
    resolved_session_id = resolved_session["session_id"]
    history_context = await session_store.build_history_context(resolved_session_id, max_messages=12)

    try:
        new_record = await task_lifecycle.retry(
            task_id=task_id,
            thread_id=resolved_thread_id,
            session_id=resolved_session_id,
            history_context=history_context,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    return {
        "status": "accepted",
        "task_id": new_record["task_id"],
        "thread_id": new_record["thread_id"],
        "session_id": new_record["session_id"],
        "session_dir": str(Path(output_dir) / f"session_{new_record['thread_id']}"),
        "retry_of": new_record["retry_of"],
    }


@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), thread_id: str = Form(...)):
    target_dir = os.path.join(updated_dir, f"session_{thread_id}")
    os.makedirs(target_dir, exist_ok=True)

    saved_files = []
    for file in files:
        file_path = os.path.join(target_dir, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_files.append(file.filename)

    return {"status": "uploaded", "files": saved_files}


@app.get("/api/download")
async def download_file(path: str):
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(os.path.abspath(output_dir)):
        return {"error": "Access denied: Path must be within output directory"}

    if not os.path.exists(abs_path):
        return {"error": "File not found"}

    return FileResponse(abs_path, filename=os.path.basename(abs_path))


@app.get("/api/files")
async def list_files(path: str):
    print(f"[DEBUG] list_files request path: {path}")
    abs_path = os.path.abspath(path)
    output_abs = os.path.abspath(output_dir)
    print(f"[DEBUG] abs_path: {abs_path}")
    print(f"[DEBUG] output_dir abs: {output_abs}")

    try:
        if sys.platform == "win32":
            check_path = os.path.normcase(abs_path)
            check_output = os.path.normcase(output_abs)
        else:
            check_path = abs_path
            check_output = output_abs

        if not check_path.startswith(check_output):
            print(f"[ERROR] Access denied. {check_path} not startswith {check_output}")
            return {"error": "Access denied: Path must be within output directory"}
    except Exception as e:
        print(f"[ERROR] Path check failed: {e}")
        return {"error": f"Path check failed: {e}"}

    if not os.path.exists(abs_path):
        print(f"[ERROR] Path not found: {abs_path}")
        return {"error": "Path not found"}

    files = []
    try:
        for root, dirs, filenames in os.walk(abs_path):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, output_dir)
                url_path = rel_path.replace("\\", "/")

                files.append(
                    {
                        "name": filename,
                        "type": "file",
                        "path": file_path,
                        "url": f"/outputs/{url_path}",
                        "size": os.path.getsize(file_path),
                        "mtime": os.path.getmtime(file_path),
                    }
                )
    except Exception as e:
        print(f"[ERROR] Walk failed: {e}")
        return {"error": str(e)}

    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    print(f"[DEBUG] Found {len(files)} files")
    return {"files": files}


@app.websocket("/ws")
async def websocket_legacy(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({"type": "error", "message": "Client outdated. Please refresh page."})
    await websocket.close(code=1000, reason="Client outdated")


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    await manager.connect(websocket, thread_id)
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({"type": "pong", "message": f"received: {data}"})
    except WebSocketDisconnect:
        manager.disconnect(websocket, thread_id)
    except Exception as e:
        print(f"WebSocket Error: {e}")
        manager.disconnect(websocket, thread_id)


def is_port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return False
        except OSError:
            return True


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8001"))

    if is_port_in_use(host, port):
        print(
            f"[ERROR] Port {port} is already in use on {host}. "
            "Set another port with environment variable PORT."
        )
        sys.exit(1)

    uvicorn.run("api.server:app", host=host, port=port, reload=False)

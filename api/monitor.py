import datetime
import asyncio
import sys
from typing import Any, Dict, Optional
from api.context import get_task_context, get_thread_context

# 尝试导入全局运行时（用于脚本模式下的流式输出）
try:
    import builtins
except ImportError:
    builtins = None


class ToolMonitor:
    """
    工具监控类：用于在工具执行过程中上报进度与状态。

    设计目标：
    1. 单例模式：在任意模块中 `from api.monitor import monitor` 直接复用。
    2. 双通道输出：优先走 FastAPI WebSocket；脚本模式下可回落到 runtime.stream_writer。
    3. 控制台兜底：便于本地调试与排障。
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolMonitor, cls).__new__(cls)
            # 运行时注入的 WebSocket 管理器
            cls._instance.websocket_manager = None
        return cls._instance

    def set_websocket_manager(self, manager):
        """设置 FastAPI 的 WebSocket 管理器。"""
        self.websocket_manager = manager

    @staticmethod
    def _sanitize_text(text: Any) -> str:
        """Replace characters that are known to break legacy encoders (gbk, cp936)."""
        s = str(text)
        out = []
        for ch in s:
            cp = ord(ch)
            if (0xD800 <= cp <= 0xDFFF) or (0xE000 <= cp <= 0xF8FF):
                out.append("?")
            else:
                out.append(ch)
        return "".join(out)

    @classmethod
    def _sanitize_data(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {cls._sanitize_text(k): cls._sanitize_data(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._sanitize_data(v) for v in value]
        if isinstance(value, tuple):
            return tuple(cls._sanitize_data(v) for v in value)
        if isinstance(value, (str, bytes)):
            return cls._sanitize_text(value)
        return value

    @staticmethod
    def _safe_print(text: str):
        text = ToolMonitor._sanitize_text(text)
        try:
            print(text)
            return
        except UnicodeEncodeError:
            pass

        try:
            enc = sys.stdout.encoding or "utf-8"
            safe_bytes = text.encode(enc, errors="replace")
            if hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write(safe_bytes + b"\n")
            else:
                sys.stdout.write(safe_bytes.decode(enc, errors="replace") + "\n")
        except Exception:
            # 最后一层兜底，避免日志输出影响主流程
            pass

    def _emit(self, event_type: str, message: str, data: Optional[Dict[str, Any]] = None):
        """内部统一消息分发方法。"""
        safe_message = self._sanitize_text(message)
        safe_data = self._sanitize_data(data or {})
        payload = {
            "type": "monitor_event",
            "event": event_type,
            "message": safe_message,
            "data": safe_data,
            "timestamp": datetime.datetime.now().isoformat(),
        }

        try:
            from api.evaluation import evaluation_store

            evaluation_store.record_event(
                event_type=event_type,
                message=safe_message,
                payload=safe_data,
                task_id=get_task_context(),
                thread_id=get_thread_context(),
            )
        except Exception:
            pass

        # 1) 优先通过 FastAPI WebSocket 定向推送
        if self.websocket_manager:
            try:
                thread_id = get_thread_context()
                if hasattr(self.websocket_manager, "loop") and self.websocket_manager.loop:
                    if thread_id:
                        asyncio.run_coroutine_threadsafe(
                            self.websocket_manager.send_to_thread(payload, thread_id),
                            self.websocket_manager.loop,
                        )
            except Exception as e:
                self._safe_print(f"[Monitor] WebSocket send failed: {e}")

        # 2) 脚本模式下回落到 runtime.stream_writer
        if builtins and hasattr(builtins, "runtime") and hasattr(builtins.runtime, "stream_writer"):
            try:
                builtins.runtime.stream_writer(payload)
            except Exception:
                pass

        # 3) 控制台兜底输出（便于调试）
        self._safe_print(f"\n[Monitor:{event_type}] {safe_message}")

    def report_tool(self, tool_name: str, args: Dict[str, Any] = None):
        """上报工具开始执行。"""
        self._emit("tool_start", f"开始执行工具: {tool_name}", {"tool_name": tool_name, "args": args})

    def report_assistant(self, assistant_name: str, args: Dict[str, Any] = None):
        """上报子智能体调用进度。"""
        self._emit(
            "assistant_call",
            f"正在调用助手: {assistant_name}",
            {"assistant_name": assistant_name, "args": args},
        )

    def report_task_result(self, result: str):
        """上报任务最终结果。"""
        self._emit("task_result", "任务执行完成", {"result": result})

    def report_session_dir(self, path: str):
        """上报任务会话工作目录。"""
        self._emit("session_created", f"工作目录已创建: {path}", {"path": path})


# 全局单例
monitor = ToolMonitor()

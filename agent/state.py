from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


@dataclass
class AgentState:
    messages: list[dict[str, Any]] = field(default_factory=list)
    active_session_id: str | None = None
    last_diagnosis: str | None = None
    chat_log_path: Path = field(default_factory=lambda: Path("logs/chat_history.jsonl"))
    tool_log_path: Path = field(default_factory=lambda: Path("logs/tool_calls.jsonl"))

    def log_chat(self, role: str, content: str) -> None:
        append_jsonl(
            self.chat_log_path,
            {"ts": utc_now_iso(), "role": role, "content": content},
        )

    def log_tool(
        self,
        *,
        name: str,
        args: dict[str, Any],
        result: dict[str, Any] | Any,
        ok: bool,
        error: str | None = None,
    ) -> None:
        append_jsonl(
            self.tool_log_path,
            {
                "ts": utc_now_iso(),
                "tool": name,
                "args": args,
                "ok": ok,
                "error": error,
                "result": result,
            },
        )


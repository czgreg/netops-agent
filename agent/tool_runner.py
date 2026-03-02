from __future__ import annotations

import json
import os
from typing import Any

from agent.state import AgentState


NO_SESSION_TOOLS = {"device_connect", "device_list_sessions"}


def build_tool_schema(mcp_tools: Any) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for t in getattr(mcp_tools, "tools", []):
        tools.append(
            {
                "type": "function",
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema or {"type": "object"},
            }
        )
    return tools


async def ensure_default_connection(session: Any, state: AgentState) -> None:
    if state.active_session_id:
        return

    sessions = await session.call_tool("device_list_sessions", {})
    if isinstance(sessions, dict) and sessions.get("sessions"):
        first = sessions["sessions"][0]
        sid = first.get("session_id")
        if sid:
            state.active_session_id = str(sid)
            return

    connect_args: dict[str, Any] = {
        "host": os.getenv("START_DEVICE", "10.0.0.11"),
        "protocol": os.getenv("PROTOCOL", "telnet"),
        "device_type": os.getenv("DEVICE_TYPE", "cisco_ios"),
    }
    if os.getenv("NETPILOT_PORT"):
        connect_args["port"] = os.getenv("NETPILOT_PORT")
    if os.getenv("NETPILOT_USERNAME"):
        connect_args["username"] = os.getenv("NETPILOT_USERNAME")
    if os.getenv("NETPILOT_PASSWORD"):
        connect_args["password"] = os.getenv("NETPILOT_PASSWORD")
    if os.getenv("NETPILOT_ENABLE_PASSWORD"):
        connect_args["enable_password"] = os.getenv("NETPILOT_ENABLE_PASSWORD")

    result = await session.call_tool("device_connect", connect_args)
    if isinstance(result, dict) and result.get("session_id"):
        state.active_session_id = str(result["session_id"])


def parse_tool_args(raw_args: str | None) -> dict[str, Any]:
    if not raw_args:
        return {}
    try:
        parsed = json.loads(raw_args)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


async def execute_tool_call(
    *,
    session: Any,
    state: AgentState,
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    real_args = dict(args)
    if tool_name not in NO_SESSION_TOOLS and state.active_session_id and "session_id" not in real_args:
        real_args["session_id"] = state.active_session_id

    try:
        result = await session.call_tool(tool_name, real_args)
        if tool_name == "device_connect" and isinstance(result, dict) and result.get("session_id"):
            state.active_session_id = str(result["session_id"])
        if tool_name == "device_disconnect":
            state.active_session_id = None
        state.log_tool(name=tool_name, args=real_args, result=result, ok=True)
        return {
            "ok": True,
            "result": result,
            "args": real_args,
        }
    except Exception as exc:  # pylint: disable=broad-except
        state.log_tool(
            name=tool_name,
            args=real_args,
            result={"error": str(exc)},
            ok=False,
            error=str(exc),
        )
        return {
            "ok": False,
            "result": {"error": str(exc)},
            "args": real_args,
        }


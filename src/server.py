"""
MCP SSH Server — точка входа.

Инструменты (MCP Tools):
  Общие:
    ssh_connect        — открыть сессию (linux/sros)
    ssh_disconnect     — закрыть сессию
    ssh_list_sessions  — список активных сессий

  Linux:
    ssh_exec           — выполнить команду
    ssh_exec_multi     — выполнить список команд
    ssh_send_raw       — отправить raw-текст (без ожидания промпта)
    linux_os_info      — информация об ОС

  Nokia SR OS (MD-CLI):
    sros_cli           — выполнить show/operational команду
    sros_configure     — выполнить блок конфигурации + commit/discard
    sros_get_context   — текущий CLI-контекст (pwc)
    sros_rollback      — откатить конфиг

Запуск:
  python -m src.server
  # или через uv:
  uv run mcp-ssh-server
"""

import asyncio
import json
import logging
import sys
from typing import Any, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    ListToolsResult,
    TextContent,
    Tool,
)

from .session_manager import SessionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("mcp-ssh-server")

app = Server("mcp-ssh-server")
sessions = SessionManager(default_ttl=7200)


# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    Tool(
        name="ssh_connect",
        description=(
            "Open an SSH session to a Linux host or Nokia SR OS device. "
            "Returns a session_id (or the label you provided) for subsequent calls. "
            "Credentials are passed at runtime and never stored to disk."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "IP address or hostname"},
                "port": {"type": "integer", "default": 22, "description": "SSH port"},
                "username": {"type": "string"},
                "password": {"type": "string", "description": "Password (optional if private_key provided)"},
                "private_key": {"type": "string", "description": "PEM-encoded private key string (optional)"},
                "device_type": {
                    "type": "string",
                    "enum": ["linux", "sros"],
                    "default": "linux",
                    "description": "'sros' for Nokia SR OS MD-CLI, 'linux' for bash shell",
                },
                "label": {
                    "type": "string",
                    "description": "Optional human-readable session name (e.g. 'pe1'). Used as session_id.",
                },
                "timeout": {"type": "integer", "default": 30, "description": "Connection timeout in seconds"},
            },
            "required": ["host", "username"],
        },
    ),
    Tool(
        name="ssh_disconnect",
        description="Close an active SSH session.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID or label from ssh_connect"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="ssh_list_sessions",
        description="List all active SSH sessions with their status.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # ── Linux tools ──────────────────────────────────────────────────────────
    Tool(
        name="ssh_exec",
        description="Execute a shell command on a Linux host. Returns stdout/stderr.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout": {"type": "number", "default": 60, "description": "Timeout in seconds"},
            },
            "required": ["session_id", "command"],
        },
    ),
    Tool(
        name="ssh_exec_multi",
        description="Execute multiple shell commands sequentially on a Linux host.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of shell commands",
                },
                "timeout": {"type": "number", "default": 60},
            },
            "required": ["session_id", "commands"],
        },
    ),
    Tool(
        name="ssh_send_raw",
        description=(
            "Send raw text to the SSH shell without waiting for a prompt. "
            "Useful for interactive programs, Ctrl sequences, etc."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "text": {"type": "string", "description": "Text to send (use \\n for newline, \\x03 for Ctrl-C)"},
                "wait_seconds": {"type": "number", "default": 1.0, "description": "Seconds to wait after sending"},
            },
            "required": ["session_id", "text"],
        },
    ),
    Tool(
        name="linux_os_info",
        description="Get OS information (hostname, uname, /etc/os-release) from a Linux host.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    # ── SR OS tools ──────────────────────────────────────────────────────────
    Tool(
        name="sros_cli",
        description=(
            "Execute an MD-CLI operational command on Nokia SR OS. "
            "Automatically appends '| no-more' to show commands. "
            "Use this for 'show', 'info', 'ping', 'traceroute', etc."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "command": {"type": "string", "description": "MD-CLI command"},
                "timeout": {"type": "number", "default": 60},
            },
            "required": ["session_id", "command"],
        },
    ),
    Tool(
        name="sros_configure",
        description=(
            "Enter configure mode on Nokia SR OS, execute a list of MD-CLI config commands, "
            "then commit or discard. Returns per-command output and commit result."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of configuration commands (without 'configure' prefix)",
                },
                "commit": {
                    "type": "boolean",
                    "default": True,
                    "description": "True to commit, False to discard",
                },
            },
            "required": ["session_id", "commands"],
        },
    ),
    Tool(
        name="sros_get_context",
        description="Get the current MD-CLI context path (equivalent to 'pwc' command).",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="sros_rollback",
        description="Rollback SR OS configuration by N steps.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "index": {"type": "integer", "default": 1, "description": "Rollback steps"},
            },
            "required": ["session_id"],
        },
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools(request: ListToolsRequest) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


@app.call_tool()
async def call_tool(request: CallToolRequest) -> CallToolResult:
    name = request.params.name
    args: dict[str, Any] = request.params.arguments or {}

    try:
        result = await _dispatch(name, args)
        return CallToolResult(
            content=[TextContent(type="text", text=_to_text(result))]
        )
    except KeyError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Error: {e}")],
            isError=True,
        )
    except TimeoutError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Timeout: {e}")],
            isError=True,
        )
    except Exception as e:
        logger.exception(f"Tool '{name}' failed")
        return CallToolResult(
            content=[TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")],
            isError=True,
        )


def _to_text(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, ensure_ascii=False, indent=2)


async def _dispatch(name: str, args: dict) -> Any:
    # ── Session management ────────────────────────────────────────────────

    if name == "ssh_connect":
        session_id = await sessions.create_session(
            host=args["host"],
            username=args["username"],
            password=args.get("password"),
            private_key=args.get("private_key"),
            port=args.get("port", 22),
            device_type=args.get("device_type", "linux"),
            label=args.get("label"),
            timeout=args.get("timeout", 30),
        )
        session_list = sessions.list_sessions()
        info = next((s for s in session_list if s["session_id"] == session_id), {})
        return {
            "session_id": session_id,
            "host": info.get("host"),
            "device_type": info.get("device_type"),
            "status": "connected",
        }

    if name == "ssh_disconnect":
        await sessions.close_session(args["session_id"])
        return {"status": "disconnected", "session_id": args["session_id"]}

    if name == "ssh_list_sessions":
        return sessions.list_sessions()

    # ── Linux ─────────────────────────────────────────────────────────────

    if name == "ssh_exec":
        session = await sessions.get_session(args["session_id"])
        output = await session.exec(args["command"], timeout=args.get("timeout", 60))
        return {"command": args["command"], "output": output}

    if name == "ssh_exec_multi":
        session = await sessions.get_session(args["session_id"])
        results = await session.exec_multi(args["commands"], timeout=args.get("timeout", 60))
        return results

    if name == "ssh_send_raw":
        session = await sessions.get_session(args["session_id"])
        text = args["text"].encode().decode("unicode_escape")  # handle \n \x03 etc.
        await session._send(text)
        wait = args.get("wait_seconds", 1.0)
        await asyncio.sleep(wait)
        # Drain whatever arrived
        drained = session._buffer
        session._buffer = ""
        return {"sent": repr(text), "received": drained}

    if name == "linux_os_info":
        session = await sessions.get_session(args["session_id"])
        return await session.get_os_info()

    # ── SR OS ─────────────────────────────────────────────────────────────

    if name == "sros_cli":
        session = await sessions.get_session(args["session_id"])
        output = await session.cli(args["command"], timeout=args.get("timeout", 60))
        return {"command": args["command"], "output": output}

    if name == "sros_configure":
        session = await sessions.get_session(args["session_id"])
        result = await session.configure(
            commands=args["commands"],
            commit=args.get("commit", True),
        )
        return result

    if name == "sros_get_context":
        session = await sessions.get_session(args["session_id"])
        context = await session.get_context()
        return {"context": context}

    if name == "sros_rollback":
        session = await sessions.get_session(args["session_id"])
        output = await session.rollback(args.get("index", 1))
        return {"output": output}

    raise ValueError(f"Unknown tool: {name}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def _run():
    logger.info("MCP SSH Server starting...")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()

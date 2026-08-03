"""
MCP Manager — connects to MCP servers (stdio & HTTP), discovers tools,
and forwards tool calls to the right server.

Config is read from / written to the global ~/.claude.json (mcpServers section)
so servers installed by TOMAS are also visible to Claude Code and vice versa.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

# ── paths ──────────────────────────────────────────────────────────────

CLAUDE_CONFIG = Path.home() / ".claude.json"


# ═══════════════════════════════════════════════════════════════════════
#  Config I/O  (read/write mcpServers from claude.json)
# ═══════════════════════════════════════════════════════════════════════

def _read_claude_json() -> dict:
    """Read the full claude.json (safe if missing or corrupt)."""
    if not CLAUDE_CONFIG.exists():
        return {}
    try:
        return json.loads(CLAUDE_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_claude_json(data: dict) -> None:
    """Write back to claude.json (preserving all existing keys)."""
    CLAUDE_CONFIG.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def read_mcp_servers() -> dict[str, dict]:
    """Return the {name: config} dict from the global mcpServers section."""
    return _read_claude_json().get("mcpServers", {})


def write_mcp_server(name: str, config: dict) -> None:
    """Add/update a single MCP server entry in the global config."""
    data = _read_claude_json()
    if "mcpServers" not in data:
        data["mcpServers"] = {}
    data["mcpServers"][name] = config
    _write_claude_json(data)


def remove_mcp_server(name: str) -> bool:
    """Remove an MCP server from the global config. Returns True if existed."""
    data = _read_claude_json()
    servers = data.get("mcpServers", {})
    if name not in servers:
        return False
    del servers[name]
    data["mcpServers"] = servers
    _write_claude_json(data)
    return True

def is_server_disabled(name: str) -> bool:
    """Check if an MCP server is marked as disabled in the config."""
    servers = read_mcp_servers()
    cfg = servers.get(name, {})
    return cfg.get("disabled", False)


def set_server_disabled(name: str, disabled: bool = True) -> bool:
    """Mark an MCP server as disabled or enabled. Returns True if existed."""
    servers = read_mcp_servers()
    if name not in servers:
        return False
    cfg = servers[name]
    if disabled:
        cfg["disabled"] = True
    else:
        cfg.pop("disabled", None)
    write_mcp_server(name, cfg)
    return True


def get_server_env(name: str) -> dict[str, str]:
    """Get the env vars dict for an MCP server."""
    servers = read_mcp_servers()
    cfg = servers.get(name, {})
    return cfg.get("env", {})


def test_mcp_connections() -> dict[str, dict]:
    """
    Test connections to all configured MCP servers.
    Returns {name: {"connected": bool, "error": str, "disabled": bool, "tool_count": int}}
    Does NOT print anything to stderr.
    """
    import io
    from contextlib import redirect_stderr

    servers_config = read_mcp_servers()
    results: dict[str, dict] = {}

    # Redirect stderr to capture (and discard) connection errors
    stderr_capture = io.StringIO()
    with redirect_stderr(stderr_capture):
        for name, cfg in servers_config.items():
            disabled = cfg.get("disabled", False)
            if disabled:
                results[name] = {"connected": False, "error": None, "disabled": True, "tool_count": 0}
                continue
            try:
                server = MCPServer(name, cfg)
                ok = server.connect()
                results[name] = {
                    "connected": ok,
                    "error": None if ok else server._last_error,
                    "disabled": False,
                    "tool_count": len(server.tools) if ok else 0,
                }
                if ok:
                    server.disconnect()
            except Exception as exc:
                results[name] = {
                    "connected": False,
                    "error": str(exc),
                    "disabled": False,
                    "tool_count": 0,
                }
    return results

# ═══════════════════════════════════════════════════════════════════════
#  JSON-RPC helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_request(method: str, params: Any = None, _ids: list = None) -> dict:
    if _ids is None:
        _ids = [0]
    _ids[0] += 1
    msg: dict = {"jsonrpc": "2.0", "id": _ids[0], "method": method}
    if params is not None:
        msg["params"] = params
    return msg


_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "TOMAS", "version": "1.0.0"},
    },
}
_NOTIFICATION_INITIALIZED = {
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
}


# ═══════════════════════════════════════════════════════════════════════
#  Individual MCP Server connection
# ═══════════════════════════════════════════════════════════════════════

class MCPServer:
    """Manages a single MCP server connection (stdio or HTTP)."""

    def __init__(self, name: str, config: dict):
        self.name = name
        self.transport = config.get("type", "stdio")  # "stdio" | "http"
        self.command = config.get("command", "")
        self.args = config.get("args", [])
        self.url = config.get("url", "")
        self.env = config.get("env", {})
        self.tools: list[dict] = []
        # The MCP spec defines resources (files, rows, docs the server exposes
        # for reading) and prompts (server-provided templates) alongside
        # tools. A lot of the ecosystem's value is in those two, and both are
        # cheap on top of the JSON-RPC plumbing already here.
        self.resources: list[dict] = []
        self.prompts: list[dict] = []
        self.connected = False
        self._last_error: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._id_seq = [0]
        self._read_buffer = b""

    # ── public API ──────────────────────────────────────────────────

    def connect(self) -> bool:
        """Start the server and discover its tools."""
        try:
            if self.transport == "http":
                return self._connect_http()
            return self._connect_stdio()
        except Exception as exc:
            msg = str(exc)
            self._last_error = msg
            return False

    def call_tool(self, name: str, arguments: dict) -> str:
        """Call a tool on this server and return the result text."""
        if not self.connected:
            return f"Error: MCP server '{self.name}' is not connected."
        with self._lock:
            req = _make_request("tools/call", {"name": name, "arguments": arguments}, self._id_seq)
            if self.transport == "http":
                return self._http_call(req)
            return self._stdio_call(req)

    def _request(self, method: str, params: Optional[dict] = None) -> str:
        """Send one JSON-RPC request and return its text result."""
        if not self.connected:
            return f"Error: MCP server '{self.name}' is not connected."
        with self._lock:
            req = _make_request(method, params or {}, self._id_seq)
            if self.transport == "http":
                return self._http_call(req)
            return self._stdio_call(req)

    def _discover_optional(self) -> None:
        """List resources and prompts. Both are optional in the spec, so a
        server that does not implement them must not fail to connect."""
        for method, attr, key in (("resources/list", "resources", "resources"),
                                  ("prompts/list", "prompts", "prompts")):
            try:
                with self._lock:
                    req = _make_request(method, {}, self._id_seq)
                    if self.transport == "http":
                        resp = self._http_post(req)
                    else:
                        self._stdio_send(req)
                        resp = self._stdio_recv()
                if resp and "result" in resp:
                    setattr(self, attr, resp["result"].get(key, []) or [])
            except Exception:
                continue

    def read_resource(self, uri: str) -> str:
        """Read one resource by URI."""
        return self._request("resources/read", {"uri": uri})

    def get_prompt(self, name: str, arguments: Optional[dict] = None) -> str:
        """Render one server-provided prompt template."""
        return self._request("prompts/get",
                             {"name": name, "arguments": arguments or {}})

    def disconnect(self) -> None:
        """Gracefully shut down the server."""
        self.connected = False
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()
            self._proc = None

    # ── stdio transport ─────────────────────────────────────────────

    def _connect_stdio(self) -> bool:
        if not self.command:
            return False
        env = os.environ.copy()
        env.update({k: v for k, v in self.env.items() if isinstance(v, str)})

        # On Windows, many MCP commands are .cmd files (npx.cmd, etc.).
        # shell=True routes through cmd.exe so they can be found.
        use_shell = os.name == "nt"

        if use_shell:
            # Build a command line string for cmd.exe
            cmd_parts = [self.command] + self.args
            cmd_line = subprocess.list2cmdline(cmd_parts)
            self._proc = subprocess.Popen(
                cmd_line,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=False,
                shell=True,
            )
        else:
            self._proc = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=False,
            )
        # Send initialize
        self._stdio_send(_INITIALIZE)
        resp = self._stdio_recv()
        if resp is None or "result" not in resp:
            raise RuntimeError(f"initialize failed: {resp}")
        self._stdio_send(_NOTIFICATION_INITIALIZED)
        # List tools
        req = _make_request("tools/list", _ids=self._id_seq)
        self._stdio_send(req)
        tools_resp = self._stdio_recv()
        if tools_resp and "result" in tools_resp:
            self.tools = tools_resp["result"].get("tools", [])
        self.connected = True
        self._discover_optional()
        return True

    def _stdio_send(self, msg: dict) -> None:
        if self._proc and self._proc.stdin:
            line = json.dumps(msg, ensure_ascii=False) + "\n"
            self._proc.stdin.write(line.encode("utf-8"))
            self._proc.stdin.flush()

    def _stdio_recv(self) -> Optional[dict]:
        if not self._proc or not self._proc.stdout:
            return None
        line = self._proc.stdout.readline()
        if not line:
            return None
        return json.loads(line.decode("utf-8"))

    def _result_text(self, resp: Optional[dict]) -> str:
        """Text out of any MCP result shape.

        tools/call returns `content`, resources/read returns `contents`, and
        prompts/get returns `messages`. One extractor rather than three.
        """
        if resp is None:
            return f"Error: no response from MCP server '{self.name}'"
        if "error" in resp:
            err = resp["error"]
            return f"Error: MCP [{self.name}] {err.get('message', 'unknown error')}"
        result = resp.get("result", {}) or {}
        texts: list[str] = []

        def take(block: dict) -> None:
            if not isinstance(block, dict):
                return
            if block.get("text"):
                texts.append(block["text"])
            elif block.get("blob"):
                mime = block.get("mimeType", "application/octet-stream")
                texts.append(f"[binary resource, {mime}, "
                             f"{len(block['blob'])} base64 chars]")

        for block in result.get("content", []) or []:
            take(block)
        for block in result.get("contents", []) or []:
            take(block)
        for msg in result.get("messages", []) or []:
            content = (msg or {}).get("content")
            if isinstance(content, dict):
                take(content)
            elif isinstance(content, list):
                for block in content:
                    take(block)
            elif isinstance(content, str):
                texts.append(content)
        if not texts and result.get("description"):
            texts.append(result["description"])
        return "\n".join(t for t in texts if t) or "(no output)"

    def _stdio_call(self, req: dict) -> str:
        self._stdio_send(req)
        return self._result_text(self._stdio_recv())

    # ── HTTP transport ──────────────────────────────────────────────

    def _connect_http(self) -> bool:
        if not self.url:
            return False
        # Initialize via HTTP POST
        init_resp = self._http_post(_INITIALIZE)
        if init_resp is None or "result" not in init_resp:
            raise RuntimeError(f"initialize failed: {init_resp}")
        self._http_post(_NOTIFICATION_INITIALIZED)
        # List tools
        req = _make_request("tools/list", _ids=self._id_seq)
        tools_resp = self._http_post(req)
        if tools_resp and "result" in tools_resp:
            self.tools = tools_resp["result"].get("tools", [])
        self.connected = True
        self._discover_optional()
        return True

    def _http_post(self, msg: dict) -> Optional[dict]:
        if not self.url:
            return None
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        try:
            req = urllib.request.Request(
                self.url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "TOMAS/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"network error: {e.reason}")

    def _http_call(self, req: dict) -> str:
        return self._result_text(self._http_post(req))


# ═══════════════════════════════════════════════════════════════════════
#  MCP Manager — aggregate of all servers
# ═══════════════════════════════════════════════════════════════════════

class MCPManager:
    """Discovers, connects, and manages all MCP servers."""

    def __init__(self):
        self.servers: dict[str, MCPServer] = {}
        self._all_tools: list[dict] = []
        self.failed_servers: dict[str, str] = {}  # name -> error message
        # exposed tool name -> (server name, original tool name). Two servers
        # can expose the same tool (chrome-devtools and playwright both have
        # take_screenshot); routing by first match sent half those calls to
        # the wrong server, and emitting both under one name put duplicate
        # names in the API payload.
        self._owner: dict[str, tuple[str, str]] = {}

    def discover_and_connect(self, config: dict[str, dict] = None,
                             parallel: bool = True,
                             max_workers: int = 8) -> list[dict]:
        """
        Read MCP config (from global config or provided dict),
        connect to every server, and return the combined tool list.
        Skips servers marked as disabled.

        Connections run concurrently. Serially, 17 servers took 21.5 s before
        the user could type anything — most of it spent waiting out the
        timeouts of six servers that were never going to answer.

        Registration is deliberately *not* concurrent: names are merged
        afterwards in config order, so which server wins an uncontested name
        does not depend on which thread finished first. Same result as the
        serial version, ~6x faster.
        """
        if config is None:
            config = read_mcp_servers()
        entries = [(n, c) for n, c in config.items() if not c.get("disabled", False)]
        if not entries:
            return self._all_tools

        def attempt(item: tuple[str, dict]):
            name, cfg = item
            try:
                server = MCPServer(name, cfg)
                ok = server.connect()
            except Exception as exc:                # a bad config must not
                return name, None, str(exc)          # take the whole startup down
            return name, (server if ok else None), (
                None if ok else (server._last_error or "unknown error"))

        if parallel and len(entries) > 1:
            with ThreadPoolExecutor(max_workers=min(max_workers, len(entries))) as pool:
                results = list(pool.map(attempt, entries))
        else:
            results = [attempt(e) for e in entries]

        # Merge in config order — deterministic exposed names.
        for name, server, error in results:
            if server is None:
                self.failed_servers[name] = error or "unknown error"
                continue
            self.servers[name] = server
            for t in server.tools:
                self._register_tool(name, t)
        return self._all_tools

    def _register_tool(self, server_name: str, tool: dict) -> None:
        """Expose one tool, namespacing it if the name is already claimed."""
        original = tool.get("name", "?")
        if original in self._owner:
            exposed = f"mcp_{server_name}_{original}"
            # Pathological case: even the namespaced name collides.
            suffix = 2
            while exposed in self._owner:
                exposed = f"mcp_{server_name}_{original}_{suffix}"
                suffix += 1
        else:
            exposed = original
        entry = self._to_anthropic_tool(tool)
        entry["name"] = exposed
        self._all_tools.append(entry)
        self._owner[exposed] = (server_name, original)

    @property
    def tools(self) -> list[dict]:
        """All MCP tools across all connected servers. Names are unique."""
        return self._all_tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Route a tool call to the server that owns the exposed name."""
        owner = self._owner.get(tool_name)
        if owner is None:
            return f"Error: MCP tool '{tool_name}' not found on any connected server."
        server_name, original = owner
        return self.servers[server_name].call_tool(original, arguments)

    def get_server_for_tool(self, tool_name: str) -> str | None:
        """Return the server name that owns the given tool, or None."""
        owner = self._owner.get(tool_name)
        return owner[0] if owner else None

    # ── Resources ───────────────────────────────────────────────────

    def list_resources(self) -> list[dict]:
        """Every resource across connected servers, tagged with its owner."""
        out: list[dict] = []
        for name, server in self.servers.items():
            for r in server.resources:
                entry = dict(r)
                entry["server"] = name
                out.append(entry)
        return out

    def read_resource(self, uri: str, server: str | None = None) -> str:
        """Read a resource by URI.

        With no server given, the URI is looked up across all of them — and
        an ambiguous URI is reported rather than silently resolved to
        whichever server happened to connect first.
        """
        if server:
            srv = self.servers.get(server)
            if srv is None:
                return f"Error: MCP server '{server}' is not connected."
            return srv.read_resource(uri)
        owners = [n for n, s in self.servers.items()
                  if any(r.get("uri") == uri for r in s.resources)]
        if not owners:
            return f"Error: no connected MCP server exposes resource '{uri}'."
        if len(owners) > 1:
            return (f"Error: resource '{uri}' is exposed by {len(owners)} servers "
                    f"({', '.join(owners)}). Pass server= to disambiguate.")
        return self.servers[owners[0]].read_resource(uri)

    # ── Prompts ─────────────────────────────────────────────────────

    def list_prompts(self) -> list[dict]:
        """Every server-provided prompt template, tagged with its owner."""
        out: list[dict] = []
        for name, server in self.servers.items():
            for p in server.prompts:
                entry = dict(p)
                entry["server"] = name
                out.append(entry)
        return out

    def get_prompt(self, name: str, arguments: dict | None = None,
                   server: str | None = None) -> str:
        """Render a server-provided prompt template."""
        if server:
            srv = self.servers.get(server)
            if srv is None:
                return f"Error: MCP server '{server}' is not connected."
            return srv.get_prompt(name, arguments)
        owners = [n for n, s in self.servers.items()
                  if any(p.get("name") == name for p in s.prompts)]
        if not owners:
            return f"Error: no connected MCP server provides prompt '{name}'."
        if len(owners) > 1:
            return (f"Error: prompt '{name}' is provided by {len(owners)} servers "
                    f"({', '.join(owners)}). Pass server= to disambiguate.")
        return self.servers[owners[0]].get_prompt(name, arguments)

    @staticmethod
    def _to_anthropic_tool(tool: dict) -> dict:
        """
        Convert an MCP tool descriptor to Anthropic-compatible format.
        MCP uses 'inputSchema' (camelCase); Anthropic uses 'input_schema' (snake_case).
        """
        result = {
            "name": tool.get("name", "?"),
            "description": tool.get("description", "") or tool.get("title", ""),
        }
        schema = tool.get("inputSchema") or tool.get("input_schema", {"type": "object"})
        result["input_schema"] = schema
        return result

    def disconnect_all(self) -> None:
        """Shut down all servers."""
        for server in self.servers.values():
            server.disconnect()
        self.servers.clear()
        self._all_tools.clear()
        self._owner.clear()


# ═══════════════════════════════════════════════════════════════════════
#  Standalone CLI helpers (used by TOMAS mcp add / list / remove)
# ═══════════════════════════════════════════════════════════════════════

def cmd_mcp_list() -> str:
    """Return a human-readable list of configured MCP servers."""
    servers = read_mcp_servers()
    if not servers:
        return "No MCP servers configured."
    lines = [f"Configured MCP servers ({len(servers)}):", ""]
    for name, cfg in servers.items():
        transport = cfg.get("type", "stdio")
        if transport == "http":
            lines.append(f"  {name}  (HTTP → {cfg.get('url', '?')})")
        else:
            cmd = cfg.get("command", "?")
            args = " ".join(cfg.get("args", []))
            lines.append(f"  {name}  (stdio → {cmd} {args})")
    return "\n".join(lines)


def cmd_mcp_add(args: list[str]) -> str:
    """
    Parse arguments and add an MCP server to the global config.
    Usage:
      TOMAS mcp add --transport http <name> <url>
      TOMAS mcp add --transport stdio <name> -- <command> [args...]
    """
    transport = "stdio"
    name = None
    url = None
    command_parts = []

    i = 0
    while i < len(args):
        if args[i] == "--transport" and i + 1 < len(args):
            transport = args[i + 1]
            i += 2
        elif args[i] == "--":
            command_parts = args[i + 1:]
            break
        elif name is None:
            name = args[i]
            i += 1
        elif transport == "http" and url is None:
            url = args[i]
            i += 1
        elif transport == "stdio":
            command_parts = args[i:]
            break
        else:
            i += 1

    if not name:
        return "Error: missing server name.\nUsage: TOMAS mcp add --transport <http|stdio> <name> [-- <command>] [args...]"

    if transport == "http":
        if not url:
            return "Error: missing URL for HTTP transport."
        cfg = {"type": "http", "url": url}
    elif transport == "stdio":
        if not command_parts:
            return "Error: missing command for stdio transport.\nUsage: TOMAS mcp add --transport stdio <name> -- <command> [args...]"
        cfg = {"type": "stdio", "command": command_parts[0], "args": command_parts[1:]}
    else:
        return f"Error: unknown transport '{transport}'. Use 'http' or 'stdio'."

    write_mcp_server(name, cfg)
    return f"✓ MCP server '{name}' added ({transport})."


def cmd_mcp_remove(name: str) -> str:
    """Remove an MCP server from the config."""
    if remove_mcp_server(name):
        return f"✓ MCP server '{name}' removed."
    return f"Error: MCP server '{name}' not found."


def cmd_mcp_env(args: list[str]) -> str:
    """
    Manage environment variables for an MCP server (e.g. auth tokens).

    Usage:
      TOMAS mcp env <server>              — list env vars
      TOMAS mcp env <server> KEY=VALUE    — set an env var
      TOMAS mcp env <server> --unset KEY  — remove an env var
    """
    if not args:
        return ("Usage:\n"
                "  TOMAS mcp env <server>\n"
                "  TOMAS mcp env <server> KEY=VALUE\n"
                "  TOMAS mcp env <server> --unset KEY")

    server_name = args[0]
    servers = read_mcp_servers()

    if server_name not in servers:
        available = [n for n in servers]
        msg = f"Error: MCP server '{server_name}' not found."
        if available:
            msg += f"\nAvailable servers: {', '.join(available)}"
        return msg

    config = servers[server_name]
    current_env = config.get("env", {})

    # ── No extra args → list current env ──
    if len(args) == 1:
        if not current_env:
            return (f"MCP server '{server_name}' has no environment variables set.\n"
                    f"To add one:\n"
                    f"  TOMAS mcp env {server_name} KEY=VALUE")
        lines = [f"Environment for '{server_name}':", ""]
        for k, v in current_env.items():
            display = v[:20] + "..." if len(v) > 24 else v
            lines.append(f"  {k} = {display}")
        return "\n".join(lines)

    action = args[1]

    # ── --unset KEY ──
    if action == "--unset":
        if len(args) < 3:
            return "Error: --unset requires a key name.\nUsage: TOMAS mcp env <server> --unset KEY"
        key = args[2]
        if key not in current_env:
            return f"Error: no env var '{key}' on server '{server_name}'."
        del current_env[key]
        if current_env:
            config["env"] = current_env
        else:
            # Remove empty env to keep config clean
            config.pop("env", None)
        write_mcp_server(server_name, config)
        return f"✓ Removed '{key}' from '{server_name}'."

    # ── KEY=VALUE ──
    if "=" in action:
        key, _, value = action.partition("=")
        if not key:
            return "Error: invalid format. Use KEY=VALUE."
        current_env[key] = value
        config["env"] = current_env
        write_mcp_server(server_name, config)
        masked = value[:4] + "***" if len(value) > 4 else "***"
        return f"✓ Set {key}={masked} on '{server_name}'."

    # ── Unknown ──
    return (f"Unknown action: {action}\n"
            f"Usage:\n"
            f"  TOMAS mcp env <server>\n"
            f"  TOMAS mcp env <server> KEY=VALUE\n"
            f"  TOMAS mcp env <server> --unset KEY")

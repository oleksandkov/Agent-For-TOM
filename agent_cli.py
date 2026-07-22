#!/usr/bin/env python3
"""
TOMAS CLI — Interactive TUI + subcommand interface for the TOMAS agent.

Usage:
    TOMAS                          Interactive TUI menu (arrow keys + Enter)
    TOMAS --run                    Launch agent directly
    TOMAS mcp list                 List configured MCP servers
    TOMAS mcp add --transport http <name> <url>
    TOMAS mcp add --transport stdio <name> -- <command> [args...]
    TOMAS mcp remove <name>        Remove an MCP server
    TOMAS mcp disable <name>       Disable an MCP server (skipped at startup)
    TOMAS mcp enable <name>        Re-enable a disabled MCP server
    TOMAS mcp env <server>          List env vars for an MCP server
    TOMAS mcp env <server> KEY=VALUE   Set an env var (e.g. auth token)
    TOMAS mcp env <server> --unset KEY  Remove an env var
    TOMAS skill list               List installed skills
    TOMAS --help                   Show this help
"""

import os
import sys
import subprocess
from pathlib import Path

# ── Force UTF-8 for stdout (handles Unicode in skills descriptions) ──
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# ── Windows msvcrt for keyboard input ──
import msvcrt

# Add project directory to path
PROJECT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_DIR))

# Load .env
from dotenv import load_dotenv
load_dotenv(PROJECT_DIR / ".env", override=True)

# Import agent modules
from agent import (
    TOOLS, RISK_LEVELS, PROJECT_DIR as AGENT_PROJECT_DIR,
    build_system_prompt, reinit_client
)


# ═══════════════════════════════════════════════════════════
#  DYNAMIC CONFIG GETTERS — always read fresh from env
# ═══════════════════════════════════════════════════════════

def get_model() -> str:
    """Read model from environment (updated after .env changes)."""
    return os.environ.get("AGENT_MODEL") or "Not set"


def is_playwright_available() -> bool:
    """Check if playwright is importable right now."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def update_dotenv(key: str, value: str):
    """Update a key in both .env file and the running os.environ."""
    env_file = PROJECT_DIR / ".env"
    content = env_file.read_text(encoding="utf-8") if env_file.exists() else ""

    # Update in file content
    lines = content.splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines), encoding="utf-8")

    # Also update the running process environment so get_model() works immediately
    os.environ[key] = value


# ═══════════════════════════════════════════════════════════
#  KEYBOARD INPUT
# ═══════════════════════════════════════════════════════════

def get_key():
    """Read a single keypress. Returns key name or character."""
    key = msvcrt.getch()
    if key == b'\xe0':  # Arrow / function keys
        key = msvcrt.getch()
        mapping = {
            b'H': 'UP', b'P': 'DOWN',
            b'K': 'LEFT', b'M': 'RIGHT',
            b'G': 'HOME', b'O': 'END',
            b'I': 'PGUP', b'Q': 'PGDN',
        }
        return mapping.get(key, f'FUNC({key[0]})')
    elif key == b'\r':
        return 'ENTER'
    elif key == b'\x1b':
        return 'ESC'
    elif key == b'\x03':
        return 'CTRL_C'
    elif key == b'\x00':  # Some function keys
        msvcrt.getch()  # consume second byte
        return 'FUNC'
    else:
        try:
            return key.decode('utf-8')
        except UnicodeDecodeError:
            return '?'


# ═══════════════════════════════════════════════════════════
#  ANSI UTILITIES
# ═══════════════════════════════════════════════════════════

CLEAR_LINE = '\033[2K'
CURSOR_UP = '\033[A'
HIDE_CURSOR = '\033[?25l'
SHOW_CURSOR = '\033[?25h'
RESET = '\033[0m'
BOLD = '\033[1m'
DIM = '\033[2m'
GREEN = '\033[92m'
CYAN = '\033[96m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
MAGENTA = '\033[95m'
RED = '\033[91m'
GRAY = '\033[90m'
REVERSE = '\033[7m'
BOLD_OFF = '\033[22m'


def clear_screen():
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


# ═══════════════════════════════════════════════════════════
#  GENERIC ARROW-KEY MENU
# ═══════════════════════════════════════════════════════════

def arrow_menu(title: str, items: list, header_lines: list = None,
               footer: str = None) -> int:
    """
    Show an arrow-key navigable menu.
    Returns the index of the selected item, or -1 if cancelled.
    """
    n = len(items)
    selected = 0
    prev_selected = -1  # force redraw

    def draw():
        nonlocal prev_selected
        # Redraw only the items (assumes they're at fixed position)
        for i in range(n):
            sys.stdout.write(CURSOR_UP + CLEAR_LINE)
        # Now write over them
        for i in range(n):
            prefix = f'{GREEN}▶{RESET} ' if i == selected else '  '
            label = f'{BOLD}{items[i]}{RESET}' if i == selected else items[i]
            sys.stdout.write(f'{CLEAR_LINE}{prefix}{label}\n')
        if footer:
            sys.stdout.write(CLEAR_LINE + DIM + footer + RESET + '\n')
        sys.stdout.flush()
        prev_selected = selected

    # ── First draw ──
    clear_screen()
    # Header
    if header_lines:
        for line in header_lines:
            print(line)
    else:
        print(f'{BOLD}{title}{RESET}')
        print('─' * 50)
    # Draw items
    for i in range(n):
        prefix = f'{GREEN}▶{RESET} ' if i == selected else '  '
        label = f'{BOLD}{items[i]}{RESET}' if i == selected else items[i]
        sys.stdout.write(f'{CLEAR_LINE}{prefix}{label}\n')
    if footer:
        sys.stdout.write(CLEAR_LINE + DIM + footer + RESET + '\n')
    sys.stdout.flush()
    prev_selected = selected

    # ── Event loop ──
    while True:
        key = get_key()
        if key == 'UP':
            selected = (selected - 1) % n
            min_l = min(prev_selected, selected)
            up_count = n - min_l
            for _ in range(up_count + (1 if footer else 0)):
                sys.stdout.write(CURSOR_UP + CLEAR_LINE)
            for i in range(min_l, n):
                prefix = f'{GREEN}▶{RESET} ' if i == selected else '  '
                label = f'{BOLD}{items[i]}{RESET}' if i == selected else items[i]
                sys.stdout.write(f'{CLEAR_LINE}{prefix}{label}\n')
            if footer:
                sys.stdout.write(CLEAR_LINE + DIM + footer + RESET + '\n')
            sys.stdout.flush()
            prev_selected = selected

        elif key == 'DOWN':
            selected = (selected + 1) % n
            min_l = min(prev_selected, selected)
            up_count = n - min_l
            for _ in range(up_count + (1 if footer else 0)):
                sys.stdout.write(CURSOR_UP + CLEAR_LINE)
            for i in range(min_l, n):
                prefix = f'{GREEN}▶{RESET} ' if i == selected else '  '
                label = f'{BOLD}{items[i]}{RESET}' if i == selected else items[i]
                sys.stdout.write(f'{CLEAR_LINE}{prefix}{label}\n')
            if footer:
                sys.stdout.write(CLEAR_LINE + DIM + footer + RESET + '\n')
            sys.stdout.flush()
            prev_selected = selected

        elif key in ('ENTER',):
            return selected

        elif key in ('ESC', 'q', 'CTRL_C'):
            return -1


def confirm_menu(title: str, items: list, header_lines: list = None,
                 footer: str = None) -> int:
    """Alias for arrow_menu — returns index or -1."""
    return arrow_menu(title, items, header_lines, footer)


# ═══════════════════════════════════════════════════════════
#  INFO DISPLAY (paged / press-any-key)
# ═══════════════════════════════════════════════════════════

def show_info_page(title: str, lines: list, prompt: str = "Press any key to go back"):
    """Display a page of info and wait for a keypress."""
    clear_screen()
    print(f'{BOLD}{title}{RESET}')
    print('─' * 50)
    for line in lines:
        print(line)
    print()
    sys.stdout.write(DIM + prompt + '...' + RESET)
    sys.stdout.flush()
    msvcrt.getch()  # wait for any key


def prompt_text(prompt: str, default: str = None) -> str:
    """Prompt for text input (uses standard input())."""
    if default:
        val = input(f'{prompt} [{default}]: ').strip()
        return val if val else default
    else:
        return input(f'{prompt}: ').strip()


# ═══════════════════════════════════════════════════════════
#  ENV HELPERS
# ═══════════════════════════════════════════════════════════

def get_env_value(key: str, default: str = "Not set") -> str:
    return os.environ.get(key, default)


# ═══════════════════════════════════════════════════════════
#  MULTI-PROVIDER CONFIG — persists to providers.json
# ═══════════════════════════════════════════════════════════

PROVIDERS_CONFIG_PATH = PROJECT_DIR / "providers.json"


def _load_providers_config() -> dict:
    """Load the multi-provider config file."""
    try:
        if PROVIDERS_CONFIG_PATH.exists():
            import json
            return json.loads(PROVIDERS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"active": None, "providers": {}}


def _save_providers_config(config: dict):
    """Save the multi-provider config file."""
    import json
    PROVIDERS_CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def _get_configured_providers(config: dict = None) -> list[str]:
    """Return list of provider display names that are configured."""
    if config is None:
        config = _load_providers_config()
    return list(config.get("providers", {}).keys())


def _get_active_provider_name(config: dict = None) -> str | None:
    """Return the name of the currently active provider."""
    if config is None:
        config = _load_providers_config()
    return config.get("active")


def _activate_provider(name: str) -> bool:
    """Switch to a configured provider by name. Returns True on success."""
    config = _load_providers_config()
    providers = config.get("providers", {})
    if name not in providers:
        return False
    provider = providers[name]

    # Zen provider — needs proxy
    if provider.get("type") == "zen":
        from zen_proxy import check_status, start_proxy
        if not check_status(6446):
            start_proxy(6446, daemon=True)
        update_dotenv("ANTHROPIC_API_KEY", "zen-proxy-key")
        update_dotenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:6446")
        update_dotenv("ANTHROPIC_EXTRA_HEADERS", "")
        if provider.get("model"):
            update_dotenv("AGENT_MODEL", provider["model"])
    else:
        # Regular provider — restore saved env vars
        env = provider.get("env", {})
        for key, value in env.items():
            update_dotenv(key, value)
        if provider.get("model"):
            update_dotenv("AGENT_MODEL", provider["model"])

    # Mark as active
    config["active"] = name
    _save_providers_config(config)

    # Reinitialize the anthropic client
    reinit_client()
    return True


def _save_provider_config(name: str, env_vars: dict, model: str = "",
                          provider_type: str = None):
    """Save or update a single provider's configuration."""
    config = _load_providers_config()
    if "providers" not in config:
        config["providers"] = {}

    entry = config["providers"].get(name, {})
    entry["env"] = env_vars
    if model:
        entry["model"] = model
    if provider_type:
        entry["type"] = provider_type
    config["providers"][name] = entry

    # Automatically activate this provider
    config["active"] = name
    _save_providers_config(config)


def _choose_provider_to_switch() -> bool:
    """Show a sub-menu of configured providers to switch to.
    Returns True if a switch was made, False if cancelled."""
    config = _load_providers_config()
    providers = _get_configured_providers(config)
    if not providers:
        show_info_page('No Providers',
                       ['  No providers are configured yet.',
                        '',
                        '  Go to "Connect / configure provider" first.'])
        return False

    active = config.get("active")
    display = []
    for p in providers:
        mark = f'{GREEN}◈{RESET} ' if p == active else '  '
        display.append(f'{mark}{p}')
    display.append('')
    display.append(f'  {RED}✕{RESET}  Cancel')

    idx = arrow_menu('Switch Active Provider', display,
                     footer='↑↓ navigate · Enter select · Esc cancel')
    if idx < 0 or idx >= len(providers):
        return False

    name = providers[idx]
    if name == active:
        show_info_page('Already Active', [f'  {name} is already the active provider.'])
        return True

    if _activate_provider(name):
        show_info_page('Provider Switched',
                       [f'  ✓ Switched to {name}',
                        f'  Model: {get_env_value("AGENT_MODEL")}'])
        return True
    else:
        show_info_page('Error', [f'  ✗ Failed to switch to {name}'])
        return False


# ═══════════════════════════════════════════════════════════
#  MENU PAGES
# ═══════════════════════════════════════════════════════════

def page_model():
    """Show current model info."""
    lines = [
        f'  AGENT_MODEL:       {get_env_value("AGENT_MODEL")}',
        f'  ANTHROPIC_BASE_URL: {get_env_value("ANTHROPIC_BASE_URL")}',
        f'  API Key:           ***{get_env_value("ANTHROPIC_API_KEY")[-4:]}',
        '',
    ]
    # Check if Zen proxy is active
    try:
        from zen_proxy import check_status
        if check_status():
            lines.append(f'  {GREEN}◈{RESET} OpenCode Zen proxy: {GREEN}running{RESET}')
            lines.append(f'  {DIM}  Available models:{RESET}')
            from zen_proxy import ZEN_MODELS, MODEL_CONTEXT_WINDOWS
            for m in ZEN_MODELS:
                cw = MODEL_CONTEXT_WINDOWS.get(m, '?')
                cw_str = f'{cw:,}' if isinstance(cw, int) else str(cw)
                lines.append(f'  {DIM}    • {m} ({cw_str} ctx){RESET}')
        else:
            lines.append(f'  OpenCode Zen proxy: {DIM}not running{RESET}')
    except Exception:
        pass
    show_info_page('Current LLM Model', lines)


def page_providers():
    """Show connected providers."""
    base_url = get_env_value('ANTHROPIC_BASE_URL')
    api_key = get_env_value('ANTHROPIC_API_KEY')
    lines = []
    if base_url != "Not set" and api_key != "Not set":
        lines.append(f'  ✓ Active: {base_url}')
        lines.append(f'    Key: ***{api_key[-4:]}')
    else:
        lines.append('  ✗ No active provider')
    lines.append('')

    # Show configured providers from multi-provider config
    config = _load_providers_config()
    providers = _get_configured_providers(config)
    active = config.get("active")
    if providers:
        lines.append(f'  Saved providers ({len(providers)}):')
        for p in providers:
            mark = f'{GREEN}◈{RESET}' if p == active else ' '
            lines.append(f'    {mark} {p}')
    else:
        lines.append('  (No saved provider configurations)')

    lines.append('')
    lines.append('  Env-detected providers:')
    for var in ['OPENAI_API_KEY', 'OPENAI_BASE_URL', 'GOOGLE_API_KEY', 'GROQ_API_KEY']:
        val = get_env_value(var)
        status = '✓' if val != "Not set" else '✗'
        lines.append(f'    {status} {var}')
    # OpenCode Zen status
    try:
        from zen_proxy import check_status
        zen_running = check_status()
    except Exception:
        zen_running = False
    zen_icon = '✓' if zen_running else '✗'
    zen_model = get_env_value('AGENT_MODEL')
    lines.append(f'    {zen_icon} OpenCode Zen proxy{" (" + zen_model + ")" if zen_running else ""}')
    show_info_page('Connected Providers', lines)


def page_tools():
    """Show available tools."""
    lines = [f'  Total tools: {len(TOOLS)}', '']
    for tool in TOOLS:
        risk = RISK_LEVELS.get(tool['name'], 'unknown')
        icons = {'low': '🟢', 'medium': '🟡', 'high': '🔴'}
        icon = icons.get(risk, '⚪')
        lines.append(f'  {icon} {tool["name"]} ({risk})')
        lines.append(f'     {tool["description"]}')
        props = tool['input_schema'].get('properties', {})
        if props:
            lines.append(f'     Params: {", ".join(props.keys())}')
        lines.append('')
    show_info_page('Available Tools', lines)


def page_mcps():
    """Interactive MCP server management page with real connection status."""
    from mcp_manager import (
        read_mcp_servers, write_mcp_server, remove_mcp_server,
        is_server_disabled, set_server_disabled,
        get_server_env, cmd_mcp_env, test_mcp_connections,
    )

    # Cache for connection test results: {name: {"connected": bool, "error": str, "disabled": bool, "tool_count": int}}
    test_results = test_mcp_connections()

    while True:
        servers = read_mcp_servers()
        items = []
        server_names = []

        # ── Row 0: Refresh button ──
        if servers:
            n_ok = sum(1 for r in test_results.values() if r.get("connected"))
            n_fail = sum(1 for r in test_results.values() if not r.get("connected") and not r.get("disabled"))
            n_disabled = sum(1 for r in test_results.values() if r.get("disabled"))
            status_parts = []
            if n_ok: status_parts.append(f'{GREEN}{n_ok} OK{RESET}')
            if n_fail: status_parts.append(f'{RED}{n_fail} fail{RESET}')
            if n_disabled: status_parts.append(f'{DIM}{n_disabled} disabled{RESET}')
            status_str = ', '.join(status_parts)
            items.append(f'  ⟳ Refresh connections  ({status_str})')
            # NOTE: do NOT add to server_names for the test row

        # ── Blank separator ──
        blank_idx = len(items)
        items.append('')

        # ── Server rows ──
        if not servers:
            items.append('  (no MCP servers configured)')
            server_names.append(None)
        else:
            for name in sorted(servers.keys()):
                cfg = servers[name]
                transport = cfg.get("type", "?")
                target = cfg.get("url") or cfg.get("command", "?")
                disabled = is_server_disabled(name)

                # Determine status icon from test results
                if test_results and name in test_results:
                    r = test_results[name]
                    if r.get("disabled"):
                        icon = '✗'
                        status_tag = f' {DIM}[DISABLED]{RESET}'
                    elif r.get("connected"):
                        icon = '✓'
                        status_tag = f' {GREEN}[{r["tool_count"]} tools]{RESET}'
                    else:
                        icon = '⚠'
                        err = r.get("error") or "unknown error"
                        if len(err) > 42:
                            err = err[:39] + "..."
                        status_tag = f' {RED}[FAIL]{RESET} {DIM}{err}{RESET}'
                elif disabled:
                    icon = '✗'
                    status_tag = f' {DIM}[DISABLED]{RESET}'
                else:
                    icon = '?'
                    status_tag = f' {DIM}[untested]{RESET}'

                env_count = len(cfg.get("env", {}))
                env_tag = f' {BLUE}[{env_count} env]{RESET}' if env_count else ''
                label = f'  {icon} {name} — {transport} → {target}{status_tag}{env_tag}'
                items.append(label)
                server_names.append(name)

        # ── Bottom actions ──
        sep_actions = len(items)
        items.append('')
        items.append('  ── Add new MCP server ──')
        items.append('  ← Back to main menu')

        header = [
            f'  {BOLD}MCP Server Management{RESET}',
            f'  {DIM}Select a server to manage — connections auto-tested{RESET}',
            '─' * 50,
        ]

        idx = arrow_menu('', items, header_lines=header,
                         footer='↑↓ navigate · Enter select · Esc back')

        if idx < 0 or idx == len(items) - 1:  # Back or Esc
            return

        # ── Test connections ──
        if servers and idx == 0:
            clear_screen()
            print(f'{BOLD}Testing MCP connections...{RESET}')
            print('─' * 50)
            print('Connecting to each server (this may take a moment)...')
            print()
            import sys as _sys
            _sys.stdout.flush()
            test_results = test_mcp_connections()
            # Show results
            lines = ['  Results:', '']
            for name in sorted(test_results.keys()):
                r = test_results[name]
                if r.get("disabled"):
                    lines.append(f'  ✗ {name} — disabled')
                elif r.get("connected"):
                    lines.append(f'  ✓ {name} — {r["tool_count"]} tools')
                else:
                    err = r.get("error") or "unknown error"
                    lines.append(f'  ⚠ {name} — {err}')
            lines.append('')
            lines.append('  Press any key to return to server list')
            show_info_page('Connection Test', lines)
            continue

        # ── Add new server ──
        add_idx = (blank_idx + 1) if not servers else sep_actions + 1
        if idx == add_idx:
            _mcp_add_server_interactive()
            continue

        # ── Select a server to manage ──
        # When servers exist: idx 0=test, idx 1=blank, idx 2+=servers → server_idx = idx - 2
        # When no servers: no test row → only 1 blank before "no servers" msg → server_idx = idx - 1
        server_idx = idx - (2 if servers else 1)
        if server_idx < 0 or server_idx >= len(server_names):
            continue
        name = server_names[server_idx]
        if name is None:
            continue
        cfg = servers.get(name)
        if cfg is None:
            continue
        _mcp_server_menu(name, cfg)


def _mcp_server_menu(name: str, cfg: dict):
    """Show management options for a single MCP server."""
    from mcp_manager import remove_mcp_server, set_server_disabled, is_server_disabled

    while True:
        transport = cfg.get("type", "?")
        target = cfg.get("url") or cfg.get("command", "?")
        disabled = is_server_disabled(name)
        status = f'{DIM}DISABLED{RESET}' if disabled else f'{GREEN}ENABLED{RESET}'
        env_count = len(cfg.get("env", {}))

        items = []
        if disabled:
            items.append('  Enable server')
        else:
            items.append('  Disable server')
        items.append(f'  Auth / env vars ({env_count})')
        items.append('  Remove server')
        items.append('  View config')
        items.append('')
        items.append('  ← Back to server list')

        header = [
            f'  {BOLD}Manage: {name}{RESET}',
            f'  {DIM}{transport} → {target}  |  Status: {status}  |  Env vars: {env_count}{RESET}',
            '─' * 50,
        ]

        idx = arrow_menu('', items, header_lines=header,
                         footer='↑↓ navigate · Enter select · Esc back')

        if idx < 0 or idx == len(items) - 1:  # Back
            return

        if idx == 0:  # Toggle enable/disable
            new_disabled = not disabled
            set_server_disabled(name, new_disabled)
            show_info_page(
                'Done',
                [f'  ✓ Server "{name}" {"disabled" if new_disabled else "enabled"}.'],
            )
            # Refresh cfg
            from mcp_manager import read_mcp_servers
            cfg.update(read_mcp_servers().get(name, {}))
            continue

        elif idx == 1:  # Auth / env vars
            _mcp_server_auth(name)
            # Refresh cfg
            from mcp_manager import read_mcp_servers
            cfg.update(read_mcp_servers().get(name, {}))
            continue

        elif idx == 2:  # Remove server
            header_confirm = [
                f'  {BOLD}Remove MCP Server{RESET}',
                '─' * 50,
                f'  Are you sure you want to remove "{name}"?',
                '',
            ]
            confirm_items = ['  Yes, remove it', '  No, cancel']
            cidx = arrow_menu('', confirm_items, header_lines=header_confirm,
                              footer='Enter select · Esc cancel')
            if cidx == 0:  # Yes
                remove_mcp_server(name)
                show_info_page('Done', [f'  ✓ Server "{name}" removed.'])
                return  # Go back to server list
            continue

        elif idx == 3:  # View config
            lines = [
                f'  Name:     {name}',
                f'  Type:     {cfg.get("type", "?")}',
                f'  URL:      {cfg.get("url", "—")}',
                f'  Command:  {cfg.get("command", "—")}',
                f'  Args:     {cfg.get("args", [])}',
                f'  Disabled: {cfg.get("disabled", False)}',
                f'  Env vars: {len(cfg.get("env", {}))}',
            ]
            env = cfg.get("env", {})
            if env:
                lines.append('')
                for k, v in env.items():
                    masked = v[:8] + '***' if len(v) > 10 else v
                    lines.append(f'    {k} = {masked}')
            show_info_page(f'Config: {name}', lines)
            continue


def _mcp_server_auth(name: str):
    """Manage env vars for a server (interactive)."""
    from mcp_manager import get_server_env, write_mcp_server, read_mcp_servers

    while True:
        servers = read_mcp_servers()
        cfg = servers.get(name, {})
        env = cfg.get("env", {})

        items = []
        if env:
            for k in sorted(env.keys()):
                masked = env[k][:4] + '***' if len(env[k]) > 6 else '***'
                items.append(f'  {k} = {masked}')
            items.append('')
            items.append('  ── Add / update env var ──')
            items.append('  Remove an env var')
        else:
            items.append('  (no env vars set)')
            items.append('')
            items.append('  ── Add env var ──')

        items.append('')
        items.append('  ← Back')

        header = [
            f'  {BOLD}Auth / Env Vars: {name}{RESET}',
            f'  {DIM}Set API keys, tokens, or other env vars the MCP server needs{RESET}',
            '─' * 50,
        ]

        idx = arrow_menu('', items, header_lines=header,
                         footer='↑↓ navigate · Enter select · Esc back')

        if idx < 0 or idx == len(items) - 1:  # Back
            return

        # Check if user selected an existing env var → offer to change or delete
        env_keys = sorted(env.keys())
        if env and idx < len(env_keys):
            key = env_keys[idx]
            actions = [
                '  Update value',
                '  Delete this var',
                '',
                '  ← Back',
            ]
            header2 = [
                f'  {BOLD}Env var: {key}{RESET}',
                f'  {DIM}Current value: {env[key][:20]}{"..." if len(env[key]) > 20 else ""}{RESET}',
                '─' * 50,
            ]
            aidx = arrow_menu('', actions, header_lines=header2,
                              footer='Enter select · Esc back')
            if aidx == 0:  # Update
                val = prompt_text(f'Enter new value for {key}')
                if val:
                    env[key] = val
                    cfg["env"] = env
                    write_mcp_server(name, cfg)
                    show_info_page('Done', [f'  ✓ {key} updated.'])
            elif aidx == 1:  # Delete
                del env[key]
                if env:
                    cfg["env"] = env
                else:
                    cfg.pop("env", None)
                write_mcp_server(name, cfg)
                show_info_page('Done', [f'  ✓ {key} removed.'])
            continue

        # "Add / update env var" action
        add_idx = 1 if env else 2  # After (no env vars) + blank line
        if idx == add_idx:
            key = prompt_text('Enter env var name (e.g. API_KEY)')
            if not key:
                continue
            val = prompt_text(f'Enter value for {key}')
            if val:
                env[key] = val
                cfg["env"] = env
                write_mcp_server(name, cfg)
                show_info_page('Done', [f'  ✓ {key} set.'])
            continue

        # "Remove an env var" action
        if env and idx == add_idx + 2:  # After "── Add / update env var ──" and "Remove ..."
            if not env:
                continue
            keys = list(env.keys())
            remove_items = [f'  {k}' for k in keys] + ['', '  ← Back']
            remove_header = [
                f'  {BOLD}Remove env var from {name}{RESET}',
                '─' * 50,
            ]
            ridx = arrow_menu('', remove_items, header_lines=remove_header,
                              footer='↑↓ navigate · Enter select · Esc back')
            if 0 <= ridx < len(keys):
                key = keys[ridx]
                del env[key]
                if env:
                    cfg["env"] = env
                else:
                    cfg.pop("env", None)
                write_mcp_server(name, cfg)
                show_info_page('Done', [f'  ✓ {key} removed.'])
            continue


def _mcp_add_server_interactive():
    """Interactive wizard to add a new MCP server."""
    from mcp_manager import write_mcp_server

    # Step 1: choose transport
    transport_items = ['  HTTP (URL-based)', '  STDIO (command-based)', '', '  Cancel']
    transport_header = [
        f'  {BOLD}Add MCP Server — Transport{RESET}',
        '─' * 50,
        '  Choose how this MCP server communicates:',
        '',
    ]
    tidx = arrow_menu('', transport_items, header_lines=transport_header,
                      footer='↑↓ navigate · Enter select · Esc cancel')
    if tidx < 0 or tidx >= 2:
        return
    is_http = (tidx == 0)

    # Step 2: name
    name = prompt_text('Server name (e.g. my-tool)')
    if not name:
        return

    # Step 3: transport-specific details
    if is_http:
        url = prompt_text('URL (e.g. https://api.example.com/mcp)')
        if not url:
            return
        cfg = {"type": "http", "url": url}
    else:
        command = prompt_text('Command (e.g. npx, python, node)')
        if not command:
            return
        args_str = prompt_text('Arguments (space-separated, leave empty if none)', '')
        args = args_str.split() if args_str else []
        cfg = {"type": "stdio", "command": command}
        if args:
            cfg["args"] = args

    write_mcp_server(name, cfg)
    show_info_page('Done', [
        f'  ✓ MCP server "{name}" added.',
        '',
        '  Now you can:',
        f'    • TOMAS mcp env {name} KEY=VALUE   — set auth tokens',
        f'    • TOMAS mcp disable {name}          — disable if not needed',
    ])


def page_skills():
    """Show installed skills with enhanced formatting."""
    from skills_manager import discover_skills, find_skill_dirs

    all_skills = discover_skills()
    dirs = find_skill_dirs()
    if not all_skills:
        show_info_page('Installed Skills', ['  No skills installed.'])
        return

    # Group skills by their source directory
    by_dir: dict[str, list] = {}
    dir_short: dict[str, str] = {}
    for d in dirs:
        d_str = str(d)
        parts = d_str.replace(".agents", "~/.agents").replace(".claude", "~/.claude").split("\\")
        short = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        dir_short[d_str] = short
        by_dir[d_str] = []

    for s in all_skills:
        matched = False
        for d in dirs:
            d_str = str(d)
            if str(s["file"]).startswith(d_str):
                by_dir[d_str].append(s)
                matched = True
                break
        if not matched and dirs:
            by_dir[str(dirs[0])].append(s)

    lines = []
    lines.append(f'  Skills: {len(all_skills)} total')
    lines.append('')

    for d in dirs:
        d_str = str(d)
        group = by_dir.get(d_str, [])
        if not group:
            continue
        short = dir_short.get(d_str, d_str)
        lines.append(f'  {CYAN}{BOLD}◉ {short}{RESET}  ({len(group)} skills)')
        lines.append('')

        max_name = min(max(len(s["name"]) for s in group) + 2, 40)
        max_name = max(max_name, 30)

        for s in sorted(group, key=lambda x: x["name"]):
            name = s["name"]
            desc = s["description"] or ""
            desc_max = max(50, 80 - max_name - 4)
            if len(desc) > desc_max:
                desc = desc[:desc_max - 3] + "..."
            pad = " " * (max_name - len(name))
            lines.append(f'    {GREEN}{BOLD}{name}{RESET}{pad}{DIM}{desc}{RESET}')
        lines.append('')

    show_info_page('Installed Skills', lines)


def page_edit_instructions():
    """View/edit AGENT_INSTRUCTIONS.md."""
    inst_file = AGENT_PROJECT_DIR / "AGENT_INSTRUCTIONS.md"
    clear_screen()
    print(f'{BOLD}Agent Instructions (AGENT_INSTRUCTIONS.md){RESET}')
    print('─' * 50)
    if inst_file.exists():
        print(inst_file.read_text(encoding="utf-8"))
    else:
        print('  (file does not exist yet)')
    print()
    sys.stdout.write(DIM + 'Press any key to go back, or [e] to edit in Notepad...' + RESET)
    sys.stdout.flush()
    key = msvcrt.getch()
    if key in (b'e', b'E'):
        os.startfile(inst_file) if inst_file.exists() else None


def page_edit_claude():
    """View/edit CLAUDE.md."""
    claude_file = AGENT_PROJECT_DIR / "CLAUDE.md"
    clear_screen()
    print(f'{BOLD}Project Guidelines (CLAUDE.md){RESET}')
    print('─' * 50)
    if claude_file.exists():
        print(claude_file.read_text(encoding="utf-8"))
    else:
        print('  (file does not exist yet)')
    print()
    sys.stdout.write(DIM + 'Press any key to go back, or [e] to edit in Notepad...' + RESET)
    sys.stdout.flush()
    key = msvcrt.getch()
    if key in (b'e', b'E'):
        os.startfile(claude_file) if claude_file.exists() else None


def page_configure_provider():
    """Arrow-key menu to select and configure a provider."""
    configured = set(_get_configured_providers())

    provider_names = [
        'OpenRouter (openrouter.ai)',
        'Anthropic Direct (api.anthropic.com)',
        'OpenAI (api.openai.com)',
        'Google AI',
        'OpenCode Zen (opencode.ai)  ◈',
        'Custom / Other',
    ]
    # Show ✓ for already-configured providers
    display = []
    for p in provider_names:
        if p in configured:
            display.append(f'{GREEN}✓{RESET} {p}')
        else:
            display.append(f'  {p}')

    idx = arrow_menu('Connect / Configure Provider', display,
                     footer='↑↓ navigate · Enter select · Esc cancel')
    if idx < 0:
        return

    if idx == 0:  # OpenRouter
        key = prompt_text('Enter OpenRouter API key (sk-or-...)')
        if key:
            update_dotenv("ANTHROPIC_API_KEY", key)
            update_dotenv("ANTHROPIC_BASE_URL", "https://openrouter.ai/api")
            update_dotenv("ANTHROPIC_EXTRA_HEADERS", "")
            _save_provider_config(
                provider_names[0],
                {"ANTHROPIC_API_KEY": key,
                 "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
                 "ANTHROPIC_EXTRA_HEADERS": ""},
                provider_type="openrouter"
            )
            reinit_client()
            show_info_page('Done', ['  ✓ OpenRouter configured and active.'])
    elif idx == 1:  # Anthropic Direct
        key = prompt_text('Enter Anthropic API key (sk-ant-...)')
        if key:
            update_dotenv("ANTHROPIC_API_KEY", key)
            update_dotenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
            update_dotenv("ANTHROPIC_EXTRA_HEADERS", "")
            _save_provider_config(
                provider_names[1],
                {"ANTHROPIC_API_KEY": key,
                 "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
                 "ANTHROPIC_EXTRA_HEADERS": ""},
                provider_type="anthropic"
            )
            reinit_client()
            show_info_page('Done', ['  ✓ Anthropic Direct configured and active.'])
    elif idx == 2:  # OpenAI
        key = prompt_text('Enter OpenAI API key')
        base = prompt_text('Enter base URL', 'https://api.openai.com/v1')
        if key:
            update_dotenv("OPENAI_API_KEY", key)
            update_dotenv("OPENAI_BASE_URL", base)
            update_dotenv("ANTHROPIC_EXTRA_HEADERS", "")
            _save_provider_config(
                provider_names[2],
                {"OPENAI_API_KEY": key,
                 "OPENAI_BASE_URL": base,
                 "ANTHROPIC_EXTRA_HEADERS": ""},
                provider_type="openai"
            )
            show_info_page('Done', ['  ✓ OpenAI configured.',
                                    '',
                                    f'  {YELLOW}Note: OpenAI is saved but the agent uses',
                                    '  the ANTHROPIC_* env vars for API calls.',
                                    '  Use "Switch provider" to activate it.'])
    elif idx == 3:  # Google AI
        key = prompt_text('Enter Google AI API key')
        if key:
            update_dotenv("GOOGLE_API_KEY", key)
            _save_provider_config(
                provider_names[3],
                {"GOOGLE_API_KEY": key},
                provider_type="google"
            )
            show_info_page('Done', ['  ✓ Google AI configured.',
                                    '',
                                    f'  {YELLOW}Note: Google AI is saved but the agent uses',
                                    '  the ANTHROPIC_* env vars for API calls.',
                                    '  Use "Switch provider" to activate it.'])
    elif idx == 4:  # OpenCode Zen — auto-start proxy, no config needed
        _zen_setup_proxy()
        # Also save to multi-provider config
        from zen_proxy import ZEN_MODELS
        config = _load_providers_config()
        if "providers" not in config:
            config["providers"] = {}
        config["providers"][provider_names[4]] = {
            "type": "zen",
            "model": os.environ.get("AGENT_MODEL", ZEN_MODELS[0])
        }
        config["active"] = provider_names[4]
        _save_providers_config(config)
    elif idx == 5:  # Custom
        name = prompt_text('Provider name')
        key = prompt_text('API key')
        base = prompt_text('Base URL')
        if key and base and name:
            env_key = f"{name.upper().replace(' ', '_')}_API_KEY"
            env_url = f"{name.upper().replace(' ', '_')}_BASE_URL"
            update_dotenv(env_key, key)
            update_dotenv(env_url, base)
            _save_provider_config(
                name,
                {env_key: key, env_url: base},
                provider_type="custom"
            )
            show_info_page('Done', [f'  ✓ {name} configured and active.'])


def page_configure_zen():
    """Sub-menu for OpenCode Zen configuration."""
    opts = [
        '  🚀  Start Zen proxy (local, built-in)',
        '  🔑  Manual — Direct Zen API',
        '  ℹ️   About OpenCode Zen',
        '  ◀   Back to providers',
    ]
    idx = arrow_menu('OpenCode Zen Configuration', opts,
                     footer='↑↓ navigate · Enter select · Esc back')
    if idx < 0 or idx == 3:
        return

    if idx == 0:  # Start proxy
        _zen_setup_proxy()
    elif idx == 1:  # Direct API
        _zen_setup_direct()
    elif idx == 2:  # About
        _zen_show_info()


def _zen_setup_proxy():
    """Start the built-in Zen proxy and configure TOMAS to use it."""
    from zen_proxy import check_status, start_proxy, ZEN_MODELS, MODEL_CONTEXT_WINDOWS
    port = 6446

    if check_status(port):
        # Already running — just configure
        update_dotenv("ANTHROPIC_API_KEY", "oc-zen-proxy")
        update_dotenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{port}")
        update_dotenv("ANTHROPIC_EXTRA_HEADERS", "")
        update_dotenv("AGENT_MODEL", ZEN_MODELS[0])
        reinit_client()
        show_info_page('Zen Proxy Ready', [
            '  ✓ Zen proxy already running',
            f'  ✓ Connected: http://127.0.0.1:{port}',
            f'  ✓ Model: {ZEN_MODELS[0]} ({MODEL_CONTEXT_WINDOWS.get(ZEN_MODELS[0], "?"):,} ctx)',
            '',
            '  Tip: Use Choose Model menu to pick a different model.',
        ])
        return

    print(f'  {DIM}Starting Zen proxy on port {port}...{RESET}')
    try:
        start_proxy(port, daemon=True)
        # Give it a moment to start
        import time
        time.sleep(0.5)

        if check_status(port):
            update_dotenv("ANTHROPIC_API_KEY", "oc-zen-proxy")
            update_dotenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{port}")
            update_dotenv("ANTHROPIC_EXTRA_HEADERS", "")
            update_dotenv("AGENT_MODEL", ZEN_MODELS[0])
            reinit_client()
            show_info_page('Zen Proxy Started', [
                '  ✓ Zen proxy is running locally',
                f'  ✓ Endpoint: http://127.0.0.1:{port}',
                f'  ✓ Default model: {ZEN_MODELS[0]}',
                '',
                '  Available models:',
                *(f'    • {m} ({MODEL_CONTEXT_WINDOWS.get(m, "?"):,} ctx)' for m in ZEN_MODELS),
                '',
                f'  {YELLOW}Note: Free-tier models may have rate limits.{RESET}',
            ])
        else:
            show_info_page('⚠ Zen Proxy Issue', [
                '  Proxy failed to start. Try:',
                '   1. Check port 6446 is available',
                '   2. Run manually: python zen_proxy.py',
            ])
    except Exception as e:
        show_info_page('⚠ Error', [f'  {RED}{e}{RESET}'])


def _zen_setup_direct():
    """Configure direct Zen API access without local proxy."""
    from zen_proxy import ZEN_MODELS, MODEL_CONTEXT_WINDOWS
    key = prompt_text('Enter Zen API key (or leave blank for free tier)', 'public')
    if not key:
        key = 'public'
    update_dotenv("ANTHROPIC_API_KEY", key)
    # For direct API, point to opencode.ai with extra headers
    update_dotenv("ANTHROPIC_BASE_URL", "https://opencode.ai/zen/v1")
    # The proxy is recommended because Zen requires dynamic x-opencode-* headers.
    # Direct access may not work without the proxy.
    update_dotenv("AGENT_MODEL", ZEN_MODELS[0])
    reinit_client()
    show_info_page('Zen Direct Configured', [
        '  ✓ API key set',
        '  ✓ Base URL: https://opencode.ai/zen/v1',
        f'  ✓ Default model: {ZEN_MODELS[0]} ({MODEL_CONTEXT_WINDOWS.get(ZEN_MODELS[0], "?"):,} ctx)',
        '',
        f'  {YELLOW}⚠ Note: Direct Zen API requires custom headers.{RESET}',
        f'  {YELLOW}  For reliable access, use "Start Zen proxy" instead.{RESET}',
    ])


def _zen_show_info():
    """Show information about OpenCode Zen."""
    from zen_proxy import ZEN_MODELS, MODEL_CONTEXT_WINDOWS, check_status
    port = 6446
    running = check_status(port)
    r_icon = f'{GREEN}✓{RESET}' if running else f'{RED}✗{RESET}'
    lines = [
        '',
        f'  {BOLD}OpenCode Zen{RESET} — pay-as-you-go AI gateway',
        f'  {DIM}─────────────────────────────────────{RESET}',
        '  A curated API gateway from the OpenCode team.',
        '  Pay per token, no subscription needed.',
        '',
        f'  {BOLD}Proxy status:{RESET}',
        f'    {r_icon} on http://127.0.0.1:{port}',
        '',
        f'  {BOLD}Free-tier models:{RESET}',
        *(f'    {DIM}•{RESET} {m} ({MODEL_CONTEXT_WINDOWS.get(m, "?"):,} ctx)' for m in ZEN_MODELS),
        '',
        f'  {BOLD}Pricing (Zen, not free-tier):{RESET}',
        f'  {DIM}  opencode/claude-sonnet-5     $2/$10  per M tokens{RESET}',
        f'  {DIM}  opencode/claude-haiku-4-5    $1/$5   per M tokens{RESET}',
        f'  {DIM}  opencode/gpt-5.4-mini        $0.75/$4.50 per M tokens{RESET}',
        f'  {DIM}  opencode/qwen3.7-plus        $0.40/$1.60 per M tokens{RESET}',
        '',
        f'  {BOLD}Links:{RESET}',
        f'  {CYAN}https://opencode.ai/docs/zen{RESET}',
        f'  {CYAN}https://github.com/anomalyco/opencode{RESET}',
        '',
        f'  {DIM}Use "Start Zen proxy" in the menu to begin.{RESET}',
    ]
    show_info_page('About OpenCode Zen', lines)


def _detect_provider() -> str:
    """Detect the current AI provider from ANTHROPIC_BASE_URL."""
    base = os.environ.get("ANTHROPIC_BASE_URL", "").lower()
    if "openrouter" in base:
        return "openrouter"
    if "opencode" in base or "127.0.0.1:6446" in base or "localhost:6446" in base or "zen" in base:
        return "zen"
    if "anthropic" in base:
        return "anthropic"
    if "openai" in base:
        return "openai"
    return "other"


PROVIDER_LABELS = {
    "openrouter": "OpenRouter",
    "zen": "OpenCode Zen",
    "anthropic": "Anthropic Direct",
    "openai": "OpenAI",
    "other": "Generic",
}


def _provider_model_entries(provider: str) -> list[tuple[str, str | None]]:
    """Return model entries relevant to the given provider."""
    entries: list[tuple[str, str | None]] = []

    if provider == "openrouter":
        entries = [
            ('── OpenAI models (via OpenRouter) ──', None),
            ('openai/gpt-4o-mini',                'openai/gpt-4o-mini'),
            ('openai/gpt-4o',                     'openai/gpt-4o'),
            ('openai/gpt-4.1',                    'openai/gpt-4.1'),
            ('openai/gpt-4.1-mini',               'openai/gpt-4.1-mini'),
            ('openai/gpt-4.1-nano',               'openai/gpt-4.1-nano'),
            ('openai/o3-mini',                    'openai/o3-mini'),
            ('openai/o4-mini',                    'openai/o4-mini'),
            ('openai/gpt-4.5-preview',            'openai/gpt-4.5-preview'),
            ('── Anthropic models (via OpenRouter) ──', None),
            ('anthropic/claude-sonnet-4.5',       'anthropic/claude-sonnet-4.5'),
            ('anthropic/claude-opus-4.5',         'anthropic/claude-opus-4.5'),
            ('anthropic/claude-sonnet-4',          'anthropic/claude-sonnet-4'),
            ('anthropic/claude-haiku-4.5',         'anthropic/claude-haiku-4.5'),
            ('anthropic/claude-3.5-sonnet',       'anthropic/claude-3.5-sonnet'),
            ('anthropic/claude-3.5-haiku',        'anthropic/claude-3.5-haiku'),
            ('── Google models (via OpenRouter) ──', None),
            ('google/gemini-2.5-pro',             'google/gemini-2.5-pro'),
            ('google/gemini-2.5-flash',           'google/gemini-2.5-flash'),
            ('google/gemini-2.5-flash-8b',        'google/gemini-2.5-flash-8b'),
            ('google/gemini-2.0-flash',           'google/gemini-2.0-flash'),
            ('google/gemma-3-27b-it',             'google/gemma-3-27b-it'),
            ('google/gemma-3-12b-it',             'google/gemma-3-12b-it'),
            ('── Meta models (via OpenRouter) ──', None),
            ('meta-llama/llama-4-maverick',       'meta-llama/llama-4-maverick'),
            ('meta-llama/llama-4-scout',          'meta-llama/llama-4-scout'),
            ('meta-llama/llama-3.3-70b-instruct', 'meta-llama/llama-3.3-70b-instruct'),
            ('meta-llama/llama-3.1-8b-instruct', 'meta-llama/llama-3.1-8b-instruct'),
            ('── DeepSeek models (via OpenRouter) ──', None),
            ('deepseek/deepseek-chat',            'deepseek/deepseek-chat'),
            ('deepseek/deepseek-r1',              'deepseek/deepseek-r1'),
            ('deepseek/deepseek-r1-distill-llama-70b', 'deepseek/deepseek-r1-distill-llama-70b'),
            ('── Qwen models (via OpenRouter) ──', None),
            ('qwen/qwen-2.5-72b-instruct',        'qwen/qwen-2.5-72b-instruct'),
            ('qwen/qwen-3-30b-instruct',          'qwen/qwen-3-30b-instruct'),
            ('qwen/qwq-32b',                      'qwen/qwq-32b'),
            ('── Mistral models (via OpenRouter) ──', None),
            ('mistral/mistral-large',             'mistral/mistral-large'),
            ('mistral/mistral-small',             'mistral/mistral-small'),
            ('mistral/codestral-2501',            'mistral/codestral-2501'),
            ('── Other popular (via OpenRouter) ──', None),
            ('cohere/command-r-plus',             'cohere/command-r-plus'),
            ('cohere/command-r7b-12-2024',        'cohere/command-r7b-12-2024'),
            ('ai21/jamba-1.6-mini',               'ai21/jamba-1.6-mini'),
            ('x-ai/grok-2-1212',                  'x-ai/grok-2-1212'),
            ('perplexity/sonar-pro',              'perplexity/sonar-pro'),
            ('nousresearch/hermes-3-llama-3.1-405b', 'nousresearch/hermes-3-llama-3.1-405b'),
        ]
    elif provider == "zen":
        from zen_proxy import ZEN_MODELS, MODEL_CONTEXT_WINDOWS
        entries = [
            ('── Zen models (proxy-supported) ──', None),
        ]
        for m in ZEN_MODELS:
            cw = MODEL_CONTEXT_WINDOWS.get(m, '?')
            cw_str = f'{cw:,} ctx' if isinstance(cw, int) else str(cw)
            entries.append((f'  {m} ({cw_str})', m))
    elif provider == "anthropic":
        entries = [
            ('── Anthropic Direct ──', None),
            ('claude-sonnet-4-5',                 'claude-sonnet-4-5'),
            ('claude-opus-4-5',                   'claude-opus-4-5'),
            ('claude-opus-4',                     'claude-opus-4'),
            ('claude-sonnet-4',                   'claude-sonnet-4'),
            ('claude-haiku-4-5',                  'claude-haiku-4-5'),
            ('claude-3-5-sonnet-20241022',        'claude-3-5-sonnet-20241022'),
            ('claude-3-5-haiku-20241022',         'claude-3-5-haiku-20241022'),
        ]
    elif provider == "openai":
        entries = [
            ('── OpenAI models ──', None),
            ('gpt-4o-mini',                       'gpt-4o-mini'),
            ('gpt-4o',                            'gpt-4o'),
            ('gpt-4.1',                           'gpt-4.1'),
            ('gpt-4.1-mini',                      'gpt-4.1-mini'),
            ('gpt-4.1-nano',                      'gpt-4.1-nano'),
            ('gpt-4.5-preview',                   'gpt-4.5-preview'),
            ('o3-mini',                           'o3-mini'),
            ('o4-mini',                           'o4-mini'),
            ('o1',                                'o1'),
            ('o1-mini',                           'o1-mini'),
        ]
    else:
        # Generic fallback — broad coverage
        entries = [
            ('openai/gpt-4o-mini',                'openai/gpt-4o-mini'),
            ('openai/gpt-4o',                     'openai/gpt-4o'),
            ('anthropic/claude-sonnet-4.5',       'anthropic/claude-sonnet-4.5'),
            ('anthropic/claude-3.5-sonnet',       'anthropic/claude-3.5-sonnet'),
            ('anthropic/claude-3.5-haiku',        'anthropic/claude-3.5-haiku'),
            ('google/gemini-2.5-flash',           'google/gemini-2.5-flash'),
            ('google/gemini-2.5-pro',             'google/gemini-2.5-pro'),
            ('meta-llama/llama-3.3-70b-instruct', 'meta-llama/llama-3.3-70b-instruct'),
            ('meta-llama/llama-4-maverick',       'meta-llama/llama-4-maverick'),
            ('deepseek/deepseek-chat',            'deepseek/deepseek-chat'),
            ('deepseek/deepseek-r1',              'deepseek/deepseek-r1'),
            ('mistral/mistral-large',             'mistral/mistral-large'),
            ('qwen/qwen-2.5-72b-instruct',        'qwen/qwen-2.5-72b-instruct'),
            ('cohere/command-r-plus',             'cohere/command-r-plus'),
        ]

    entries += [
        ('── Custom model ──', None),
        ('  ✏️  Enter custom model name', '__custom__'),
    ]
    return entries


def _show_filtered_model_menu(model_entries, label, current, initial_query=None):
    """Show a filtered subset of model entries based on user search query."""
    if initial_query is not None:
        query = initial_query
    else:
        query = prompt_text('Search models by name').lower().strip()
    if not query:
        # No query — go back to full list
        return page_choose_model()

    # Filter entries
    filtered = []
    for disp, val in model_entries:
        if val is None or val == '__custom__':
            # Keep separators and special entries
            filtered.append((disp, val))
        elif query in disp.lower() or (val and query in val.lower()):
            filtered.append((disp, val))

    if not filtered:
        show_info_page('No Matches',
                       [f'  No models matched "{query}".',
                        '',
                        '  Press any key to try again.'])
        return page_choose_model()

    f_display = [e[0] for e in filtered]
    f_values = [e[1] for e in filtered]
    idx = arrow_menu(f'Search Results: "{query}"  ({label})',
                     f_display,
                     footer='↑↓ navigate · Enter select · Esc cancel')
    if idx < 0:
        return  # back to main menu
    selected = f_values[idx]
    if selected is None:
        return
    if selected == '__custom__':
        model = prompt_text('Enter custom model name')
    else:
        model = selected
    if model:
        update_dotenv("AGENT_MODEL", model)
        # Also update saved provider config if available
        _update_provider_model(model)
        show_info_page('Done', [f'  ✓ Model set to: {model}'])


def _update_provider_model(model: str):
    """Update the model in the stored provider config, if any."""
    config = _load_providers_config()
    active = config.get("active")
    if active and active in config.get("providers", {}):
        config["providers"][active]["model"] = model
        _save_providers_config(config)


def page_choose_model():
    """Arrow-key menu to select a model."""
    provider = _detect_provider()
    label = PROVIDER_LABELS.get(provider, "Generic")

    # For OpenRouter: auto-fetch all models from API, fall back to static list
    if provider == "openrouter":
        fetched = _try_fetch_openrouter_entries()
        if fetched is not None:
            model_entries = fetched
        else:
            model_entries = _provider_model_entries(provider)
            model_entries.insert(0, ('🔍  Retry fetch from OpenRouter API', '__fetch__'))
    # For Zen: try to fetch from running proxy if available
    elif provider == "zen":
        from zen_proxy import check_status
        if check_status(6446):
            fetched = _try_fetch_zen_proxy_entries()
            if fetched is not None:
                model_entries = fetched
            else:
                model_entries = _provider_model_entries(provider)
        else:
            model_entries = _provider_model_entries(provider)
    else:
        model_entries = _provider_model_entries(provider)

    # Prepend action entries
    model_entries.insert(0, ('🔍  Search models by name', '__search__'))
    model_entries.insert(0, ('⟐  Switch active provider', '__switch_provider__'))

    current = os.environ.get("AGENT_MODEL", "Not set")

    # Count real model entries (skip separators and special actions)
    special = {'__search__', '__switch_provider__', '__fetch__', '__custom__'}
    real_count = sum(1 for _, v in model_entries if v is not None and v not in special)

    # For large model lists (> 30), prompt for search immediately
    if real_count > 30:
        query = prompt_text('Search models by name (press Enter to show all)').lower().strip()
        if query:
            _show_filtered_model_menu(model_entries, label, current, initial_query=query)
            return
        # Empty query — fall through to full list

    display = [e[0] for e in model_entries]
    values = [e[1] for e in model_entries]
    idx = arrow_menu(f'Choose Model  ({label}) — current: {current}',
                     display,
                     footer='↑↓ navigate · Enter select · Esc cancel')
    if idx < 0:
        return
    selected = values[idx]

    if selected is None:
        return  # separator
    if selected == '__search__':
        _show_filtered_model_menu(model_entries, label, current)
        return
    if selected == '__switch_provider__':
        switched = _choose_provider_to_switch()
        if switched:
            # Re-enter model picker with the new provider's models
            return page_choose_model()
        return
    if selected == '__fetch__' and provider == "openrouter":
        _fetch_openrouter_models()
        return page_choose_model()
    if selected == '__custom__':
        model = prompt_text('Enter custom model name')
    else:
        model = selected

    if model:
        update_dotenv("AGENT_MODEL", model)
        _update_provider_model(model)
        show_info_page('Done', [f'  ✓ Model set to: {model}'])


def _try_fetch_openrouter_entries() -> list[tuple[str, str | None]] | None:
    """Try to fetch model entries from OpenRouter API. Returns list or None on failure."""
    import urllib.request
    import json
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"User-Agent": "TOMAS/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None

    models = data.get("data", [])
    if not models:
        return None

    models.sort(key=lambda m: m.get("id", ""))
    entries: list[tuple[str, str | None]] = []
    for m in models:
        mid = m.get("id", "")
        pricing = m.get("pricing", {}) or {}
        try:
            prompt_p = float(pricing.get("prompt")) if pricing.get("prompt") is not None else None
            comp_p = float(pricing.get("completion")) if pricing.get("completion") is not None else None
        except (ValueError, TypeError):
            prompt_p = None
            comp_p = None
        if prompt_p is not None and comp_p is not None:
            label = f'{mid}  (P: ${prompt_p:.4f} · C: ${comp_p:.4f} per token)'
        else:
            label = mid
        entries.append((label, mid))

    if not entries:
        return None

    return entries


def _try_fetch_zen_proxy_entries() -> list[tuple[str, str | None]] | None:
    """Fetch model entries from the running Zen proxy (http://127.0.0.1:6446/v1/models)."""
    import urllib.request
    import json
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:6446/v1/models",
            headers={"User-Agent": "TOMAS/1.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None

    models = data.get("data", [])
    if not models:
        return None

    entries: list[tuple[str, str | None]] = []
    for m in models:
        mid = m.get("id", "")
        entries.append((f'  {mid}', mid))

    return entries


def _fetch_openrouter_models():
    """Fetch models from OpenRouter API and let user pick one (fallback manual trigger)."""
    clear_screen()
    print(f'{DIM}Fetching models from OpenRouter API...{RESET}')
    sys.stdout.flush()
    entries = _try_fetch_openrouter_entries()
    if entries is None:
        show_info_page('⚠ Error', ['  Failed to fetch models from OpenRouter API.'])
        return

    display = [e[0] for e in entries]
    values = [e[1] for e in entries]
    idx = arrow_menu(f'OpenRouter Models ({len(entries)} available, showing first 150)',
                     display[:150],
                     footer='↑↓ navigate · Enter select · Esc cancel')
    if idx < 0:
        return
    model = values[idx]
    if model:
        update_dotenv("AGENT_MODEL", model)
        show_info_page('Done', [f'  ✓ Model set to: {model}'])


def page_run_agent():
    """Launch the agent."""
    clear_screen()
    print(f'{BOLD}Starting TOMAS Agent{RESET}')
    print('─' * 50)
    print("Type 'quit' or 'exit' to leave. Ctrl+C also works.")
    print()
    from agent import main
    main()


# ═══════════════════════════════════════════════════════════
#  MAIN MENU
# ═══════════════════════════════════════════════════════════

TOMAS_ART = fr'''
{BLUE}╔══════════════════════════════════════════════╗{RESET}
{BLUE}║{RESET}  {CYAN}{BOLD}████████╗ ██████╗ ███╗   ███╗ █████╗ ███████╗{RESET}  {BLUE}║{RESET}
{BLUE}║{RESET}  {CYAN}{BOLD}╚══██╔══╝██╔═══██╗████╗ ████║██╔══██╗██╔════╝{RESET}  {BLUE}║{RESET}
{BLUE}║{RESET}  {CYAN}{BOLD}   ██║   ██║   ██║██╔████╔██║███████║███████╗{RESET}  {BLUE}║{RESET}
{BLUE}║{RESET}  {CYAN}{BOLD}   ██║   ██║   ██║██║╚██╔╝██║██╔══██║╚════██║{RESET}  {BLUE}║{RESET}
{BLUE}║{RESET}  {CYAN}{BOLD}   ██║   ╚██████╔╝██║ ╚═╝ ██║██║  ██║███████║{RESET}  {BLUE}║{RESET}
{BLUE}║{RESET}  {CYAN}{BOLD}   ╚═╝    ╚═════╝ ╚═╝     ╚═╝╚═╝  ╚═╝╚══════╝{RESET}  {BLUE}║{RESET}
{BLUE}║{RESET}                                            {BLUE}║{RESET}
{BLUE}║{RESET}  {DIM}{MAGENTA}An AI Coding Agent — from the Terminal{RESET} {BLUE}║{RESET}
{BLUE}╚══════════════════════════════════════════════╝{RESET}
'''

MENU_ITEMS = [
    f'  {GREEN}⟐{RESET}  Check default LLM model',
    f'  {GREEN}⟐{RESET}  Check connected providers',
    f'  {GREEN}⟐{RESET}  Connect / configure provider',
    f'  {GREEN}⟐{RESET}  Switch active provider',
    f'  {GREEN}⟐{RESET}  Choose model to use',
    f'  {YELLOW}⬡{RESET}  Check available MCPs',
    f'  {YELLOW}⬡{RESET}  Check available tools',
    f'  {YELLOW}⬡{RESET}  Check installed skills',
    f'  {BLUE}✎{RESET}  View/Edit agent instructions',
    f'  {BLUE}✎{RESET}  View/Edit project guidelines',
    f'  {MAGENTA}▶{RESET}  {BOLD}Run agent (interactive){RESET}',
    f'  {RED}✕{RESET}  Exit',
]

MENU_ACTIONS = [
    page_model,
    page_providers,
    page_configure_provider,
    _choose_provider_to_switch,
    page_choose_model,
    page_mcps,
    page_tools,
    page_skills,
    page_edit_instructions,
    page_edit_claude,
    page_run_agent,
    None,  # exit
]

EXIT_INDEX = len(MENU_ITEMS) - 1  # 11


def run_menu():
    """Run the main interactive menu."""
    while True:
        model_status = f'{CYAN}{get_model()}{RESET}'
        active_provider = _get_active_provider_name()
        if active_provider:
            provider_status = f'{GREEN}{active_provider}{RESET}'
        else:
            provider_status = f'{DIM}None configured{RESET}'
        header = [
            TOMAS_ART,
            f'  {BOLD}Control Panel{RESET}',
            f'  {DIM}{"─" * 46}{RESET}',
            f'  {BLUE}Project{RESET}  {AGENT_PROJECT_DIR.name}',
            f'  {GREEN}Provider{RESET} {provider_status}',
            f'  {CYAN}Model{RESET}    {model_status}',
            '',
        ]
        idx = arrow_menu('', MENU_ITEMS,
                         header_lines=header,
                         footer=f'{DIM}↑↓ navigate  ·  Enter select  ·  Esc/q to quit{RESET}')
        if idx < 0 or idx == EXIT_INDEX:
            clear_screen()
            print(f'{BOLD}TOMAS{RESET} — {DIM}See you later!{RESET}')
            print()
            break
        action = MENU_ACTIONS[idx]
        if action:
            action()


# ═══════════════════════════════════════════════════════════
#  SUBCOMMAND DISPATCH — TOMAS mcp/skill/--run
# ═══════════════════════════════════════════════════════════

def cmd_run_agent():
    """Launch the agent directly."""
    clear_screen()
    print(f'{BOLD}TOMAS Agent{RESET}')
    print('─' * 50)
    from agent import main
    main()


def cmd_mcp_list():
    """List MCP servers."""
    from mcp_manager import cmd_mcp_list
    print(cmd_mcp_list())


def cmd_mcp_add():
    """Add an MCP server.
    Syntax:
        TOMAS mcp add --transport http <name> <url>
        TOMAS mcp add --transport stdio <name> -- <command> [args...]
    """
    from mcp_manager import write_mcp_server

    argv = sys.argv[3:]  # skip "TOMAS mcp add"

    # Find --transport flag
    transport = None
    if "--transport" in argv:
        idx = argv.index("--transport")
        if idx + 1 < len(argv):
            transport = argv[idx + 1]
            argv = argv[:idx] + argv[idx + 2:]

    if not transport:
        print("Error: --transport flag required (http or stdio)")
        print("Usage:")
        print("  TOMAS mcp add --transport http <name> <url>")
        print("  TOMAS mcp add --transport stdio <name> -- <command> [args...]")
        sys.exit(1)

    if transport == "http":
        if len(argv) < 2:
            print("Error: TOMAS mcp add --transport http <name> <url>")
            sys.exit(1)
        name = argv[0]
        url = argv[1]
        write_mcp_server(name, {"type": "http", "url": url})
        print(f"✓ Added HTTP MCP server: {name} -> {url}")

    elif transport == "stdio":
        if "--" not in argv:
            print("Error: TOMAS mcp add --transport stdio <name> -- <command> [args...]")
            sys.exit(1)
        sep = argv.index("--")
        name = argv[0] if sep > 0 else None
        if not name:
            print("Error: name is required before --")
            sys.exit(1)
        cmd_parts = argv[sep + 1:]
        if not cmd_parts:
            print("Error: command required after --")
            sys.exit(1)
        command = cmd_parts[0]
        args_list = cmd_parts[1:] if len(cmd_parts) > 1 else []
        write_mcp_server(name, {
            "type": "stdio",
            "command": command,
            "args": args_list,
        })
        cmd_str = " ".join([command] + args_list)
        print(f"✓ Added stdio MCP server: {name} -> {cmd_str}")

    else:
        print(f"Error: Unknown transport '{transport}'. Use 'http' or 'stdio'.")
        sys.exit(1)


def cmd_mcp_remove():
    """Remove an MCP server."""
    from mcp_manager import remove_mcp_server
    argv = sys.argv[3:]  # skip "TOMAS mcp remove"
    if not argv:
        print("Error: TOMAS mcp remove <name>")
        sys.exit(1)
    name = argv[0]
    if remove_mcp_server(name):
        print(f"✓ Removed MCP server: {name}")
    else:
        print(f"Error: MCP server '{name}' not found.")


def cmd_mcp_env():
    """Manage environment variables for an MCP server (e.g. auth tokens)."""
    from mcp_manager import cmd_mcp_env as mcp_env
    argv = sys.argv[3:]  # skip "TOMAS mcp env"
    result = mcp_env(argv)
    print(result)


def cmd_mcp_disable():
    """Disable an MCP server (won't be connected at startup)."""
    from mcp_manager import set_server_disabled, read_mcp_servers
    argv = sys.argv[3:]  # skip "TOMAS mcp disable"
    if not argv:
        print("Error: TOMAS mcp disable <name>")
        sys.exit(1)
    name = argv[0]
    servers = read_mcp_servers()
    if name not in servers:
        print(f"Error: MCP server '{name}' not found.")
        sys.exit(1)
    set_server_disabled(name, True)
    print(f"✓ Server '{name}' disabled. It will be skipped on next agent start.")


def cmd_mcp_enable():
    """Enable a disabled MCP server."""
    from mcp_manager import set_server_disabled, read_mcp_servers
    argv = sys.argv[3:]  # skip "TOMAS mcp enable"
    if not argv:
        print("Error: TOMAS mcp enable <name>")
        sys.exit(1)
    name = argv[0]
    servers = read_mcp_servers()
    if name not in servers:
        print(f"Error: MCP server '{name}' not found.")
        sys.exit(1)
    set_server_disabled(name, False)
    print(f"✓ Server '{name}' enabled. It will be connected on next agent start.")


def cmd_skill_list():
    """List installed skills with enhanced formatting."""
    from skills_manager import discover_skills, find_skill_dirs

    all_skills = discover_skills()
    dirs = find_skill_dirs()
    if not all_skills:
        print("No skills installed.")
        return

    by_dir: dict[str, list] = {}
    for d in dirs:
        by_dir[str(d)] = []

    for s in all_skills:
        matched = False
        for d in dirs:
            d_str = str(d)
            if str(s["file"]).startswith(d_str):
                by_dir[d_str].append(s)
                matched = True
                break
        if not matched and dirs:
            by_dir[str(dirs[0])].append(s)

    print(f"Skills: {len(all_skills)} total")
    print()

    for d in dirs:
        d_str = str(d)
        group = by_dir.get(d_str, [])
        if not group:
            continue
        parts = d_str.replace(".agents", "~/.agents").replace(".claude", "~/.claude").split("\\")
        short = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        print(f"  ◉ {short}  ({len(group)} skills)")
        print()

        max_name = min(max(len(s["name"]) for s in group) + 2, 40)
        max_name = max(max_name, 30)

        for s in sorted(group, key=lambda x: x["name"]):
            name = s["name"]
            desc = s["description"] or ""
            desc_max = max(50, 80 - max_name - 4)
            if len(desc) > desc_max:
                desc = desc[:desc_max - 3] + "..."
            pad = " " * (max_name - len(name))
            print(f"    {name}{pad}{desc}")
        print()


def print_help():
    print(__doc__.strip())


# ═══════════════════════════════════════════════════════════
#  CLI ENTRY POINT — parse subcommands
# ═══════════════════════════════════════════════════════════

def main():
    # ── No args → interactive TUI ──
    if len(sys.argv) == 1:
        run_menu()
        return

    arg = sys.argv[1]

    # ── --run → launch agent ──
    if arg in ('--run', '-r'):
        cmd_run_agent()
        return

    # ── --help → show help ──
    if arg in ('--help', '-h'):
        print_help()
        return

    # ── mcp subcommand ──
    if arg == 'mcp':
        if len(sys.argv) < 3:
            print("Usage: TOMAS mcp {list|add|remove|env|disable|enable} [...]")
            print("  TOMAS mcp list")
            print("  TOMAS mcp add --transport http <name> <url>")
            print("  TOMAS mcp add --transport stdio <name> -- <command> [args...]")
            print("  TOMAS mcp remove <name>")
            print("  TOMAS mcp env <server> [KEY=VALUE|--unset KEY]")
            print("  TOMAS mcp disable <name>")
            print("  TOMAS mcp enable <name>")
            sys.exit(1)
        sub = sys.argv[2]
        if sub == 'list':
            cmd_mcp_list()
        elif sub == 'add':
            cmd_mcp_add()
        elif sub == 'remove':
            cmd_mcp_remove()
        elif sub == 'env':
            cmd_mcp_env()
        elif sub == 'disable':
            cmd_mcp_disable()
        elif sub == 'enable':
            cmd_mcp_enable()
        else:
            print(f"Unknown mcp subcommand: {sub}")
            print("Usage: TOMAS mcp {list|add|remove|env|disable|enable} [...]")
            sys.exit(1)
        return

    # ── skill subcommand ──
    if arg == 'skill':
        if len(sys.argv) < 3:
            print("Usage: TOMAS skill {list}")
            print("  TOMAS skill list")
            sys.exit(1)
        sub = sys.argv[2]
        if sub == 'list':
            cmd_skill_list()
        else:
            print(f"Unknown skill subcommand: {sub}")
            print("Usage: TOMAS skill {list}")
            sys.exit(1)
        return

    # ── Unknown argument ──
    print(f"Unknown option: {arg}")
    print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
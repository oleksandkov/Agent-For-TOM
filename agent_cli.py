#!/usr/bin/env python3
"""
TOMAS CLI — Interactive TUI + subcommand interface for the TOMAS agent.

Usage:
    TOMAS                          Interactive TUI menu (arrow keys + Enter)
    TOMAS --run                    Launch agent directly
    TOMAS setup                    Install default MCPs and configure environment
    TOMAS mcp list                 List configured MCP servers
    TOMAS mcp add <name> -- <command> [args...]   (stdio, default)
    TOMAS mcp add --transport http <name> <url>   (HTTP)
    TOMAS mcp remove <name>        Remove an MCP server
    TOMAS mcp disable <name>       Disable an MCP server (skipped at startup)
    TOMAS mcp enable <name>        Re-enable a disabled MCP server
    TOMAS mcp env <server>          List env vars for an MCP server
    TOMAS mcp env <server> KEY=VALUE   Set an env var (e.g. auth token)
    TOMAS mcp env <server> --unset KEY  Remove an env var
    TOMAS skill list               List installed skills
    TOMAS skill install <name> -- <command> [args...]  Install a skill from npm
    TOMAS browser                  Download the Playwright browser (~170 MB)
                                   for web search; duckduckgo is used without it
    TOMAS update                   Update TOMAS from GitHub
    TOMAS uninstall                Remove TOMAS completely
    TOMAS --help                   Show this help

Note:
    TOMAS upgrade is an alias for TOMAS update.
"""

import os
import sys
import subprocess
import shutil
import threading
from pathlib import Path

# ── Windows console setup (must run before anything can print) ──
# On a non-UTF-8 console codepage (cp1251/cp1252/cp437) the TUI's box-drawing
# and symbol glyphs raise UnicodeEncodeError. errors="replace" guarantees a
# glyph degrades to '?' instead of taking down the process.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        os.system("")  # enables ANSI escape processing in legacy consoles
    except Exception:
        pass

# Session and instructions management
from session_manager import (
    list_sessions, load_session, continue_session,
    delete_session, get_session_count, clear_all_sessions,
)

# ── Windows msvcrt for keyboard input ──
import msvcrt

# Add project directory to path
PROJECT_DIR = Path(__file__).parent.resolve()
TOMAS_DIR = Path.home() / ".tomas"
sys.path.insert(0, str(PROJECT_DIR))

ENV_FILE = TOMAS_DIR / ".env"           # durable config; survives an update
_LEGACY_ENV_FILE = PROJECT_DIR / ".env"  # inside $SrcDir in a deployed install


def _migrate_src_env() -> None:
    """Rescue config the CLI used to write into the source tree.

    `TOMAS update` deletes $SrcDir wholesale (install.ps1:231), so API keys,
    base URLs and the model selection written there were destroyed on every
    upgrade. Only runs for a real deployed install ($SrcDir == ~/.tomas/src) —
    a dev checkout's .env is the developer's own file and is left alone.
    """
    if PROJECT_DIR != TOMAS_DIR / "src" or not _LEGACY_ENV_FILE.exists():
        return
    try:
        existing = ""
        if ENV_FILE.exists():
            existing = ENV_FILE.read_text(encoding="utf-8")
        have = {ln.split("=", 1)[0].strip()
                for ln in existing.splitlines() if "=" in ln}

        rescued = [ln for ln in _LEGACY_ENV_FILE.read_text(encoding="utf-8").splitlines()
                   if "=" in ln and ln.split("=", 1)[0].strip() not in have]
        if rescued:
            TOMAS_DIR.mkdir(parents=True, exist_ok=True)
            merged = existing.rstrip("\n")
            merged = (merged + "\n" if merged else "") + "\n".join(rescued) + "\n"
            ENV_FILE.write_text(merged, encoding="utf-8")
        # with_suffix would give ".env.env.migrated" — for a dotfile the whole
        # name is the stem, so set the name outright.
        _LEGACY_ENV_FILE.rename(_LEGACY_ENV_FILE.with_name(".env.migrated"))
    except Exception:
        pass


_migrate_src_env()

# Load .env — durable config first, then any dev-checkout overrides
from dotenv import load_dotenv
load_dotenv(ENV_FILE)  # main config (API key, etc.)
load_dotenv(_LEGACY_ENV_FILE, override=True)  # dev-checkout overrides (model)

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


# The .env primitives live in provider_manager so config persistence has one
# implementation. ENV_FILE stays a module-level name here because it is the
# path this UI writes to (and what the durability tests substitute).
from provider_manager import set_env_key as _set_env_key, drop_env_key as _drop_env_key


def update_dotenv(key: str, value: str):
    """Persist a config key and apply it to the running process.

    Writes to ~/.tomas/.env, which survives an update — this used to write
    into the source tree, so every API key and provider chosen through the
    menus was wiped by `TOMAS update`.

    Because a dev checkout's .env is loaded afterwards with override=True, a
    stale entry there would silently shadow what the user just set, so the
    key is dropped from that file too.
    """
    _set_env_key(ENV_FILE, key, value)
    _drop_env_key(_LEGACY_ENV_FILE, key)

    # Also update the running process environment so get_model() works immediately
    os.environ[key] = value


# ═══════════════════════════════════════════════════════════
#  KEYBOARD INPUT
# ═══════════════════════════════════════════════════════════

_SPECIAL_KEYS = {
    'H': 'UP', 'P': 'DOWN',
    'K': 'LEFT', 'M': 'RIGHT',
    'G': 'HOME', 'O': 'END',
    'I': 'PGUP', 'Q': 'PGDN',
    'S': 'DELETE',
}


def get_key():
    """Read a single keypress. Returns a key name, or the character typed.

    Reads through `getwch` rather than `getch`: `getch` hands back one *byte*,
    so a Cyrillic keystroke arrived as an undecodable fragment and was
    discarded as '?'. The REPL was fixed for this in Phase 7; the menus were
    not, which is why the type-to-filter binding needs it here.
    """
    ch = msvcrt.getwch()
    if ch in ('\x00', '\xe0'):          # arrow / function key: a second read follows
        return _SPECIAL_KEYS.get(msvcrt.getwch(), 'FUNC')
    if ch == '\r':
        return 'ENTER'
    if ch == '\x1b':
        return 'ESC'
    if ch == '\x03':
        return 'CTRL_C'
    if ch in ('\x08', '\x7f'):
        return 'BACKSPACE'
    if ch == '\t':
        return 'TAB'
    return ch


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
    """Clear the terminal screen.

    This used to be `os.system('cls')`, which spawns a whole cmd.exe for the
    sake of blanking a screen — ~13 ms and a visible flash, paid on every
    menu open. The TUI already requires ANSI (it is built out of colour and
    cursor escapes, and enables VT processing at import), so the escape
    sequence is both faster and flicker-free. `\033[3J` also clears the
    scrollback, which `cls` does and a bare `\033[2J` does not.
    """
    try:
        sys.stdout.write('\033[H\033[2J\033[3J')
        sys.stdout.flush()
    except Exception:
        os.system('cls' if os.name == 'nt' else 'clear')


# ═══════════════════════════════════════════════════════════
#  GENERIC ARROW-KEY MENU
# ═══════════════════════════════════════════════════════════

CURSOR_UP_N = '\033[{}A'  # move cursor up N lines
ERASE_DOWN = '\033[J'     # erase from cursor to end of screen

from text_display import display_width, shorten, strip_ansi, term_columns, term_lines


def menu_row_count(item: str, columns: int) -> int:
    """How many terminal rows `item` occupies once drawn.

    The redraw bug this fixes came from assuming the answer is always 1. It
    is not: the session browser builds two-row entries (`label + '\\n' +
    summary`), and any label longer than the window soft-wraps. Both are
    counted here.
    """
    if not item:
        return 1
    rows = 0
    for segment in item.split('\n'):
        w = display_width(segment)
        rows += max(1, -(-w // columns)) if columns > 0 else 1
    return rows


def _is_selectable(item: str) -> bool:
    """Blank spacer rows are drawn but must not be landed on."""
    return bool(strip_ansi(item or '').strip())


def _matches(item: str, needle: str) -> bool:
    return needle in strip_ansi(item or '').lower()


# One footer, so every menu advertises the same keys — and mentions `?`,
# which is where the rest of them are written down.
DEFAULT_FOOTER = '↑↓ navigate · Enter select · Esc back · ? keys'

MENU_KEYS = [
    ("↑ ↓",       "move (blank spacer rows are skipped)"),
    ("PgUp PgDn", "move a screenful"),
    ("Home End",  "first / last row"),
    ("1-9",       "jump to the Nth selectable row"),
    ("/",         "filter the list · Esc clears the filter"),
    ("Enter",     "select"),
    ("Esc ← q",   "back"),
    ("?",         "this help"),
]


def _show_menu_keys() -> None:
    """The key overlay. Painted over the menu; the caller repaints after."""
    clear_screen()
    budget = max(1, term_columns() - 1)
    print(f'  {BOLD}Menu keys{RESET}')
    print(f'  {DIM}{"─" * min(50, budget - 2)}{RESET}')
    width = max(len(k) for k, _ in MENU_KEYS)
    for key, desc in MENU_KEYS:
        print(shorten(f'    {CYAN}{key}{RESET}{" " * (width - len(key) + 2)}{DIM}{desc}{RESET}', budget))
    print()
    sys.stdout.write(f'  {DIM}Press any key to go back{RESET}')
    sys.stdout.flush()
    get_key()


def arrow_menu(title: str, items: list, header_lines: list = None,
               footer: str = None, max_visible: int = 14) -> int:
    """Arrow-key menu with a row-accurate viewport.

    Returns the index of the selected item in `items`, or -1 if cancelled.

    The previous implementation tracked its viewport in *items* and redrew by
    moving the cursor up `len(visible) + 1` rows. Any item that occupied more
    than one row — a multi-line session entry, or a label wider than the
    window — made that number too small, so each redraw started lower than
    the last one had ended and the list visibly duplicated itself down the
    screen, leaving stale `▶` cursors behind (that is the reported bug).

    Two changes make it correct rather than nearly correct:

      * every drawn row is truncated to one column short of the terminal, so
        nothing soft-wraps and the row count is knowable; and
      * `draw_all` returns the number of rows it actually emitted, and the
        redraw rewinds by exactly that, then `ERASE_DOWN` clears whatever was
        below. What gets erased is what was drawn, by construction.

    Keys: ↑↓ move (skipping blank spacers), PgUp/PgDn, Home/End, digits jump,
    `/` filters, Enter selects, Esc/←/q cancels.
    """
    n = len(items)
    if n == 0:
        return -1

    columns = term_columns()
    line_budget = max(1, columns - 1)   # never touch the last column
    # Budget against the rows the header actually *draws*, not the number of
    # entries in the list. A single entry can be many rows -- TOMAS_ART is one
    # string holding 11 lines -- so counting entries overestimated the space
    # left by ~10 rows and let the list run off the bottom of the window.
    if header_lines:
        header_rows = sum(menu_row_count(line, line_budget) for line in header_lines)
    else:
        header_rows = 2                 # title + rule, drawn by draw_header()
    rows_available = max(4, term_lines() - header_rows - 3)
    if max_visible:
        rows_available = min(rows_available, max(4, max_visible))

    query = ''
    filtering = False
    last_rows = 0

    def visible_indices() -> list[int]:
        """Item indices eligible to be drawn, honouring the active filter."""
        if query:
            return [i for i in range(n) if _is_selectable(items[i])
                    and _matches(items[i], query)]
        return list(range(n))

    def landable(shown: list[int]) -> list[int]:
        """Of the drawn rows, the ones the cursor may rest on."""
        stops = [i for i in shown if _is_selectable(items[i])]
        return stops or shown

    shown = visible_indices()
    stops = landable(shown)
    selected = stops[0] if stops else 0

    def window(shown: list[int]) -> list[int]:
        """The slice of `shown` that fits in the row budget, around `selected`."""
        if not shown:
            return []
        try:
            pos = shown.index(selected)
        except ValueError:
            pos = 0
        start = pos
        used = menu_row_count(items[shown[pos]], line_budget)
        end = pos + 1
        # Grow forwards first, then backwards, so the selection stays put
        # instead of jumping to the top of the viewport on every keypress.
        while end < len(shown):
            cost = menu_row_count(items[shown[end]], line_budget)
            if used + cost > rows_available:
                break
            used += cost
            end += 1
        while start > 0:
            cost = menu_row_count(items[shown[start - 1]], line_budget)
            if used + cost > rows_available:
                break
            used += cost
            start -= 1
        return shown[start:end]

    def draw_all() -> int:
        """Draw the viewport and footer. Returns rows emitted."""
        rows = 0
        view = window(shown)
        for i in view:
            chosen = (i == selected)
            for k, segment in enumerate((items[i] or '').split('\n')):
                if k == 0:
                    prefix = f'{GREEN}▶{RESET} ' if chosen else '  '
                    body = f'{BOLD}{segment}{RESET}' if chosen else segment
                else:
                    prefix, body = '  ', segment
                line = shorten(prefix + body, line_budget)
                sys.stdout.write(CLEAR_LINE + line + RESET + '\n')
                rows += 1

        if filtering:
            base = f'/{query}{DIM}  ({len(stops)} match){RESET}'
        else:
            base = footer if footer else DEFAULT_FOOTER
            if len(view) < len(shown):
                here = (stops.index(selected) + 1) if selected in stops else 0
                base += f'  [{here}/{len(stops)}]'
        sys.stdout.write(CLEAR_LINE + DIM + shorten(base, line_budget) + RESET + '\n')
        return rows + 1

    def redraw():
        nonlocal last_rows
        if last_rows:
            sys.stdout.write(CURSOR_UP_N.format(last_rows) + ERASE_DOWN)
        last_rows = draw_all()
        sys.stdout.flush()

    def move(step: int):
        """Advance the cursor by `step` landable rows, wrapping at the ends."""
        nonlocal selected
        if not stops:
            return
        try:
            pos = stops.index(selected)
        except ValueError:
            pos = 0
        selected = stops[(pos + step) % len(stops)]

    def refilter():
        nonlocal shown, stops, selected
        shown = visible_indices()
        stops = landable(shown)
        if stops and selected not in stops:
            selected = stops[0]

    def draw_header():
        if header_lines:
            # A header entry can itself be several lines -- TOMAS_ART is one
            # string containing embedded newlines. shorten() measures a
            # newline as zero width and keeps consuming budget past it, so
            # passing the whole block through in one call truncated the
            # entire ASCII banner down to one line's worth of columns and cut
            # it off mid-render with a trailing ellipsis. Each physical line
            # needs its own budget.
            for line in header_lines:
                for sub in line.split(chr(10)):
                    print(shorten(sub, line_budget))
        else:
            print(f'{BOLD}{shorten(title, line_budget)}{RESET}')
            print('─' * min(50, line_budget))

    def full_redraw():
        """Repaint everything, header included, and re-anchor the rewind.

        `redraw` rewinds by `last_rows`, which is only meaningful while the
        rows below the header are the last thing on screen. Anything that
        paints over the whole screen — the key overlay — has to come back
        through here, or the next rewind lands in the middle of it.
        """
        nonlocal last_rows
        clear_screen()
        draw_header()
        last_rows = draw_all()
        sys.stdout.flush()

    # ── First draw ──
    sys.stdout.write(HIDE_CURSOR)
    full_redraw()

    def leave(result: int) -> int:
        sys.stdout.write(SHOW_CURSOR)
        sys.stdout.flush()
        return result

    # ── Event loop ──
    while True:
        key = get_key()

        # Filter mode captures text; everything else is a normal binding.
        if filtering and key not in ('ENTER', 'ESC', 'UP', 'DOWN', 'CTRL_C'):
            if key == 'BACKSPACE':
                query = query[:-1]
            elif isinstance(key, str) and len(key) == 1 and key.isprintable():
                query += key.lower()
            else:
                continue
            refilter()
            redraw()
            continue

        if key == 'UP':
            move(-1)
            redraw()
        elif key == 'DOWN':
            move(1)
            redraw()
        elif key == 'PGUP':
            move(-max(1, len(window(shown)) - 1))
            redraw()
        elif key == 'PGDN':
            move(max(1, len(window(shown)) - 1))
            redraw()
        elif key == 'HOME':
            if stops:
                selected = stops[0]
            redraw()
        elif key == 'END':
            if stops:
                selected = stops[-1]
            redraw()
        elif key == 'ENTER':
            if filtering:
                filtering = False
                redraw()
                if not stops:
                    continue
            return leave(selected)
        elif key == 'ESC':
            if filtering or query:
                filtering, query = False, ''
                refilter()
                redraw()
                continue
            return leave(-1)
        elif key in ('LEFT', 'q', 'CTRL_C'):
            return leave(-1)
        elif key == '/':
            filtering, query = True, ''
            refilter()
            redraw()
        elif key == '?':
            _show_menu_keys()
            full_redraw()
        elif isinstance(key, str) and key.isdigit() and key != '0':
            # Jump to the Nth selectable row — the menus are numbered lists.
            target = int(key) - 1
            if target < len(stops):
                selected = stops[target]
                redraw()


def confirm_menu(title: str, items: list, header_lines: list = None,
                 footer: str = None) -> int:
    """Alias for arrow_menu — returns index or -1."""
    return arrow_menu(title, items, header_lines, footer)


# ═══════════════════════════════════════════════════════════
#  INFO DISPLAY (paged / press-any-key)
# ═══════════════════════════════════════════════════════════

def show_info_page(title: str, lines: list, prompt: str = "Press any key to go back",
                   accept: tuple = ()):
    """Display a page of info, scrollable, and wait for the user to leave.

    Two fixes over the previous version, which printed everything and waited
    on a bare `msvcrt.getch()`:

    * **It scrolls.** The tools and skills pages are far longer than a
      terminal, so their first screens used to scroll off the top before the
      user could read them, with no way back.
    * **It consumes whole keys.** `getch()` returns one byte, and an arrow key
      sends two. The orphaned second byte stayed in the buffer and was read by
      the *next* menu as a phantom keypress, moving the selection on its own.

    `accept` names keys the caller wants back rather than swallowed (e.g.
    `('e',)` for "edit this file"). Returns the key that was pressed when it is
    one of those, else None. It exists so a page offering an action does not
    have to re-implement the scrolling and key decoding to get one keystroke —
    which is exactly how the instruction pages ended up back on raw `getch()`
    with both bugs above.
    """
    columns = term_columns()
    budget = max(1, columns - 1)
    body = []
    for line in lines:
        body.extend((line or '').split('\n'))

    top = 0
    while True:
        page = max(3, term_lines() - 5)
        top = max(0, min(top, max(0, len(body) - page)))
        clear_screen()
        print(f'{BOLD}{shorten(title, budget)}{RESET}')
        print('─' * min(50, budget))
        for line in body[top:top + page]:
            print(shorten(line, budget))

        more = len(body) > page
        if more:
            hint = (f'{DIM}{top + 1}-{min(top + page, len(body))} of {len(body)}'
                    f'  ·  ↑↓ PgUp/PgDn scroll  ·  Esc back{RESET}')
        else:
            hint = DIM + prompt + '...' + RESET
        print()
        sys.stdout.write(shorten(hint, budget))
        sys.stdout.flush()

        key = get_key()
        if accept and key in accept:
            return key
        if not more:
            return None
        if key in ('ESC', 'ENTER', 'q', 'LEFT', 'CTRL_C'):
            return None
        if key == 'UP':
            top -= 1
        elif key == 'DOWN':
            top += 1
        elif key == 'PGUP':
            top -= page
        elif key == 'PGDN':
            top += page
        elif key == 'HOME':
            top = 0
        elif key == 'END':
            top = len(body)


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

PROVIDERS_CONFIG_PATH = TOMAS_DIR / "providers.json"
_LEGACY_PROVIDERS_PATH = PROJECT_DIR / "providers.json"


def _migrate_providers_config() -> None:
    """One-time move of provider config out of the source tree.

    `TOMAS update` replaces $SrcDir (== PROJECT_DIR in a deployed install)
    wholesale, so provider config stored there was wiped on every update.
    """
    if PROVIDERS_CONFIG_PATH.exists() or not _LEGACY_PROVIDERS_PATH.exists():
        return
    try:
        import json
        data = json.loads(_LEGACY_PROVIDERS_PATH.read_text(encoding="utf-8"))
        if not data.get("providers"):
            return  # nothing worth keeping (e.g. the committed empty stub)
        TOMAS_DIR.mkdir(parents=True, exist_ok=True)
        PROVIDERS_CONFIG_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        _LEGACY_PROVIDERS_PATH.rename(
            _LEGACY_PROVIDERS_PATH.with_suffix(".json.migrated"))
    except Exception:
        pass


def _migrate_decorated_provider_names() -> None:
    """Strip menu decoration that leaked into saved provider *names*.

    The configure page used one list for both the labels it drew and the key it
    saved under, and the OpenCode Zen entry carried a trailing `◈` marker. Any
    config written before that was fixed has the glyph baked into the name, so
    it shows up wherever the provider is displayed. The label no longer carries
    it; this renames what is already on disk.
    """
    try:
        config = _load_providers_config()
        providers = config.get("providers") or {}
        renames = {name: name.rstrip(" ◈") for name in providers
                   if name.rstrip(" ◈") != name}
        if not renames:
            return
        for old, new in renames.items():
            providers[new] = providers.pop(old)
            if config.get("active") == old:
                config["active"] = new
        _save_providers_config(config)
    except Exception:
        pass


_migrate_providers_config()


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


# Runs here rather than beside the other migration: it needs both accessors above.
_migrate_decorated_provider_names()


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
    import provider_manager
    config = _load_providers_config()
    # Restricted to the current working set — see
    # provider_manager.VISIBLE_PROVIDER_TYPES. A provider configured outside
    # it (a legacy entry, or one added by hand) stays in providers.json and
    # keeps working if switched to directly; it just does not resurface here.
    all_providers = _get_configured_providers(config)
    providers = [p for p in all_providers
                if config.get("providers", {}).get(p, {}).get("type")
                in provider_manager.VISIBLE_PROVIDER_TYPES]
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
                     footer=DEFAULT_FOOTER)
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

def page_providers():
    """Show configured providers and what the active one can do."""
    from provider_manager import capabilities_for_active

    base_url = get_env_value('ANTHROPIC_BASE_URL')
    api_key = get_env_value('ANTHROPIC_API_KEY')
    config = _load_providers_config()
    providers = _get_configured_providers(config)
    active = config.get("active")

    lines = []
    if base_url != "Not set" and api_key != "Not set":
        lines.append(f'  {GREEN}✓{RESET} Connected  {active or base_url}')
        lines.append(f'    {DIM}Endpoint{RESET}  {base_url}')
        lines.append(f'    {DIM}Key{RESET}       ***{api_key[-4:]}')
        lines.append(f'    {DIM}Model{RESET}     {get_env_value("AGENT_MODEL")}')

        # Capabilities come from the stored probe — this never hits the network.
        caps = capabilities_for_active()
        state = 'probed' if caps.probed else 'assumed (not probed yet)'
        lines.append('')
        lines.append(f'  {BOLD}Capabilities{RESET} {DIM}({state}){RESET}')
        lines.append(f'    Context     {caps.context_window:,} tokens')
        lines.append(f'    Tool limit  {caps.max_tools}')
        lines.append(f'    Streaming   {"yes" if caps.streaming else "no"}')
    else:
        lines.append(f'  {RED}✗{RESET} No active provider')
        lines.append(f'    {DIM}Use "Add or Configure Provider" to connect one.{RESET}')
    lines.append('')

    if providers:
        lines.append(f'  {BOLD}Saved{RESET} {DIM}({len(providers)}){RESET}')
        for p in providers:
            mark = f'{GREEN}◈{RESET}' if p == active else ' '
            suffix = f' {DIM}· active{RESET}' if p == active else ''
            lines.append(f'    {mark} {p}{suffix}')
    else:
        lines.append(f'  {DIM}No saved provider configurations.{RESET}')

    # Keys picked up from the environment that a provider could be built from.
    env_found = [var for var in
                 ('OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'GOOGLE_API_KEY', 'GROQ_API_KEY')
                 if get_env_value(var) != "Not set"]
    if env_found:
        lines.append('')
        lines.append(f'  {BOLD}Detected in environment{RESET}')
        for var in env_found:
            lines.append(f'    {GREEN}✓{RESET} {var}')

    show_info_page('Providers', lines)


def page_tools():
    """Show the built-in tools and the risk tier each one is approved under."""
    icons = {'low': '🟢', 'medium': '🟡', 'high': '🔴'}
    lines = [
        f'  {len(TOOLS)} built-in tools.  {DIM}MCP tools are listed under MCP Servers.{RESET}',
        f'  {DIM}Risk decides what each permission mode auto-approves.{RESET}',
        '',
    ]
    for tool in TOOLS:
        name = tool['name']
        # run_command is the one tool whose tier is decided per call, from the
        # command itself -- a table lookup here would print a tier that never
        # actually applies. Everything else falls back to the table, whose
        # default is "high", not "unknown".
        if name == 'run_command':
            risk, note = 'varies', ' 🟢 read-only · 🔴 anything mutating'
        else:
            risk, note = RISK_LEVELS.get(name, 'high'), ''
        lines.append(f'  {icons.get(risk, "⚪")} {BOLD}{name}{RESET} {DIM}({risk}){RESET}{DIM}{note}{RESET}')
        lines.append(f'     {tool["description"]}')
        props = tool['input_schema'].get('properties', {})
        if props:
            lines.append(f'     {DIM}Params: {", ".join(props.keys())}{RESET}')
        lines.append('')
    show_info_page('Tools', lines)


def _budget_header(agent_mod, breakdown, budget) -> list:
    """The live breakdown, drawn above the choices that change it.

    Rendered by `agent.render_budget` rather than re-derived here: the whole
    point of this page is that the numbers are the ones the turn actually
    uses, and a second implementation would drift from the first within a
    release. This function only frames what that returns.
    """
    auto = ' (auto)' if budget.profile.name != _budget_settings_name(agent_mod) else ''
    lines = [
        f'  {BOLD}{budget.profile.label}{RESET}{DIM}{auto} · '
        f'{breakdown.window:,} token window{RESET}',
        f'  {DIM}{budget.profile.summary}{RESET}',
        '',
    ]
    lines.extend(agent_mod.render_budget(breakdown))
    lines.append('')
    return lines


def _budget_settings_name(agent_mod) -> str:
    try:
        return agent_mod.budget_settings().profile
    except Exception:
        return 'auto'


def _budget_pick_preset(agent_mod, settings):
    """Choose a preset, or let the window choose one."""
    import dataclasses

    import core.budget as cb
    window = agent_mod.CONTEXT_WINDOW or agent_mod.DEFAULT_CONTEXT_WINDOW
    auto = cb.auto_profile(window)
    options = [
        f'  {"Auto":<10}{DIM}— follow the model: {auto.label} for '
        f'{window:,} tokens{RESET}',
    ]
    for name in ('economy', 'balanced', 'full'):
        p = cb.PRESETS[name]
        marker = f'{GREEN}◈{RESET}' if settings.profile == name else ' '
        options.append(f'{marker} {p.label:<10}{DIM}— {p.summary}{RESET}')
    idx = arrow_menu('Preset', options, footer=DEFAULT_FOOTER)
    if idx < 0:
        return settings
    chosen = 'auto' if idx == 0 else ('economy', 'balanced', 'full')[idx - 1]
    return dataclasses.replace(settings, profile=chosen)


def _budget_set_number(settings, field: str, label: str, current: int,
                       derived: bool):
    """Set a numeric override, or hand the field back to the profile."""
    import dataclasses
    note = 'derived from the window' if derived else 'set by hand'
    raw = prompt_text(f'{label} (currently {current:,}, {note}) — '
                      f'a number, or "auto"')
    if not raw:
        return settings
    if raw.strip().lower() == 'auto':
        return dataclasses.replace(settings, **{field: None})
    try:
        return dataclasses.replace(settings, **{field: max(0, int(raw))})
    except ValueError:
        show_info_page('Not a number', [f'  {RED}✗{RESET}  {raw!r} is not a number.'])
        return settings


def _budget_browse_tools(agent_mod, settings):
    """Turn individual tools and whole MCP servers on or off.

    Servers first, because switching one off is the move that actually
    recovers tokens — a single MCP server here publishes dozens of tools, and
    disabling them one at a time is not a workflow anyone will finish.
    """
    import dataclasses

    import core.budget as cb
    while True:
        pool = agent_mod.ALL_TOOLS or agent_mod.COMBINED_TOOLS or agent_mod.TOOLS
        budget = agent_mod.active_budget()
        groups: dict = {}
        for tool in pool:
            name = tool.get('name', '')
            server = agent_mod._server_of(name) or 'built-in'
            groups.setdefault(server, []).append(name)

        rows, actions = [], []
        for server in sorted(groups):
            names = groups[server]
            off = server in budget.disabled_servers
            if server == 'built-in':
                # Built-ins are never sent to the chopping block: read, write,
                # edit and run are what the agent *is*, and a budget screen
                # that let you disable them would be offering to break it.
                rows.append(f'  {DIM}{server:<28}{len(names):>3} tools · '
                            f'always on{RESET}')
                actions.append(None)
                continue
            cost = agent_mod.estimate_tool_tokens(
                [t for t in pool if t.get('name') in names])
            mark = f'{RED}✕{RESET}' if off else f'{GREEN}✓{RESET}'
            state = f'{DIM}disabled{RESET}' if off else f'~{cost:,} tok'
            rows.append(f'{mark} {server:<28}{len(names):>3} tools · {state}')
            actions.append(('server', server))
        rows.append('')
        actions.append(None)
        disabled_count = len(budget.disabled_tools)
        rows.append(f'  {DIM}Individual tools…{RESET}'
                    + (f' {DIM}({disabled_count} disabled){RESET}'
                       if disabled_count else ''))
        actions.append(('individual', ''))

        idx = arrow_menu('Tools & servers', rows, footer=DEFAULT_FOOTER)
        if idx < 0 or idx >= len(actions) or actions[idx] is None:
            return settings
        kind, value = actions[idx]
        if kind == 'server':
            settings = cb.toggle_server(settings, value)
            agent_mod.save_budget_settings(settings)
        else:
            settings = _budget_browse_individual(agent_mod, settings)


def _budget_browse_individual(agent_mod, settings):
    import core.budget as cb
    while True:
        pool = agent_mod.ALL_TOOLS or agent_mod.COMBINED_TOOLS or agent_mod.TOOLS
        budget = agent_mod.active_budget()
        builtin = {t.get('name') for t in agent_mod.TOOLS}
        rows, names = [], []
        for tool in sorted(pool, key=lambda t: t.get('name', '')):
            name = tool.get('name', '')
            if name in builtin:
                continue
            off = name in budget.disabled_tools
            mark = f'{RED}✕{RESET}' if off else f'{GREEN}✓{RESET}'
            cost = agent_mod.estimate_tool_tokens([tool])
            rows.append(f'{mark} {name:<44}{DIM}~{cost:,} tok{RESET}')
            names.append(name)
        if not rows:
            show_info_page('No MCP tools', [
                '  No MCP tools are connected, so there is nothing to disable.',
                '',
                f'  {DIM}Built-in tools are always on.{RESET}'])
            return settings
        idx = arrow_menu('Individual tools', rows, footer=DEFAULT_FOOTER)
        if idx < 0 or idx >= len(names):
            return settings
        settings = cb.toggle_tool(settings, names[idx])
        agent_mod.save_budget_settings(settings)


def page_context_budget():
    """Show where the context window goes, and change it.

    This page exists because nothing showed the user the composition of a
    turn. Measured on a 32,768-token local model: tool schemas 18,079 tokens,
    output reserve 8,192, system prompt 3,353 — 29,625 of fixed cost against a
    24,576 compaction trigger, so compaction fired on the first message with an
    empty history and could never clear. Every one of those numbers was
    available to the program and none of them was available to the person
    paying for them.
    """
    import agent as agent_mod
    import core.budget as cb

    while True:
        try:
            settings = agent_mod.budget_settings(refresh=True)
            breakdown = agent_mod.budget_breakdown()
            budget = agent_mod.active_budget()
        except Exception as exc:
            show_info_page('Context Budget', [
                f'  {RED}✗{RESET}  Could not read the budget: {exc}'])
            return

        rows = [
            f'  Preset             {BOLD}{budget.profile.label}{RESET}'
            + (f'{DIM}  (following the model){RESET}'
               if settings.profile not in cb.PRESETS else ''),
            f'  Tool ceiling       {BOLD}{budget.tool_ceiling}{RESET}'
            f'{DIM}  {"set by hand" if budget.tool_ceiling_is_manual else "from the window"}'
            f' · {breakdown.tools_sent} of {breakdown.tools_available} sent{RESET}',
            f'  Output reserve     {BOLD}{budget.output_reserve:,}{RESET}'
            f'{DIM}  {"set by hand" if budget.output_reserve_is_manual else "from the window"}{RESET}',
            f'  Tools & servers    {DIM}enable or disable individually{RESET}',
            f'  Auto-compact at    {BOLD}{cb.compaction_percent_label(settings)}'
            f'{RESET}{DIM}  when to summarise the conversation{RESET}',
            '',
        ]
        actions = ['preset', 'tools', 'output', 'browse', 'compact', None]
        for spec in cb.SECTIONS:
            key = spec['key']
            on = budget.allows(key)
            mark = f'{GREEN}✓{RESET}' if on else f'{RED}✕{RESET}'
            guard = (f' {YELLOW}·{RESET}{DIM} self-improvement{RESET}'
                     if spec['always_on'] else '')
            rows.append(f'{mark} {spec["label"]:<24}{DIM}{spec["detail"]}{RESET}{guard}')
            actions.append(('section', key))
        rows.append('')
        actions.append(None)
        rows.append(f'  {DIM}Reset to defaults{RESET}')
        actions.append('reset')

        idx = arrow_menu('Context Budget', rows,
                         header_lines=_budget_header(agent_mod, breakdown, budget),
                         footer=DEFAULT_FOOTER, max_visible=len(rows))
        if idx < 0 or idx >= len(actions) or actions[idx] is None:
            return
        action = actions[idx]

        if action == 'preset':
            settings = _budget_pick_preset(agent_mod, settings)
        elif action == 'tools':
            settings = _budget_set_number(
                settings, 'tool_ceiling', 'Tool ceiling',
                budget.tool_ceiling, not budget.tool_ceiling_is_manual)
        elif action == 'output':
            settings = _budget_set_number(
                settings, 'output_reserve', 'Output reserve (tokens)',
                budget.output_reserve, not budget.output_reserve_is_manual)
        elif action == 'browse':
            settings = _budget_browse_tools(agent_mod, settings)
        elif action == 'compact':
            settings = _budget_pick_compaction(agent_mod, settings)
        elif action == 'reset':
            settings = cb.Settings()
        else:
            _kind, key = action
            window = agent_mod.CONTEXT_WINDOW or agent_mod.DEFAULT_CONTEXT_WINDOW
            was_on = budget.allows(key)
            if was_on and key in cb.ALWAYS_ON and not _confirm_disable_learning(key):
                continue
            settings = cb.toggle_section(settings, key, window)
        agent_mod.save_budget_settings(settings)


def page_settings():
    """Feature switches — what the agent does, not what it spends.

    Deliberately separate from Context Budget, which answers a different
    question (how much of the window may something occupy) in a different
    unit. Merging them would put "streaming on/off" next to "tool ceiling:
    32" and leave the user to work out which numbers move together.
    """
    import agent as agent_mod
    import core.features as cf

    while True:
        current = agent_mod.features(refresh=True)
        rows, keys = [], []
        for spec in cf.FEATURES:
            on = current.enabled(spec['key'])
            mark = f'{GREEN}✓{RESET}' if on else f'{RED}✕{RESET}'
            rows.append(f'{mark} {spec["label"]:<22}{DIM}{spec["detail"]}{RESET}')
            keys.append(spec['key'])
        # Numeric settings sit below the switches, each showing its current
        # value. One that depends on a switch says so when that switch is off,
        # rather than silently having no effect.
        for spec in cf.CHOICES:
            value = current.choice(spec['key'])
            gate = spec.get('depends_on')
            inert = gate and not current.enabled(gate)
            label = f'{spec["label"]}: {value:,} {spec["unit"]}'
            note = (f'{DIM} — needs "{next(f["label"] for f in cf.FEATURES if f["key"] == gate)}" on{RESET}'
                    if inert else f'{DIM}{spec["detail"]}{RESET}')
            mark = f'{DIM}◦{RESET}' if inert else f'{CYAN}#{RESET}'
            rows.append(f'{mark} {label:<22}{note}')
            keys.append(('choice', spec['key']))
        rows.append('')
        keys.append(None)
        rows.append(f'  {DIM}Reset to defaults{RESET}')
        keys.append('__reset__')

        idx = arrow_menu(
            'Settings', rows,
            header_lines=[
                f'  {DIM}Enter toggles the highlighted switch. These persist '
                f'across sessions{RESET}',
                f'  {DIM}and models — unlike the context budget, which follows '
                f'the model.{RESET}',
                '─' * 58,
            ],
            footer=DEFAULT_FOOTER, max_visible=len(rows))
        if idx < 0 or idx >= len(keys) or keys[idx] is None:
            return
        chosen = keys[idx]
        if chosen == '__reset__':
            agent_mod.save_features(cf.Features())
            continue
        if isinstance(chosen, tuple):
            agent_mod.save_features(
                _settings_pick_value(agent_mod, current, chosen[1]))
            continue
        agent_mod.save_features(cf.toggle(current, chosen))


def _settings_pick_value(agent_mod, current, key):
    """Pick one value for a numeric setting. Returns the new Features."""
    import core.features as cf

    spec = next(c for c in cf.CHOICES if c['key'] == key)
    now = current.choice(key)
    window = agent_mod.CONTEXT_WINDOW or agent_mod.DEFAULT_CONTEXT_WINDOW
    rows = []
    for value in spec['values']:
        mark = f'{GREEN}●{RESET}' if value == now else f'{DIM}○{RESET}'
        # The share is what makes the number a decision: 100,000 tokens means
        # nothing until you know it is half your window, or most of it.
        share = f'{DIM}  {value / window:.0%} of the {window:,} window{RESET}' \
            if window else ''
        rows.append(f'{mark} {value:>7,} {spec["unit"]}{share}')

    idx = arrow_menu(spec['label'], rows,
                     header_lines=[f'  {DIM}{spec["detail"]}{RESET}',
                                   '─' * 50],
                     footer=DEFAULT_FOOTER)
    if idx < 0 or idx >= len(spec['values']):
        return current
    return cf.set_choice(current, key, spec['values'][idx])


def _budget_pick_compaction(agent_mod, settings):
    """Choose when the conversation is summarised, or switch it off.

    The window percentage is shown next to each choice in tokens, because
    "90%" is only a decision the user can make if they can see what 90% of
    *their* model is — the same reason the rest of this page shows numbers
    rather than shares.
    """
    import core.budget as cb

    window = agent_mod.CONTEXT_WINDOW or agent_mod.DEFAULT_CONTEXT_WINDOW
    rows, values = [], []
    for percent, label, detail in cb.COMPACTION_CHOICES:
        current = f'{GREEN}●{RESET}' if percent == settings.compact_at_percent \
            else f'{DIM}○{RESET}'
        if percent:
            at = f'{DIM}  ≈ {int(window * percent / 100):,} tokens{RESET}'
        elif percent is None:
            at = (f'{DIM}  ≈ '
                  f'{int(window * cb.core_context.DEFAULT_FIT_FRACTION):,} '
                  f'tokens{RESET}')
        else:
            at = ''
        rows.append(f'{current} {label:<24}{DIM}{detail}{RESET}{at}')
        values.append(percent)

    idx = arrow_menu(
        'Automatic compaction',
        rows,
        header_lines=[
            f'  {DIM}When the request reaches this share of the '
            f'{window:,}-token window, the{RESET}',
            f'  {DIM}conversation so far is replaced by a summary. '
            f'{RESET}{CYAN}/compact{RESET}{DIM} always works,{RESET}',
            f'  {DIM}whatever is chosen here.{RESET}',
            '─' * 50,
        ],
        footer=DEFAULT_FOOTER)
    if idx < 0 or idx >= len(values):
        return settings
    return cb.set_compact_at(settings, values[idx])


def _confirm_disable_learning(key: str) -> bool:
    """Ask before switching off a part of the self-improving loop.

    No *preset* may disable these (core.budget enforces that), but the user
    may — and should be told what it costs rather than discovering later that
    the agent stopped remembering. This is the one toggle on the page that
    changes what the program is rather than what it spends.
    """
    label = {'learned_facts': 'retrieved facts',
             'standing_rules': 'standing rules'}.get(key, key)
    idx = arrow_menu(
        f'Turn off {label}?',
        [f'  {DIM}Keep it on (recommended){RESET}',
         f'  {RED}Turn it off{RESET}  {DIM}— the agent stops applying what it '
         f'has learned{RESET}'],
        header_lines=[
            f'  {YELLOW}⚠{RESET}  This is part of the self-improving loop.',
            f'  {DIM}It costs 0 tokens until something has actually been '
            f'learned, and{RESET}',
            f'  {DIM}what it costs after that is what the agent knows about '
            f'you.{RESET}',
            '',
        ],
        footer=DEFAULT_FOOTER)
    return idx == 1


def page_mcps():
    """Interactive MCP server management page with real connection status."""
    from mcp_manager import (
        read_mcp_servers, write_mcp_server, remove_mcp_server,
        is_server_disabled, set_server_disabled,
        get_server_env, cmd_mcp_env, test_mcp_connections,
    )

    # Connection status is expensive (it starts every configured server), so
    # the page never waits for it: a fresh result is reused, otherwise the
    # probe runs on a background thread and the menu draws immediately with
    # `[testing…]`. This page used to block for 23.3 s before its first row.
    import net_probe

    MCP_STATUS_KEY, MCP_STATUS_TTL = 'mcp_status', 90.0
    probing = {'running': False}

    def start_probe():
        if probing['running']:
            return
        probing['running'] = True

        def work():
            try:
                net_probe.put(MCP_STATUS_KEY, test_mcp_connections())
            except Exception:
                net_probe.put(MCP_STATUS_KEY, {})
            finally:
                probing['running'] = False

        threading.Thread(target=work, daemon=True).start()

    fresh, test_results = net_probe.peek(MCP_STATUS_KEY, MCP_STATUS_TTL)
    if not fresh:
        test_results = {}
        start_probe()

    while True:
        # Pick up whatever the background probe has finished by now.
        got, latest = net_probe.peek(MCP_STATUS_KEY, MCP_STATUS_TTL)
        if got:
            test_results = latest

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
            if probing['running']:
                status_parts.append(f'{DIM}testing…{RESET}')
            status_str = ', '.join(status_parts) or f'{DIM}not tested yet{RESET}'
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
            print(f'Connecting to {len(servers)} server(s) in parallel...')
            print()
            sys.stdout.flush()
            net_probe.invalidate(MCP_STATUS_KEY)
            test_results = test_mcp_connections()
            net_probe.put(MCP_STATUS_KEY, test_results)
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
                      footer=DEFAULT_FOOTER)
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

    # warn=True: this page exists to report current state, including any
    # frontmatter problems, so it always rescans rather than reading the
    # per-turn cache other callers use.
    all_skills = discover_skills(warn=True)
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


def _launch_agent(session_id: str = ""):
    """Launch the agent, optionally continuing a session."""
    clear_screen()
    print(f'{BOLD}Starting TOMAS Agent{RESET}')
    print('─' * 50)
    print("Type 'quit' or 'exit' to leave. Esc Esc also exits.")
    print()

    import agent as agent_mod

    # Set session to continue if specified
    if session_id:
        agent_mod.CONTINUE_SESSION_ID = session_id

    agent_mod.main()


def _format_message_content(content, max_len: int = 100) -> str:
    """Format a message's content for display — handles str, list of blocks, etc."""
    if isinstance(content, str):
        return content[:max_len].replace('\n', ' ')
    elif isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("type", "")
                if t == "text":
                    texts.append(block.get("text", "")[:max_len])
                elif t == "tool_use":
                    texts.append(f"[tool: {block.get('name', '?')}]")
                elif t == "tool_result":
                    texts.append("[result]")
            elif hasattr(block, "type"):
                t = getattr(block, "type", "")
                if t == "text":
                    texts.append(getattr(block, "text", "")[:max_len])
                elif t == "tool_use":
                    texts.append(f"[tool: {getattr(block, 'name', '?')}]")
                elif t == "tool_result":
                    texts.append("[result]")
        combined = " ".join(texts)
        return combined[:max_len] if combined else str(content)[:max_len]
    return str(content)[:max_len]


def _show_full_conversation(data: dict) -> None:
    """Display the full conversation from a session using show_info_page."""
    msgs = data.get("messages", [])
    if not msgs:
        show_info_page("Full Conversation", ["  (no messages)"])
        return

    lines = []
    lines.append(f'  Total: {len(msgs)} messages  ·  {data.get("project", "?")}  ·  {data.get("timestamp_str", "?")}')
    lines.append('')
    for i, m in enumerate(msgs):
        role = m.get("role", "?")
        content = m.get("content", "")
        icon = '◆' if role == 'user' else '▌' if role == 'assistant' else '·'
        formatted = _format_message_content(content, max_len=120)
        lines.append(f'  [{i+1}] {icon} {DIM}{role}{RESET}: {formatted}')
        # If content is a list of tool results, show them expanded
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    result_text = str(block.get("content", ""))[:80].replace('\n', ' ')
                    lines.append(f'       {DIM}↳ result: {result_text}{RESET}')
                elif hasattr(block, "type") and getattr(block, "type", "") == "tool_result":
                    result_text = str(getattr(block, "content", ""))[:80].replace('\n', ' ')
                    lines.append(f'       {DIM}↳ result: {result_text}{RESET}')

    show_info_page(f'Full Conversation ({len(msgs)} messages)', lines)


def _session_detail(sid: str) -> None:
    """Show session detail and allow continue/delete actions."""
    while True:
        data = load_session(sid)
        if data is None:
            show_info_page('Error', [f'  Could not load session.'])
            return

        ts = data.get("timestamp_str", "?")
        proj = data.get("project", "?")
        model = data.get("model", "?")
        msgs = data.get("message_count", 0)
        summary = data.get("summary", "")
        tokens = data.get("token_usage", {})

        # Build detail header lines
        lines = [
            f'  {DIM}ID:{RESET}      {CYAN}{sid}{RESET}',
            f'  {DIM}When:{RESET}    {ts}',
            f'  {DIM}Project:{RESET} {GREEN}{proj}{RESET}',
            f'  {DIM}Model:{RESET}   {model}',
            f'  {DIM}Messages:{RESET} {msgs}',
            f'  {DIM}Tokens:{RESET}  {tokens.get("input", 0):,} in \u00b7 {tokens.get("output", 0):,} out ({tokens.get("calls", 0)} calls)',
        ]
        if summary:
            lines.append('')
            lines.append(f'  {DIM}Summary:{RESET}')
            lines.append(f'  {DIM}{summary[:120]}{RESET}')

        # Full conversation preview — all messages (truncated for display)
        msgs_list = data.get("messages", [])
        if msgs_list:
            lines.append('')
            lines.append(f'  {BOLD}Conversation ({len(msgs_list)} messages):{RESET}')
            # Show last 5 messages as preview
            preview_count = min(5, len(msgs_list))
            if len(msgs_list) > preview_count:
                lines.append(f'  {DIM}  (… {len(msgs_list) - preview_count} earlier messages …){RESET}')
            for m in msgs_list[-preview_count:]:
                role = m.get("role", "?")
                content = m.get("content", "")
                if isinstance(content, list):
                    # Extract text from block list
                    texts = []
                    for block in content:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                texts.append(block.get("text", "")[:60])
                            elif block.get("type") == "tool_use":
                                texts.append(f"[tool: {block.get('name', '?')}]")
                            elif block.get("type") == "tool_result":
                                texts.append("[result]")
                        elif hasattr(block, "type"):
                            if getattr(block, "type", "") == "text":
                                texts.append(getattr(block, "text", "")[:60])
                            elif getattr(block, "type", "") == "tool_use":
                                texts.append(f"[tool: {getattr(block, 'name', '?')}]")
                            elif getattr(block, "type", "") == "tool_result":
                                texts.append("[result]")
                    content = " ".join(texts)[:100]
                elif isinstance(content, str):
                    content = content[:100].replace('\n', ' ')
                icon = f'{GREEN}◆{RESET}' if role == 'user' else f'{MAGENTA}▌{RESET}'
                lines.append(f'  {icon} {DIM}{role}{RESET}: {content}')

        header = [
            f'  {BOLD}Session Detail{RESET}',
            '─' * 50,
        ] + lines + ['']

        actions = [
            f'  {CYAN}▶{RESET}  Continue this session',
            f'  {CYAN}📋{RESET}  View full conversation ({len(msgs_list)} messages)',
            f'  {RED}✕{RESET}  Delete this session',
            '',
            '  ← Back to list',
        ]

        idx = arrow_menu('', actions, header_lines=header,
                         footer='↑↓ navigate · Enter select · Esc back')

        if idx < 0 or idx == len(actions) - 1:
            return

        if idx == 0:  # Continue
            _launch_agent(session_id=sid)
            return  # after agent exits, go back to session list

        if idx == 1:  # View full conversation
            _show_full_conversation(data)
            continue  # refresh the detail view

        if idx == 2:  # Delete
            header_confirm = [
                f'  {BOLD}Delete Session{RESET}',
                '─' * 50,
                f'  Delete this session?',
                '',
            ]
            confirm_items = ['  Yes, delete', '  No, cancel']
            cidx = arrow_menu('', confirm_items, header_lines=header_confirm,
                              footer='Enter select · Esc cancel')
            if cidx == 0:
                delete_session(sid)
                show_info_page('Done', [f'  ✓ Session deleted.'])
            return


def page_sessions():
    """Interactive session browser."""
    while True:
        sessions = list_sessions(limit=20)
        total = get_session_count()

        # Build menu items: numbered sessions first, then actions
        items = []
        session_indices = {}  # maps menu index → session index

        if not sessions:
            items.append('  (no sessions saved yet)')
        else:
            for i, s in enumerate(sessions):
                num = i + 1
                ts = s.get("timestamp_str", "?")
                proj = s.get("project", "?")
                msgs = s.get("message_count", 0)
                summary = s.get("summary", "")[:55]

                # Single-line session entry
                label = f'  {GREEN}{num:>2}.{RESET} {DIM}{ts}{RESET}  {CYAN}{proj}{RESET}  {DIM}({msgs} msgs){RESET}'
                if summary:
                    label += f'\n      {DIM}{summary}{RESET}'
                items.append(label)
                session_indices[len(items) - 1] = i  # track session index

        # Separator + session actions
        items.append('')
        if sessions:
            items.append(f'  {BOLD}─ Actions ─{RESET}')
            items.append(f'  {YELLOW}▶{RESET}  Continue latest session')
            items.append(f'  {DIM}⟳{RESET}  Refresh list')
            items.append(f'  {RED}✕{RESET}  Delete all sessions')

        items.append('')
        items.append('  ← Back to main menu')

        header = [
            f'  {BOLD}Sessions{RESET}{DIM}  ({total} total){RESET}',
            '─' * 50,
        ]

        idx = arrow_menu('', items, header_lines=header,
                         footer='↑↓ navigate · Enter select · Esc back')

        if idx < 0 or idx == len(items) - 1:  # Back
            return

        # No sessions: only "Back" is available
        # Check if a session entry was selected
        if idx in session_indices:
            s = sessions[session_indices[idx]]
            sid = s.get("id", "")
            if sid:
                _session_detail(sid)
            continue

        # Calculate action indices (after sessions list)
        base = len(sessions) + 2  # sessions + blank line
        continue_latest_idx = base
        refresh_idx = base + 1
        delete_all_idx = base + 2

        if idx == continue_latest_idx:
            latest = sessions[0]
            sid = latest.get("id", "")
            if sid:
                _launch_agent(session_id=sid)
            continue

        if idx == refresh_idx:
            continue  # loop will refresh

        if idx == delete_all_idx:
            header_confirm = [
                f'  {BOLD}Delete All Sessions{RESET}',
                '─' * 50,
                f'  This will delete all {total} sessions permanently.',
                '',
            ]
            confirm_items = ['  Yes, delete everything', '  No, cancel']
            cidx = arrow_menu('', confirm_items, header_lines=header_confirm,
                              footer='Enter select · Esc cancel')
            if cidx == 0:
                cleared = clear_all_sessions()
                show_info_page('Done', [f'  ✓ {cleared} session(s) deleted.'])
            continue


def _page_view_instructions(title: str, subtitle: str, path):
    """Show an instructions file, with [e] to open it in the system editor.

    Both instruction pages differ only in which file they point at. The titles
    say what the file *governs* -- the filenames AGENT.md and AGENTS.md differ
    by one character and told the reader nothing about the difference.

    Rendered through `show_info_page` rather than by printing and calling
    `msvcrt.getch()`, which this did and which cost it both of the bugs that
    function was written to fix: an instructions file longer than the terminal
    scrolled off the top with no way back, and an arrow key pressed here left
    its second byte in the buffer for the *next* menu to read as a phantom
    keypress. A file the user is told to keep their standing rules in is
    exactly the file most likely to outgrow one screen.
    """
    if path.exists():
        body = path.read_text(encoding="utf-8").strip()
        content = body.split('\n') if body else [f'  {DIM}(file is empty){RESET}']
    else:
        content = [f'  {DIM}(not created yet — press [e] to start it){RESET}']

    lines = [
        f'{DIM}{subtitle}{RESET}',
        f'{DIM}{path}{RESET}',
        '─' * 50,
        *content,
    ]
    key = show_info_page(title, lines,
                         prompt='Press any key to go back, or [e] to edit',
                         accept=('e', 'E'))
    if key in ('e', 'E'):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text('', encoding="utf-8")
            os.startfile(path)
        except Exception as e:
            show_info_page('Could Not Open Editor', [f'  {RED}✗{RESET} {e}'])


def page_edit_instructions():
    """View/edit the global agent instructions."""
    _page_view_instructions(
        'Agent Instructions',
        'Applies to every project on this machine.',
        TOMAS_DIR / "instructions" / "AGENT.md",
    )


def page_edit_project_agent():
    """View/edit the project-level guidelines."""
    _page_view_instructions(
        'Project Guidelines',
        f'Applies only to {AGENT_PROJECT_DIR.name}.',
        AGENT_PROJECT_DIR / "AGENTS.md",
    )


def page_configure_provider():
    """Arrow-key menu to select and configure a provider."""
    configured = set(_get_configured_providers())

    provider_names = [
        'OpenRouter (openrouter.ai)',
        'Anthropic Direct (api.anthropic.com)',
        'OpenAI (api.openai.com)',
        'Google AI',
        'OpenCode Zen (opencode.ai)',
        'Ollama (local)',
        'Groq (console.groq.com)',
        'Custom / Other',
    ]
    # index -> provider type, matched to provider_names by position. Kept as
    # a parallel list rather than folded into provider_names itself so the
    # idx==N dispatch below — and every provider_names[N] reference inside
    # it — stays on the original, stable indices even though the menu below
    # shows only a filtered subset of them.
    provider_name_types = ['openrouter', 'anthropic', 'openai', 'google',
                           'zen', 'ollama', 'groq', 'custom']
    # Probe for a local Ollama so the option can say whether it is there.
    # Cached: without it this page paid 12.3 s on every open, because a
    # missing Ollama meant three HTTP attempts that each ran to their timeout.
    import net_probe
    import provider_manager

    # Restricted to the current working set (see
    # provider_manager.VISIBLE_PROVIDER_TYPES) — temporary and reversible,
    # not a removal: every other provider stays fully wired below, it just
    # does not appear in this menu for now.
    visible_indices = [i for i, t in enumerate(provider_name_types)
                       if t in provider_manager.VISIBLE_PROVIDER_TYPES]

    def _probe_ollama() -> list[str]:
        try:
            return provider_manager.list_models(provider_manager.Provider(
                name='Ollama (local)', type='ollama',
                base_url=provider_manager.OLLAMA_DEFAULT_URL))
        except Exception:
            return []

    ollama_models = net_probe.cached('ollama_models', 30.0, _probe_ollama)

    # Show ✓ for already-configured providers
    display = []
    for i in visible_indices:
        p = provider_names[i]
        suffix = ''
        if p == 'Ollama (local)':
            suffix = (f'  {DIM}— {len(ollama_models)} model(s) detected{RESET}'
                      if ollama_models else f'  {DIM}— not running{RESET}')
        if p in configured:
            display.append(f'{GREEN}✓{RESET} {p}{suffix}')
        else:
            display.append(f'  {p}{suffix}')

    sel = arrow_menu('Connect / Configure Provider', display,
                     footer=DEFAULT_FOOTER)
    if sel < 0:
        return
    idx = visible_indices[sel]   # back to the original, stable index

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
            import provider_manager as _pm
            update_dotenv("GOOGLE_API_KEY", key)
            _save_provider_config(
                provider_names[3],
                {"GOOGLE_API_KEY": key,
                 # Saved explicitly so the provider is usable, not merely
                 # recorded. It used to store the key alone and then say so:
                 # "Google AI is saved but the agent uses the ANTHROPIC_* env
                 # vars for API calls" — a configured provider that could not
                 # be called, with the disclaimer standing in for the feature.
                 "ANTHROPIC_BASE_URL": _pm.GOOGLE_OPENAI_BASE,
                 "ANTHROPIC_API_KEY": key,
                 "ANTHROPIC_EXTRA_HEADERS": ""},
                provider_type="google"
            )
            import net_probe
            net_probe.invalidate('google_catalog')
            catalog = _pm.google_model_catalog(key)
            if catalog:
                top = catalog[0]
                update_dotenv("AGENT_MODEL", top['name'])
                show_info_page('Connected to Google AI', [
                    f'  {GREEN}✓{RESET} API key set',
                    f'  {GREEN}✓{RESET} Endpoint: {_pm.GOOGLE_OPENAI_BASE}',
                    f'  {GREEN}✓{RESET} Model: {top["name"]} '
                    f'({top["context_window"]:,} ctx)',
                    '',
                    f'  {DIM}{len(catalog)} models are reachable with this key '
                    f'— pick another from "Change Model".{RESET}',
                ])
            else:
                show_info_page('⚠ Google AI', [
                    f'  {YELLOW}The key was saved, but Google returned no '
                    f'models for it.{RESET}',
                    '',
                    f'  {DIM}Check it at https://aistudio.google.com/apikey{RESET}',
                ])
    elif idx == 4:  # OpenCode Zen — auto-start proxy, no config needed
        _zen_setup_proxy()
        # Also save to multi-provider config
        import zen_catalog
        config = _load_providers_config()
        if "providers" not in config:
            config["providers"] = {}
        config["providers"][provider_names[4]] = {
            "type": "zen",
            "model": (os.environ.get("AGENT_MODEL", "")
                      or zen_catalog.default_free_model())
        }
        config["active"] = provider_names[4]
        _save_providers_config(config)
    elif idx == 5:  # Ollama (local)
        if not ollama_models:
            import shutil
            has_binary = bool(shutil.which("ollama"))
            started = False
            if has_binary:
                print(f'  {DIM}Ollama is installed but not running — starting it...{RESET}')
                started = _try_start_ollama_server()
                if started:
                    net_probe.invalidate('ollama_models')
                    ollama_models = net_probe.cached(
                        'ollama_models', 30.0, _probe_ollama)
            if not ollama_models:
                lines = [
                    f'  No Ollama server answered at {provider_manager.OLLAMA_DEFAULT_URL}.',
                    '']
                if started:
                    # The server came up (reachable) but reported no models —
                    # a different situation from "Ollama isn't installed",
                    # and the fix is a pull, not serve.
                    lines += [
                        '  It started, but has no models pulled yet:',
                        f'    {CYAN}ollama pull qwen2.5-coder{RESET}',
                        '',
                        '  Then come back here — it will be detected automatically.']
                elif has_binary:
                    lines += [
                        '  Found the ollama binary, but the server did not come up '
                        'in time.',
                        f'    {CYAN}ollama serve{RESET}',
                        '',
                        '  Then come back here — it will be detected automatically.']
                else:
                    lines += [
                        '  Install from https://ollama.com, then:',
                        f'    {CYAN}ollama pull qwen2.5-coder{RESET}',
                        f'    {CYAN}ollama serve{RESET}',
                        '',
                        '  Then come back here — it will be detected automatically.']
                show_info_page('Ollama not found', lines)
                return
        m_idx = arrow_menu('Ollama — choose a model',
                           [f'  {m}' for m in ollama_models],
                           footer=DEFAULT_FOOTER)
        if m_idx < 0:
            return
        model = ollama_models[m_idx]
        provider = provider_manager.Provider(
            name=provider_names[5], type='ollama',
            base_url=provider_manager.OLLAMA_DEFAULT_URL,
            model=model,
            env={'ANTHROPIC_BASE_URL': provider_manager.OLLAMA_DEFAULT_URL,
                 'ANTHROPIC_API_KEY': 'ollama'})
        # Probe now: many local models have no tool support, and the context
        # window is usually far smaller than the cloud default. Both must be
        # known before the first turn, not discovered during it.
        caps = provider_manager.probe(provider)
        provider.capabilities = caps
        provider_manager.save(provider)
        provider_manager.activate(provider.name)
        reinit_client()
        lines = [f'  ✓ Ollama configured and active — {model}.', '']
        lines.append(f'  Context window: {caps.context_window:,} tokens')
        lines.append(f'  Tool use:       {"yes" if caps.tool_use else "no — text protocol fallback"}')
        lines.append(f'  Streaming:      {"yes" if caps.streaming else "no — blocking fallback"}')
        lines.append(f'  Vision:         {"yes" if caps.vision else "no"}')
        show_info_page('Done', lines)
    elif idx == 6:  # Groq
        key = prompt_text('Enter Groq API key (gsk_...)')
        if key:
            update_dotenv("ANTHROPIC_API_KEY", key)
            update_dotenv("ANTHROPIC_BASE_URL", "https://api.groq.com/openai/v1")
            update_dotenv("ANTHROPIC_EXTRA_HEADERS", "")
            _save_provider_config(
                provider_names[6],
                {"ANTHROPIC_API_KEY": key,
                 "ANTHROPIC_BASE_URL": "https://api.groq.com/openai/v1",
                 "ANTHROPIC_EXTRA_HEADERS": ""},
                provider_type="groq"
            )
            reinit_client()
            show_info_page('Done', ['  ✓ Groq configured and active.',
                                    '',
                                    f'  {DIM}Use "Choose Model" to see every model this '
                                    f'key can reach.{RESET}'])
    elif idx == 7:  # Custom
        name = prompt_text('Provider name')
        key = prompt_text('API key')
        base = prompt_text('Base URL')
        if key and base and name:
            env_key = f"{name.upper().replace(' ', '_')}_API_KEY"
            env_url = f"{name.upper().replace(' ', '_')}_BASE_URL"
            update_dotenv(env_key, key)
            update_dotenv(env_url, base)
            # Type is sniffed once, here, and written down. Nothing sniffs at
            # runtime; an unrecognised endpoint is "custom", which works.
            _save_provider_config(
                name,
                {env_key: key, env_url: base,
                 "ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": base},
                provider_type=provider_manager.detect_type(base)
            )
            show_info_page('Done', [f'  ✓ {name} configured and active.'])


def page_configure_zen():
    """Sub-menu for OpenCode Zen configuration.

    Connecting directly is the recommended path: `openai_adapter` builds the
    dynamic `x-opencode-*` headers in-process, so the standalone proxy buys
    nothing here. It is kept only for pointing *other* tools at Zen.
    """
    opts = [
        f'  🔑  Connect to Zen  {DIM}(recommended){RESET}',
        f'  ℹ️   About OpenCode Zen',
        '',
        f'  🚀  Start local proxy  {DIM}(only needed by other tools){RESET}',
        '  ◀   Back to providers',
    ]
    actions = [_zen_setup_direct, _zen_show_info, None, _zen_setup_proxy, None]
    idx = arrow_menu('OpenCode Zen', opts,
                     footer='↑↓ navigate · Enter select · Esc back')
    if idx < 0:
        return
    action = actions[idx]
    if action:
        action()


def _ollama_server_reachable(timeout: float = 1.0) -> bool:
    """Direct reachability check against Ollama's own endpoint.

    Deliberately not `provider_manager.list_models()`, which the rest of this
    page uses to mean "the server is up" — that returns `[]` for a server
    that is running but has no models pulled yet, which would make a freshly
    started, empty Ollama look identical to one that never started at all.
    """
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(
                "http://localhost:11434/api/tags", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _try_start_ollama_server() -> bool:
    """Launch `ollama serve` in the background if it is installed but not
    already running — the same "the user should not need a manual terminal
    command first" reasoning `_zen_setup_proxy` already applies to Zen.

    Returns True once the local API actually answers, False if the `ollama`
    binary is not on PATH or the server never came up within the wait.
    """
    import shutil
    import subprocess
    import time as _time

    if _ollama_server_reachable():
        return True   # already running — nothing to launch
    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        return False
    kwargs = {}
    if sys.platform == "win32":
        # Same detached-process pattern agent.py's background run_command
        # uses: the server must outlive this menu, not be tied to its console.
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
    try:
        subprocess.Popen(
            [ollama_bin, "serve"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, **kwargs)
    except Exception:
        return False
    # Polled rather than a fixed sleep: measured live on a real installation
    # with a large local+cloud model library, the server took ~12s to start
    # answering — enumerating everything registered is part of startup, not
    # just binding the port — while an empty/small library binds in under a
    # second. 25s covers the slow case without making the fast one wait for it.
    deadline = _time.monotonic() + 25.0
    while _time.monotonic() < deadline:
        if _ollama_server_reachable():
            return True
        _time.sleep(0.4)
    return False


def _zen_setup_proxy():
    """Start the built-in Zen proxy and configure TOMAS to use it."""
    import zen_catalog
    from zen_proxy import check_status, start_proxy
    port = 6446
    cat = zen_catalog.catalog()
    model = zen_catalog.default_free_model()
    entry = cat.get(model)

    if check_status(port):
        # Already running — just configure
        update_dotenv("ANTHROPIC_API_KEY", "oc-zen-proxy")
        update_dotenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{port}")
        update_dotenv("ANTHROPIC_EXTRA_HEADERS", "")
        update_dotenv("AGENT_MODEL", model)
        reinit_client()
        show_info_page('Zen Proxy Ready', [
            '  ✓ Zen proxy already running',
            f'  ✓ Connected: http://127.0.0.1:{port}',
            f'  ✓ Model: {entry.label if entry else model}',
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

        # Never cached: this is the check that decides whether the start we
        # just performed worked, so a stale "not running" would be a lie.
        if check_status(port, use_cache=False):
            update_dotenv("ANTHROPIC_API_KEY", "oc-zen-proxy")
            update_dotenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{port}")
            update_dotenv("ANTHROPIC_EXTRA_HEADERS", "")
            update_dotenv("AGENT_MODEL", model)
            reinit_client()
            show_info_page('Zen Proxy Started', [
                '  ✓ Zen proxy is running locally',
                f'  ✓ Endpoint: http://127.0.0.1:{port}',
                f'  ✓ Default model: {entry.label if entry else model}',
                '',
                f'  Free models ({cat.freshness}):',
                *(f'    • {m.label}' for m in cat.free()),
                '',
                f'  {DIM}{len(cat.paid())} more models are available and bill '
                f'per token — see Choose Model.{RESET}',
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
    """Connect straight to Zen — no local proxy involved.

    Zen needs dynamic `x-opencode-*` headers on every request. Those used to be
    the proxy's whole reason to exist; `openai_adapter._headers_for` now adds
    them in-process (see `openai_adapter.py`, `provider.type == "zen"`), so a
    direct connection is the normal, supported path.
    """
    import zen_catalog
    key = prompt_text('Enter Zen API key (or leave blank for free tier)', 'public')
    if not key:
        key = 'public'
    # The default follows the key. Blank means "free tier" in the prompt above,
    # and it used to select `ZEN_MODELS[0]` — `claude-fable-5`, which bills.
    cat = zen_catalog.catalog()
    model = (zen_catalog.default_free_model() if key == 'public'
             else (cat.models[0].id if cat else ''))
    entry = cat.get(model)
    update_dotenv("ANTHROPIC_API_KEY", key)
    update_dotenv("ANTHROPIC_BASE_URL", "https://opencode.ai/zen/v1")
    update_dotenv("AGENT_MODEL", model)
    reinit_client()
    show_info_page('Connected to Zen', [
        f'  {GREEN}✓{RESET} API key set',
        f'  {GREEN}✓{RESET} Endpoint: https://opencode.ai/zen/v1',
        f'  {GREEN}✓{RESET} Model: {entry.label if entry else model}',
        '',
        f'  {DIM}{len(cat.free())} of {len(cat.models)} Zen models are free '
        f'({cat.freshness}).{RESET}',
        f'  {DIM}Authentication headers are added in-process — no proxy to run.{RESET}',
        f'  {DIM}Pick a different model from "Change Model".{RESET}',
    ])


def _zen_show_info():
    """Show information about OpenCode Zen."""
    import zen_catalog
    from zen_proxy import check_status
    cat = zen_catalog.catalog()
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
        f'  {BOLD}Connection:{RESET}',
        f'    {GREEN}✓{RESET} Direct — headers added in-process, no proxy needed',
        f'    {r_icon} Optional local proxy on http://127.0.0.1:{port}',
        '',
        f'  {BOLD}Free models{RESET} {DIM}({len(cat.free())} of {len(cat.models)} '
        f'served · {cat.freshness}){RESET}',
        *(f'    {DIM}•{RESET} {m.label}' for m in cat.free()),
        *([] if cat.free() else
          [f'    {DIM}none — upstream is charging for every model right now{RESET}']),
        '',
        f'  {BOLD}Everything else bills per token.{RESET}',
        f'  {DIM}  Current rates: https://opencode.ai/docs/zen{RESET}',
        '',
        f'  {BOLD}Links:{RESET}',
        f'  {CYAN}https://opencode.ai/docs/zen{RESET}',
        f'  {CYAN}https://github.com/anomalyco/opencode{RESET}',
        '',
        f'  {DIM}Choose "Connect to Zen" to begin. The local proxy is only for{RESET}',
        f'  {DIM}pointing other tools at Zen (TOMAS_ZEN_PROXY=1).{RESET}',
    ]
    show_info_page('About OpenCode Zen', lines)


def _detect_provider() -> str:
    """Detect the current AI provider from active config or ANTHROPIC_BASE_URL."""
    # First check active provider in providers.json
    config = _load_providers_config()
    active = config.get("active")
    if active:
        provider_info = config.get("providers", {}).get(active, {})
        ptype = provider_info.get("type", "")
        if ptype in PROVIDER_TYPE_TO_DETECT:
            return PROVIDER_TYPE_TO_DETECT[ptype]

    base = os.environ.get("ANTHROPIC_BASE_URL", "").lower()
    if "openrouter" in base:
        return "openrouter"
    if "opencode" in base or "127.0.0.1:6446" in base or "localhost:6446" in base or "zen" in base:
        return "zen"
    if "11434" in base or "ollama" in base:
        return "ollama"
    if "anthropic" in base:
        return "anthropic"
    if "openai" in base:
        return "openai"
    return "other"


# Map from providers.json 'type' field to _detect_provider() return values.
#
# "ollama" belongs here for the same reason every other type does, and its
# absence was not harmless: an active Ollama provider fell past this table,
# then past the URL sniffing above (an `http://localhost:11434/v1` base
# matches none of those substrings), and landed on "other" — whose entry in
# `_provider_model_entries` is a static list of cloud models. Choosing
# Ollama and then opening "Choose model" offered `openai/gpt-4o` and
# `anthropic/claude-sonnet-4.5`, and none of the models actually installed
# on the machine.
PROVIDER_TYPE_TO_DETECT = {
    "openrouter": "openrouter",
    "zen": "zen",
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
    "ollama": "ollama",
    "groq": "groq",
}


def _detect_provider_from_config() -> str:
    """Fallback: detect provider from saved multi-provider config."""
    config = _load_providers_config()
    active = config.get("active")
    if active:
        provider_info = config.get("providers", {}).get(active, {})
        ptype = provider_info.get("type", "")
        return PROVIDER_TYPE_TO_DETECT.get(ptype, "other")
    return "other"


PROVIDER_LABELS = {
    "openrouter": "OpenRouter",
    "zen": "OpenCode Zen",
    "anthropic": "Anthropic Direct",
    "openai": "OpenAI",
    "google": "Google AI",
    "ollama": "Ollama (local)",
    "groq": "Groq",
    "other": "Generic",
}


def _provider_model_entries(provider: str) -> list[tuple[str, str | None]]:
    """Return model entries relevant to the given provider."""
    entries: list[tuple[str, str | None]] = []

    if provider == "openrouter":
        # Live, never static — this used to be a fixed ~30-model list under
        # vendor headings; OpenRouter's real catalogue numbers in the
        # hundreds and free-tier availability moves under it, the same
        # problem that hid all but a handful of Ollama's models. Formatted by
        # the same helper `page_choose_model`'s primary fetch path uses, so
        # this fallback and that path never disagree about what a row shows.
        import net_probe
        import provider_manager
        cat = net_probe.cached(
            'openrouter_catalog', 60.0, provider_manager.openrouter_catalog)
        entries = _format_openrouter_entries(cat) or [
            ('── OpenRouter is not answering ──', None),
            (f'  {DIM}Check your connection, or try again shortly.{RESET}', None),
        ]
    elif provider == "groq":
        # Live: Groq's own /models listing requires the configured key
        # (unlike OpenRouter's public one), and unlike OpenRouter or Zen it
        # publishes no per-model pricing at all — every model on the account
        # is accessible the same way, so there is no free/paid split to draw.
        import net_probe
        import provider_manager
        active = provider_manager.get_active()

        def _probe_groq():
            if active is None or active.type != "groq":
                return []
            try:
                return provider_manager.list_models(active)
            except Exception:
                return []

        model_ids = net_probe.cached('groq_models', 60.0, _probe_groq)
        if model_ids:
            entries = [('── Available to this key ──', None)]
            entries += [(f'  {m}', m) for m in sorted(model_ids)]
        else:
            entries = [
                ('── Groq is not answering ──', None),
                (f'  {DIM}Check the API key under Connect / configure '
                 f'provider.{RESET}', None),
            ]
    elif provider == "zen":
        # Live, never static — for the reason the Ollama branch below is, and
        # then some: this list *changed under the hardcoded copy* three times.
        # Free-tier only, for now — see provider_manager.VISIBLE_PROVIDER_TYPES
        # and Catalog.free()'s cost.input == 0 and cost.output == 0 rule.
        # Paid models are deliberately left off this menu rather than merely
        # de-emphasized, so nothing here can be picked by accident.
        import net_probe
        import zen_catalog
        cat = net_probe.cached('zen_catalog', 60.0, zen_catalog.catalog)
        entries = []
        free = cat.free()
        if free:
            entries.append(('── Free (no charge) ──', None))
            entries += [(f'  {m.label}', m.id) for m in free]
        # Only when the list is actually doubtful. A fresh cache is the normal
        # fast path, not a degraded one — warning on it would train the user to
        # ignore the warning that matters.
        if cat.source == "static":
            entries.insert(0, (f'{DIM}(offline — showing the built-in list, '
                               f'which may be out of date){RESET}', None))
        elif cat.source == "cache" and cat.age_seconds > zen_catalog.CACHE_TTL:
            hours = int(cat.age_seconds // 3600)
            entries.insert(0, (f'{DIM}(offline — cached list, {hours}h old){RESET}',
                               None))
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
    elif provider == "ollama":
        # Live, never static: the only models worth offering are the ones
        # installed on this machine, and a hardcoded list of them would be
        # wrong the first time the user runs `ollama pull`. Cached because
        # this runs on every redraw of the menu.
        import net_probe
        import provider_manager
        catalog = net_probe.cached(
            'ollama_catalog', 30.0, provider_manager.ollama_catalog)
        if catalog:
            entries = [('── Installed locally ──', None)]
            for m in catalog:
                marks = []
                if m['tools']:
                    marks.append('tools')
                if m['vision']:
                    marks.append('vision')
                ctx = (f"{'' if m.get('exact', True) else '≤'}"
                       f"{m['context_window']:,} ctx"
                       if m['context_window'] else '? ctx')
                note = f"{ctx}{', ' + '/'.join(marks) if marks else ''}"
                # Tool use is not decoration here: without it the agent drops
                # to the text protocol, so it is the single fact that decides
                # whether a local model is usable for real work.
                entries.append((f"  {m['name']}  {DIM}({note}){RESET}", m['name']))
        else:
            entries = [
                ('── Ollama is not answering ──', None),
                (f'  {DIM}Start it with `ollama serve`, then reopen this menu.{RESET}', None),
            ]
    elif provider == "google":
        # Live, for the same reason Ollama and Zen are: which Gemini models a
        # key can reach is a property of the key, and the list moves. Without
        # this branch google fell through to `_provider_model_entries("other")`
        # — a static cloud list — which is exactly the bug that hid all eight
        # installed Ollama models.
        import net_probe
        import provider_manager
        catalog = net_probe.cached(
            'google_catalog', 60.0, provider_manager.google_model_catalog)
        if catalog:
            entries = [('── Available to this key ──', None)]
            for m in catalog:
                ctx = f"{m['context_window']:,} ctx" if m['context_window'] else '? ctx'
                entries.append(
                    (f"  {m['name']}  {DIM}({ctx}){RESET}", m['name']))
        else:
            entries = [
                ('── Google is not answering ──', None),
                (f'  {DIM}Set GOOGLE_API_KEY, or add the key under '
                 f'Connect / configure provider.{RESET}', None),
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
                     footer=DEFAULT_FOOTER)
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
    """Update the model in the stored provider config, if any.

    The stored capabilities describe the model being replaced, so they are
    re-measured for the new one rather than inherited — see
    `provider_manager.refresh_for_model`. A failure here is not fatal: the
    model switch itself has already been written, and stale capabilities
    degrade a feature where a raised exception would lose the switch.
    """
    config = _load_providers_config()
    active = config.get("active")
    if not (active and active in config.get("providers", {})):
        return
    config["providers"][active]["model"] = model
    _save_providers_config(config)
    try:
        import provider_manager
        provider = provider_manager.get(active)
        if provider is not None:
            provider_manager.refresh_for_model(provider)
    except Exception:
        pass


def page_choose_model():
    """Arrow-key menu to select a model."""
    provider = _detect_provider()
    # Fallback: if env-var detection returns 'other', check saved config
    if provider == "other":
        provider = _detect_provider_from_config()
    label = PROVIDER_LABELS.get(provider, "Generic")

    # For OpenRouter: auto-fetch all models from API, fall back to static list
    if provider == "openrouter":
        fetched = _try_fetch_openrouter_entries()
        if fetched is not None:
            model_entries = fetched
        else:
            model_entries = _provider_model_entries(provider)
            model_entries.insert(0, ('🔍  Retry fetch from OpenRouter API', '__fetch__'))
    # Zen fetches upstream directly (see `_provider_model_entries`). It used to
    # ask the local proxy instead, when one happened to be running — but the
    # proxy builds its `/v1/models` reply out of the same hardcoded
    # `ZEN_MODELS`, so that path cost a port probe and an HTTP round trip to
    # return the stale list a second time.
    elif provider == "zen":
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
                     footer=DEFAULT_FOOTER)
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


def _format_openrouter_entries(
        cat: list[dict]) -> list[tuple[str, str | None]] | None:
    """Render provider_manager.openrouter_catalog()'s output for a picker.

    Free models come first and under their own heading, and every row says
    whether the model can call tools. Both matter more than the alphabetical
    ordering they replace: the catalogue runs to hundreds of entries, and a
    model that cannot call tools cannot drive this agent at all — it drops to
    the text protocol at best. Measured against the live catalogue, some of
    the free models declare no tool support, and picking one of those is a
    failure the menu can prevent rather than explain afterwards.

    Shared by the auto-fetch path, the manual retry button, and
    `_provider_model_entries`'s own fallback, so there is exactly one place
    that turns the catalogue into rows — not three that must agree.
    """
    if not cat:
        return None

    def describe(m: dict) -> tuple[str, str]:
        marks = []
        if m['context_window']:
            marks.append(f"{m['context_window']:,} ctx")
        if not m['tool_call']:
            marks.append('no tools')
        if not m['free'] and (m['prompt_cost'] or m['completion_cost']):
            # Per million, not per token. OpenRouter quotes per-token prices
            # like 3e-07, and `$0.0000/$0.0000` was every paid model on the
            # page — a price column that could not distinguish the cheapest
            # model in the catalogue from the most expensive.
            marks.append(f"${m['prompt_cost'] * 1e6:,.2f}/"
                         f"${m['completion_cost'] * 1e6:,.2f} per M")
        label = (f"  {m['id']}  {DIM}({' · '.join(marks)}){RESET}" if marks
                else f"  {m['id']}")
        return label, m['id']

    free = sorted((m for m in cat if m['free']), key=lambda m: m['id'])
    paid = sorted((m for m in cat if not m['free']), key=lambda m: m['id'])

    entries: list[tuple[str, str | None]] = []
    if free:
        entries.append((f'── Free ({len(free)}) ──', None))
        entries += [describe(m) for m in free]
    if paid:
        entries.append((f'── Paid ({len(paid)}) ──', None))
        entries += [describe(m) for m in paid]
    return entries or None


def _try_fetch_openrouter_entries() -> list[tuple[str, str | None]] | None:
    """Model entries from the OpenRouter API. None on failure."""
    import provider_manager
    return _format_openrouter_entries(provider_manager.openrouter_catalog())


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
                     footer=DEFAULT_FOOTER)
    if idx < 0:
        return
    model = values[idx]
    if model:
        update_dotenv("AGENT_MODEL", model)
        show_info_page('Done', [f'  ✓ Model set to: {model}'])


def _ensure_api_configured() -> bool:
    """Make sure there is something to talk to before the chat opens.

    With no key configured this falls back to the OpenCode Zen free tier.
    It used to do that by spawning the proxy daemon in the background, which
    contradicts `provider_manager._use_standalone_proxy` -- the daemon is
    opt-in now that `openai_adapter` translates in-process. Configuring the
    direct endpoint gets the same result without a stray background process.
    """
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return True

    try:
        import zen_catalog
    except ImportError:
        print(f"  {RED}✗{RESET} No API key configured.")
        print(f"  {RED}✗{RESET} Set ANTHROPIC_API_KEY in {TOMAS_DIR / '.env'} to use TOMAS.")
        return False

    print(f"  {CYAN}◈{RESET} No provider configured — using the OpenCode Zen free tier.")
    update_dotenv("ANTHROPIC_API_KEY", "public")
    update_dotenv("ANTHROPIC_BASE_URL", "https://opencode.ai/zen/v1")
    update_dotenv("ANTHROPIC_EXTRA_HEADERS", "")
    if os.environ.get("AGENT_MODEL", "").strip() == "":
        # A model that is actually free. This line said `ZEN_MODELS[0]`, which
        # is `claude-fable-5` — the sentence printed above promised the free
        # tier and the next statement selected a billing model.
        model = zen_catalog.default_free_model()
        update_dotenv("AGENT_MODEL", model)
        print(f"  {DIM}Model: {model}{RESET}")
    print(f"  {DIM}Connect your own provider any time from the menu.{RESET}")
    try:
        from agent import reinit_client
        reinit_client()
    except Exception as e:
        print(f"  {YELLOW}⚠{RESET} Could not initialise the client: {e}")
        return False
    return True


def page_run_agent():
    """Launch the agent from the TUI menu."""
    if not _ensure_api_configured():
        print()
        input(f"{DIM}Press Enter to return to menu...{RESET}")
        return
    clear_screen()
    print(f'{BOLD}TOMAS{RESET}  {DIM}· {get_model()}{RESET}')
    print('─' * 50)
    print(f"{DIM}Esc Esc to leave, or type 'exit'.  /help for commands.{RESET}")
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

# The banner is 11 rows. On a short window those rows come out of the menu's
# budget and the list starts paginating, so below a threshold the title
# collapses to one line and every entry stays visible.
TOMAS_TITLE = (f'\n  {CYAN}{BOLD}TOMAS{RESET}  '
               f'{DIM}{MAGENTA}An AI Coding Agent — from the Terminal{RESET}\n')

# Labels name what the user gets, not how it is implemented. Blank strings are
# spacers — `arrow_menu` draws them but the cursor skips over them, so they group
# the list without costing a keypress. MENU_ACTIONS is index-aligned with this.
MENU_ITEMS = [
    f'  {MAGENTA}▶{RESET}  {BOLD}Start Chat{RESET}',
    '',
    f'  {GREEN}⟐{RESET}  Providers',
    f'  {GREEN}⟐{RESET}  Add or Configure Provider',
    f'  {GREEN}⟐{RESET}  Switch Provider',
    f'  {GREEN}⟐{RESET}  Change Model',
    '',
    f'  {YELLOW}⬡{RESET}  MCP Servers',
    f'  {YELLOW}⬡{RESET}  Tools',
    f'  {YELLOW}⬡{RESET}  Skills',
    f'  {YELLOW}▣{RESET}  Context Budget',
    f'  {YELLOW}⚙{RESET}  Settings',
    '',
    f'  {CYAN}◈{RESET}  Sessions & Notes',
    f'  {BLUE}✎{RESET}  Agent Instructions',
    f'  {BLUE}✎{RESET}  Project Guidelines',
    '',
    f'  {RED}✕{RESET}  Exit',
]

MENU_ACTIONS = [
    page_run_agent,
    None,  # spacer
    page_providers,
    page_configure_provider,
    _choose_provider_to_switch,
    page_choose_model,
    None,  # spacer
    page_mcps,
    page_tools,
    page_skills,
    page_context_budget,
    page_settings,
    None,  # spacer
    page_sessions,
    page_edit_instructions,
    page_edit_project_agent,
    None,  # spacer
    None,  # exit
]

assert len(MENU_ACTIONS) == len(MENU_ITEMS), "menu labels and actions must stay aligned"

EXIT_INDEX = len(MENU_ITEMS) - 1


def _control_panel_lines() -> list:
    """The status block above the menu.

    Every number here is read from disk or memory — nothing probes the network,
    because this redraws each time the user returns from a page.
    """
    def count(fn, default='—'):
        try:
            return fn()
        except Exception:
            return default

    provider = _get_active_provider_name()
    provider_status = (f'{GREEN}{provider}{RESET}' if provider
                       else f'{YELLOW}not configured{RESET}')

    def model_line():
        from provider_manager import capabilities_for_active
        caps = capabilities_for_active()
        window = f'{caps.context_window // 1000}K context'
        return f'{CYAN}{get_model()}{RESET}  {DIM}· {window}{RESET}'

    def extensions_line():
        from mcp_manager import read_mcp_servers, is_server_disabled
        from skills_manager import discover_skills
        servers = read_mcp_servers()
        live = sum(1 for name in servers if not is_server_disabled(name))
        skills = len(discover_skills())
        parts = [f'{live} MCP', f'{len(TOOLS)} tools', f'{skills} skills']
        off = len(servers) - live
        if off:
            parts.append(f'{DIM}{off} disabled{RESET}')
        return f'{DIM} · {RESET}'.join(parts)

    def sessions_line():
        n = get_session_count()
        return f'{n} saved' if n else f'{DIM}none yet{RESET}'

    # Banner (11 rows) + panel (7) + menu + footer. Drop to the one-line title
    # rather than let the menu scroll.
    banner = TOMAS_ART if term_lines() >= len(MENU_ITEMS) + 21 else TOMAS_TITLE

    return [
        banner,
        f'  {BOLD}Control Panel{RESET}',
        f'  {DIM}{"─" * 46}{RESET}',
        f'  {DIM}Project{RESET}     {AGENT_PROJECT_DIR.name}',
        f'  {DIM}Provider{RESET}    {provider_status}',
        f'  {DIM}Model{RESET}       {count(model_line)}',
        f'  {DIM}Extensions{RESET}  {count(extensions_line)}',
        f'  {DIM}Sessions{RESET}    {count(sessions_line)}',
        '',
    ]


def run_menu():
    """Run the main interactive menu."""
    while True:
        idx = arrow_menu('', MENU_ITEMS,
                         header_lines=_control_panel_lines(),
                         max_visible=len(MENU_ITEMS),
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
    """Launch the agent directly (--run)."""
    if not _ensure_api_configured():
        sys.exit(1)
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
        TOMAS mcp add <name> -- <command> [args...]          (stdio, default)
        TOMAS mcp add --transport http <name> <url>          (HTTP)
        TOMAS mcp add --transport stdio <name> -- <command>  (explicit stdio)
    """
    from mcp_manager import write_mcp_server

    argv = sys.argv[3:]  # skip "TOMAS mcp add"

    # Find --transport flag (optional; default to stdio)
    transport = "stdio"
    if "--transport" in argv:
        idx = argv.index("--transport")
        if idx + 1 < len(argv):
            transport = argv[idx + 1]
            argv = argv[:idx] + argv[idx + 2:]

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
            print("Error: TOMAS mcp add <name> -- <command> [args...]")
            print("  Example: TOMAS mcp add chrome-devtools -- npx -y chrome-devtools-mcp")
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


#: Every server `cmd_setup` configures by default. All seven run with no
#: API key and no account -- none needs a credential to enable, so none
#: needs one to *work* immediately after install. A server that requires a
#: key (Tavily search, GitHub, ...) is deliberately never in this list; add
#: those yourself with `TOMAS mcp add` once you have the credential, so
#: setup never writes a config that is enabled but broken.
DEFAULT_MCP_SERVERS = (
    "playwright", "context7", "word-docs", "sequential-thinking",
    "fetch", "time", "excel", "pdf",
)


def _check_mcp_health(names, timeout: int = 45) -> dict[str, dict]:
    """Actually connect to each named server and list its tools.

    Writing a config entry only proves the JSON is well-formed -- it says
    nothing about whether `npx`/`uvx` exist on PATH, whether the package
    resolves, or whether the process speaks MCP once it starts. This is the
    only way to tell "configured" from "working".

    Bounded per server: a first run downloads the package fresh (npm/PyPI),
    which can take a while but must not be able to hang setup forever. A
    server that is still starting when its budget runs out is reported as
    pending, not as broken -- the process is left running rather than
    killed, since it may simply need more time to finish that first fetch.
    """
    import net_probe
    from mcp_manager import MCPServer, read_mcp_servers

    configs = read_mcp_servers()
    results: dict[str, dict] = {}

    def probe(name: str) -> dict:
        cfg = configs.get(name)
        if cfg is None:
            return {"connected": False, "error": "not configured", "tool_count": 0}
        try:
            server = MCPServer(name, cfg)
            ok = server.connect()
            row = {"connected": ok,
                   "error": None if ok else (server._last_error or "connect failed"),
                   "tool_count": len(server.tools) if ok else 0}
            if ok:
                server.disconnect()
            return row
        except Exception as exc:
            return {"connected": False, "error": str(exc), "tool_count": 0}

    # One budget shared by every server running in parallel, and genuinely
    # abandoned when it runs out. The previous version used
    # `ThreadPoolExecutor` + `wait(timeout=...)` and a comment explaining that
    # this bounded the wait -- it bounded only the *foreground* wait. The
    # pool's threads are non-daemon and `concurrent.futures` joins them from an
    # atexit hook, so every probe given up on here was waited for again at
    # interpreter shutdown, silently. `net_probe.fan_out` uses daemon threads,
    # which is what makes "leave it running" true rather than aspirational.
    done, unfinished = net_probe.fan_out(
        probe, list(names), max_workers=max(1, len(names)), timeout=timeout)
    for name, row in done:
        results[name] = (
            {"connected": False, "error": str(row), "tool_count": 0}
            if isinstance(row, Exception) else row)
    for name in unfinished:
        results[name] = {
            "connected": False,
            "error": f"still starting after {timeout}s -- first run downloads "
                     f"the package; try `TOMAS mcp list` again shortly",
            "tool_count": 0,
        }
    # No shutdown call: `net_probe.fan_out` runs on daemon threads, so an
    # abandoned probe needs nothing closing and cannot delay exit. (The
    # `pool.shutdown(wait=False)` that used to be here outlived the pool it
    # belonged to and raised NameError on every `TOMAS setup`.)
    return results


def cmd_setup():
    """Install default MCP servers, verify each one actually starts, and
    configure environment.

    Installs the default set of MCP servers for TOMAS (see
    DEFAULT_MCP_SERVERS) -- all eight run with no API key:
      - playwright: browser automation, official server (npx @playwright/mcp)
      - context7: up-to-date library documentation search (npx)
      - word-docs: create/edit Microsoft Word (.docx) documents (uvx)
      - sequential-thinking: structured step-by-step reasoning scaffold (npx)
      - fetch: fetches a URL as clean markdown, not raw HTML (uvx mcp-server-fetch)
      - time: current time / timezone conversion (uvx mcp-server-time)
      - excel: create/edit Microsoft Excel (.xlsx) documents (npx)
      - pdf: merge/split/rotate/OCR/tables/forms/annotations on existing
        PDFs -- read_file already extracts plain text, this is everything
        beyond that (uvx mcp-pdf)

    After configuring them, connects to each one and lists its tools --
    "configured" and "working" are different claims, and only the health
    check can tell a server that starts and answers tools/list from one
    whose command is merely spelled correctly.
    """
    from mcp_manager import read_mcp_servers, write_mcp_server
    import subprocess, sys

    tomas_venv = TOMAS_DIR / ".venv"
    python_exe = tomas_venv / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = sys.executable

    print(f"{BOLD}TOMAS Setup{RESET}")
    print("─" * 50)

    existing = read_mcp_servers()

    def ensure(name: str, config: dict) -> None:
        """Write a server's config unless it is already there -- setup must
        never clobber a server the user configured or customised by hand."""
        if name in existing:
            print(f"  {GREEN}✓{RESET} {name} MCP already configured")
            return
        write_mcp_server(name, config)
        print(f"  {GREEN}✓{RESET} {name} MCP configured")

    # ── playwright: upgrade TOMAS's own old bundled script to the official
    #    server in place, but leave a hand-configured entry alone ──
    old_playwright = existing.get("playwright") or {}
    is_old_bundled_script = "playwright_mcp_server.py" in " ".join(
        str(a) for a in old_playwright.get("args", []))
    if not old_playwright or is_old_bundled_script:
        write_mcp_server("playwright", {
            "type": "stdio", "command": "npx", "args": ["-y", "@playwright/mcp"],
        })
        print(f"  {GREEN}✓{RESET} playwright MCP configured (official @playwright/mcp)")
    else:
        print(f"  {GREEN}✓{RESET} playwright MCP already configured")

    ensure("context7", {
        "type": "stdio", "command": "npx", "args": ["-y", "@upstash/context7-mcp"],
    })
    ensure("word-docs", {
        "type": "stdio", "command": "uvx",
        "args": ["--from", "office-word-mcp-server", "word_mcp_server"],
    })
    ensure("sequential-thinking", {
        "type": "stdio", "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
    })
    # mcp-server-fetch and mcp-server-time both import `McpError` from
    # `mcp.shared.exceptions` -- renamed to `MCPError` in the `mcp` SDK's
    # latest release, which `uvx` resolves by default. Both packages import-
    # error out on startup against that release; pinning below it is the
    # actual fix. An exact `==` pin, not a `<` range: `_connect_stdio` runs
    # this through `cmd.exe` on Windows (`shell=True`), which parses a bare
    # `<` as input redirection rather than part of the argument -- `uvx
    # --with "mcp<1.10" ...` silently breaks there even though it works
    # invoked directly from a shell that isn't cmd.exe.
    ensure("fetch", {
        "type": "stdio", "command": "uvx",
        "args": ["--with", "mcp==1.9.4", "mcp-server-fetch"],
    })
    ensure("time", {
        "type": "stdio", "command": "uvx",
        "args": ["--with", "mcp==1.9.4", "mcp-server-time"],
    })
    ensure("excel", {
        "type": "stdio", "command": "npx", "args": ["--yes", "@negokaz/excel-mcp-server"],
    })
    ensure("pdf", {
        "type": "stdio", "command": "uvx", "args": ["mcp-pdf"],
    })

    # ── Playwright browser ──
    #
    # Only refreshed when it is already here. The installer stopped fetching
    # ~170 MB of Chromium for every user because web search falls back to
    # duckduckgo without it — and this ran straight afterwards and downloaded
    # it anyway, which made that decision worth nothing. `TOMAS browser` is
    # the one place that fetches it on purpose.
    from pathlib import Path as _Path

    cache = _Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    have_browser = cache.exists() and any(cache.glob("chromium-*"))
    print()
    if have_browser:
        print(f"  {YELLOW}⚙ Refreshing the Playwright browser...{RESET}")
        try:
            subprocess.run(
                [str(python_exe), "-m", "playwright", "install", "chromium"],
                check=True, capture_output=True, timeout=180)
            print(f"  {GREEN}✓{RESET} Playwright browser up to date")
        except Exception as e:
            print(f"  {YELLOW}⚠ Playwright refresh skipped: {e}{RESET}")
    else:
        print(f"  {DIM}No Playwright browser (~170 MB) — web search will use "
              f"duckduckgo.{RESET}")
        print(f"  {DIM}Add it any time with{RESET} {CYAN}TOMAS browser{RESET}")

    # ── Verify every default server actually starts and answers -- a
    #    config written above proves nothing about whether it works ──
    print()
    print(f"  {YELLOW}⚙ Verifying MCP servers (first run may download packages)...{RESET}")
    health = _check_mcp_health(DEFAULT_MCP_SERVERS)
    healthy = 0
    for name in DEFAULT_MCP_SERVERS:
        row = health.get(name) or {"connected": False, "error": "not checked", "tool_count": 0}
        if row["connected"]:
            healthy += 1
            print(f"  {GREEN}✓{RESET} {name:20s} {DIM}{row['tool_count']} tool(s){RESET}")
        else:
            print(f"  {RED}✗{RESET} {name:20s} {DIM}{row['error']}{RESET}")

    print()
    if healthy == len(DEFAULT_MCP_SERVERS):
        print(f"  {GREEN}✓ All {healthy} MCP servers verified working.{RESET}")
    else:
        failed = len(DEFAULT_MCP_SERVERS) - healthy
        print(f"  {YELLOW}⚠ {healthy}/{len(DEFAULT_MCP_SERVERS)} MCP servers verified -- "
              f"{failed} failed to start.{RESET}")
        print(f"  {DIM}This usually means Node.js (npx) or uv (uvx) is not installed / on PATH.{RESET}")
        print(f"  {DIM}Re-run{RESET} {CYAN}TOMAS setup{RESET} {DIM}once that's fixed, or check{RESET} "
              f"{CYAN}TOMAS mcp list{RESET}")

    print()
    print(f"  {GREEN}✓ Setup complete!{RESET}")
    print(f"  Run {CYAN}TOMAS --run{RESET} to start the agent.")


def cmd_skill_list():
    """List installed skills with enhanced formatting."""
    from skills_manager import discover_skills, find_skill_dirs

    # warn=True — see page_skills().
    all_skills = discover_skills(warn=True)
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


def cmd_skill_install():
    """Install a skill.
    Syntax:
        TOMAS skill install <name>          Install from the skills registry
        TOMAS skill install <name> -- <command> [args...]  Install via custom command
    """
    import subprocess

    argv = sys.argv[3:]  # skip "TOMAS skill install"
    if not argv:
        print("Error: TOMAS skill install <name> [-- <command> args...]")
        sys.exit(1)

    if "--" in argv:
        # Custom install command
        sep = argv.index("--")
        name = argv[0] if sep > 0 else None
        if not name:
            print("Error: name is required before --")
            sys.exit(1)
        cmd_parts = argv[sep + 1:]
        if not cmd_parts:
            print("Error: command required after --")
            sys.exit(1)
        print(f"  Installing skill '{name}' via: {' '.join(cmd_parts)}...")
        result = subprocess.run(cmd_parts, capture_output=False)
        if result.returncode == 0:
            print(f"  {GREEN}✓{RESET} Skill '{name}' installed!")
        else:
            sys.exit(result.returncode)
    else:
        # Default: use npx skills add
        name = argv[0]
        print(f"  Installing skill '{name}' from registry...")
        result = subprocess.run(
            ["npx", "-y", "skills", "add", name],
            capture_output=False,
        )
        if result.returncode == 0:
            print(f"  {GREEN}✓{RESET} Skill '{name}' installed!")
        else:
            print(f"  {RED}✗{RESET} Failed to install skill '{name}'")
            print(f"  Tip: try {CYAN}TOMAS skill install {name} -- <custom-command>{RESET}")
            sys.exit(result.returncode)


def cmd_uninstall():
    """Run the uninstall.ps1 script to remove TOMAS completely."""
    uninstall_ps1 = TOMAS_DIR / "bin" / "uninstall.ps1"
    if not uninstall_ps1.exists():
        print(f"  {RED}✗{RESET} Uninstaller not found at: {uninstall_ps1}")
        print(f"  Delete {TOMAS_DIR} manually to remove TOMAS.")
        sys.exit(1)
    print(f"  Running uninstaller...")
    sys.stdout.flush()
    result = subprocess.run(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(uninstall_ps1)],
    )
    sys.exit(result.returncode)


def cmd_update():
    """Run the upgrade script to update TOMAS from GitHub."""
    update_cmd = TOMAS_DIR / "bin" / "TOMAS-upgrade.cmd"
    if not update_cmd.exists():
        print(f"  {RED}✗{RESET} Upgrade script not found at: {update_cmd}")
        print(f"  To reinstall manually: {CYAN}powershell -c \"iex (iwr -UseBasicParsing -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/main/install.ps1)\"{RESET}")
        sys.exit(1)
    print(f"  Upgrading TOMAS from GitHub...")
    sys.stdout.flush()
    result = subprocess.run([str(update_cmd)], shell=True)
    sys.exit(result.returncode)


def print_help():
    print(__doc__.strip())


# ═══════════════════════════════════════════════════════════
#  CLI ENTRY POINT — parse subcommands
# ═══════════════════════════════════════════════════════════

def cmd_install_browser():
    """Fetch the Playwright browser, on demand rather than at install time.

    The installer stopped downloading ~170 MB of Chromium for every user
    because web search does not require it — `web_search` prefers Playwright
    and falls back to duckduckgo_search/ddgs — so this is the other half of
    that decision: the capability has to stay one command away, or "optional"
    quietly means "unavailable".
    """
    import subprocess

    print(f'  {CYAN}◈{RESET} Downloading the Playwright browser '
          f'{DIM}(~170 MB, once){RESET}')
    sys.stdout.flush()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False)
    except FileNotFoundError:
        print(f'  {RED}✗{RESET} playwright is not installed in this '
              f'environment.')
        return
    if result.returncode == 0:
        print(f'  {GREEN}✓{RESET} Browser ready — web search will use it '
              f'from now on.')
    else:
        print(f'  {RED}✗{RESET} Download failed (exit {result.returncode}). '
              f'Web search still works via duckduckgo.')


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

    # ── browser → fetch the Playwright browser on demand ──
    if arg == 'browser':
        cmd_install_browser()
        return

    # ── mcp subcommand ──
    if arg == 'mcp':
        if len(sys.argv) < 3:
            print("Usage: TOMAS mcp {list|add|remove|env|disable|enable} [...]")
            print("  TOMAS mcp list")
            print("  TOMAS mcp add <name> -- <command> [args...]      (stdio, default)")
            print("  TOMAS mcp add --transport http <name> <url>      (HTTP)")
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
            print("Usage: TOMAS skill {list|install}")
            print("  TOMAS skill list")
            print("  TOMAS skill install <name> -- <command> [args...]")
            sys.exit(1)
        sub = sys.argv[2]
        if sub == 'list':
            cmd_skill_list()
        elif sub == 'install':
            cmd_skill_install()
        else:
            print(f"Unknown skill subcommand: {sub}")
            print("Usage: TOMAS skill {list|install}")
            sys.exit(1)
        return

    # ── setup command ──
    if arg == 'setup':
        cmd_setup()
        return

    # ── update / upgrade command ──
    if arg in ('update', 'upgrade'):
        cmd_update()
        return

    # ── uninstall command ──
    if arg == 'uninstall':
        cmd_uninstall()
        return

    # ── Unknown argument ──
    print(f"Unknown option: {arg}")
    print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
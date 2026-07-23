# 🤖 TOMAS — Terminal Operated Modular Agent System

An AI coding agent that runs in your terminal. Built on the same architecture as Claude Code — agent loop, tool calling, MCP integration, and a self-improving skill system.

```bash
# ═══════════════════════════════════════════════════════════
#  One-line install
# ═══════════════════════════════════════════════════════════

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -c "iex (iwr -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/prototype2-refactoring/install.ps1)"

# Linux / macOS / WSL
curl -fsSL https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/prototype2-refactoring/install.sh | bash
```

After install, open a **new terminal** and run:

```bash
TOMAS               # Windows — interactive TUI
tomas               # Linux/macOS
TOMAS --run         # Launch agent directly
TOMAS --help        # Show CLI help
TOMAS mcp list      # List MCP servers
TOMAS skill list    # List installed skills
```

---

## ✨ Features

| Feature | Description |
|---|---|
| **Agent loop** | LLM-driven tool calling with automatic result feedback |
| **MCP integration** | Load any MCP server — filesystem, fetch, GitHub, etc. |
| **Self-improving** | Auto-detects patterns, generates skills from your usage |
| **4 modes** | `auto` / `default` / `strict` / `yolo` — control permissions |
| **Skills system** | Load reusable instruction sets via `/skill <name>` |
| **Token tracking** | Real-time token usage after every response |
| **Auto-compaction** | Summarizes long conversations before context overflow |
| **Multi-provider** | Supports Anthropic, OpenRouter, MiniMax, OpenCode Zen, and more |

### 🎮 Modes

| Mode | Behavior | Trigger |
|---|---|---|
| `auto` | Auto-approves low-risk tools (read, search) | `F5` / `/mode auto` |
| `default` | Asks before every tool | `F6` / `/mode default` |
| `strict` | Asks before every tool + resets overrides | `F7` / `/mode strict` |
| `yolo` | **Auto-approves ALL tools** — no prompts | `F8` / `/mode yolo` |

Press **Shift+Space** to cycle through modes.

### ⌨️ Slash Commands

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/clear` | Clear conversation history |
| `/status` | Model, connections, token stats |
| `/mode` | Switch mode: `/mode auto / default / strict / yolo` |
| `/compact` | Force conversation compaction |
| `/skills` | List all installed skills |
| `/skill <name>` | Load and execute a skill |
| `/self-improve` | Self-improvement system (`/si` for short) |
| `/pdf-report` | Generate AI news PDF report |
| `/exit` | Exit TOMAS |

Type `/` for auto-complete with Tab.

---

## 🔧 Configuration

Edit `~/.tomas/.env` (created during install):

```env
# Required: your API key
ANTHROPIC_API_KEY=sk-ant-...

# Optional: custom endpoint (MiniMax, OpenRouter, etc.)
# ANTHROPIC_BASE_URL=https://api.anthropic.com

# Optional: model name
# AGENT_MODEL=claude-sonnet-4-5

# Optional: auto-approve low-risk tools (1 or 0)
# AGENT_AUTO_APPROVE=1
```

### Supported providers

| Provider | `ANTHROPIC_BASE_URL` |
|---|---|
| **Anthropic** (default) | `https://api.anthropic.com` |
| **MiniMax** | `https://api.minimax.io/anthropic` |
| **OpenRouter** | `https://openrouter.ai/api/v1` |
| **OpenCode Zen** | Auto-detected (127.0.0.1:6446) |

---

## 📦 What's inside

```
~/.tomas/
├── bin/               # Launcher scripts (added to PATH)
│   ├── TOMAS.ps1      # PowerShell launcher (Windows)
│   ├── TOMAS.cmd      # CMD launcher (Windows)
│   ├── tomas          # Bash launcher (Linux/macOS)
│   └── uninstall*     # Uninstaller
├── src/               # TOMAS source code
│   ├── agent.py       # Core agent loop
│   ├── agent_cli.py   # CLI / TUI interface
│   ├── self_improve.py # Self-improving system
│   ├── mcp_manager.py # MCP server management
│   └── ...
├── .venv/             # Python virtual environment
└── .env               # Your configuration
```

---

## 🗑️ Uninstall

```bash
# Windows
uninstall-tomas

# Linux / macOS
uninstall-tomas
```

Or run the uninstaller script directly: `~/.tomas/bin/uninstall.ps1` (Windows) or `~/.tomas/bin/uninstall-tomas` (Linux/macOS).

---

## 🔄 Update

Re-run the same install command — it will download the latest version:

```powershell
# Windows
powershell -c "iex (iwr -Uri https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/prototype2-refactoring/install.ps1)"
```

```bash
# Linux/macOS
curl -fsSL https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/prototype2-refactoring/install.sh | bash
```

---

## 💻 Development

If you want to hack on TOMAS locally:

```bash
git clone https://github.com/oleksandkov/Agent-For-TOM.git
cd Agent-For-TOM
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python agent_cli.py
```

The project launchers (`TOMAS.ps1` / `TOMAS.bat`) work directly from the cloned directory for development.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│  REPL: read user input                                  │
└───────────────────────────┬─────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────┐
│  Build system prompt (base + CLAUDE.md + memory + tips)  │
└───────────────────────────┬─────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────┐
│  Agent loop (while True):                                │
│   1. call LLM with system + tools + messages             │
│   2. if stop_reason != "tool_use": return text           │
│   3. for each tool_use block:                            │
│        - check permission (risk level)                   │
│        - execute handler, collect result                 │
│   4. append tool_results as a user message               │
│   5. loop back to 1                                      │
└─────────────────────────────────────────────────────────┘
```

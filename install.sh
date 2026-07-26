#!/usr/bin/env bash
# ============================================================================
# TOMAS Agent Installer — for Linux / macOS / WSL
#
# Usage:
#   Remote:  curl -fsSL https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/prototype2-refactoring/install.sh | bash
#   Local:   bash install.sh
#
# Config — edit these before running if you fork the repo:
# ============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/oleksandkov/Agent-For-TOM/archive/prototype2-refactoring.zip}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.tomas}"

BIN_DIR="$INSTALL_DIR/bin"
SRC_DIR="$INSTALL_DIR/src"
VENV_DIR="$INSTALL_DIR/.venv"
ENV_FILE="$INSTALL_DIR/.env"

# ── Colors ──
BOLD='\033[1m'
CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
DIM='\033[2m'
RESET='\033[0m'

info()  { echo -e "  ${CYAN}${1}${RESET}"; }
ok()    { echo -e "  ${GREEN}✓${RESET} ${1}"; }
warn()  { echo -e "  ${YELLOW}⚠${RESET} ${1}"; }
fail()  { echo -e "  ${RED}✗${RESET} ${1}"; exit 1; }
header(){ echo -e "\n${BOLD}${1}${RESET}"; }

# ── Banner ──
echo ""
echo -e "  ${CYAN}╔══════════════════════════════════════════╗${RESET}"
echo -e "  ${CYAN}║       TOMAS Agent Installer v2.0         ║${RESET}"
echo -e "  ${CYAN}╚══════════════════════════════════════════╝${RESET}"
echo ""

# ── Prerequisites ──
header "Checking prerequisites..."

PYTHON=""
PYTHON_VER=""

for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
        if [ -n "$ver" ] && awk "BEGIN{exit !($ver >= 3.10)}" 2>/dev/null; then
            PYTHON=$(command -v "$cmd")
            PYTHON_VER=$ver
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python 3.10+ is required but not found."
fi
ok "Python $PYTHON_VER found at: $PYTHON"

# Check for unzip/wget/curl
if ! command -v unzip &>/dev/null; then
    fail "Required: unzip. Install it with your package manager (apt install unzip, brew install unzip, etc.)"
fi

# ── Create directories ──
INSTRUCTIONS_DIR="$INSTALL_DIR/instructions"
PROJECTS_DIR="$INSTRUCTIONS_DIR/project"
SESSIONS_DIR="$INSTALL_DIR/sessions"
SELF_IMPROVE_DIR="$INSTALL_DIR/self-improve"
MEMORY_DIR="$INSTALL_DIR/memory"
SELF_NOTES_DIR="$INSTALL_DIR/self-notes"

header "Creating directories..."
mkdir -p "$BIN_DIR" "$SRC_DIR" "$INSTRUCTIONS_DIR" "$PROJECTS_DIR" "$SESSIONS_DIR" "$SELF_IMPROVE_DIR" "$MEMORY_DIR" "$SELF_NOTES_DIR"
ok "Install dir: $INSTALL_DIR"

# ── Get source code ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd 2>/dev/null || echo "")"
HAS_LOCAL_SOURCE=false

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/agent.py" ]; then
    HAS_LOCAL_SOURCE=true
fi

if [ "$HAS_LOCAL_SOURCE" = true ]; then
    header "Local source detected — copying files..."
    # Copy all files except common excludes
    rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
          --exclude='.agent' --exclude='*.pyc' \
          "$SCRIPT_DIR/" "$SRC_DIR/" 2>/dev/null || \
    cp -r "$SCRIPT_DIR/" "$SRC_DIR/" 2>/dev/null || {
        # Fallback: manually copy files
        for f in "$SCRIPT_DIR"/*; do
            case "$(basename "$f")" in
                .venv|__pycache__|.git|.agent) ;;
                *) cp "$f" "$SRC_DIR/" 2>/dev/null || true ;;
            esac
        done
    }
    ok "Copied $(find "$SRC_DIR" -maxdepth 1 -type f | wc -l) files"
else
    header "Downloading from GitHub..."
    echo -e "  ${DIM}URL: $REPO_URL${RESET}"
    
    TMP_DIR=$(mktemp -d)
    TMP_ZIP="$TMP_DIR/tomas.zip"
    
    if command -v curl &>/dev/null; then
        curl -fsSL -o "$TMP_ZIP" "$REPO_URL" || fail "Download failed"
    elif command -v wget &>/dev/null; then
        wget -q -O "$TMP_ZIP" "$REPO_URL" || fail "Download failed"
    else
        fail "Neither curl nor wget found. Install one of them."
    fi
    
    unzip -q -o "$TMP_ZIP" -d "$TMP_DIR" || fail "Extraction failed"
    
    # Find extracted directory (GitHub creates a folder like Agent-For-TOM-prototype2-refactoring/)
    EXTRACTED=$(find "$TMP_DIR" -maxdepth 1 -type d -name "Agent-For-TOM-*" | head -1)
    if [ -z "$EXTRACTED" ]; then
        # Try alternate naming
        EXTRACTED=$(find "$TMP_DIR" -maxdepth 1 -type d ! -path "$TMP_DIR" | head -1)
    fi
    
    if [ -n "$EXTRACTED" ]; then
        rm -rf "$SRC_DIR"
        mv "$EXTRACTED" "$SRC_DIR"
        ok "Downloaded and extracted to $SRC_DIR"
    else
        fail "Could not find extracted directory"
    fi
    
    rm -rf "$TMP_DIR"
fi

# ── Create virtual environment ──
header "Creating virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON" -m venv "$VENV_DIR" || fail "Failed to create venv"
    ok "Virtual environment created"
else
    ok "Virtual environment already exists"
fi

# Determine pip path
if [ -f "$VENV_DIR/bin/pip" ]; then
    PIP="$VENV_DIR/bin/pip"
elif [ -f "$VENV_DIR/Scripts/pip" ]; then
    PIP="$VENV_DIR/Scripts/pip"
else
    PIP="$VENV_DIR/bin/pip3"
fi

# ── Install dependencies ──
header "Installing Python dependencies..."
"$PIP" install --quiet --upgrade pip 2>/dev/null || true
REQ_FILE="$SRC_DIR/requirements.txt"
if [ -f "$REQ_FILE" ]; then
    "$PIP" install --quiet -r "$REQ_FILE" && ok "Dependencies installed" || warn "Some dependencies may have failed"
else
    warn "No requirements.txt found"
fi

# ── Create launcher script ──
header "Creating launcher..."
LAUNCHER="$BIN_DIR/tomas"
cat > "$LAUNCHER" << LAUNCHEREOF
#!/usr/bin/env bash
# TOMAS launcher — installed by install.sh
TOMAS_DIR="$INSTALL_DIR"
PYTHON="\$TOMAS_DIR/.venv/bin/python3"
CLI="\$TOMAS_DIR/src/agent_cli.py"

if [ ! -f "\$PYTHON" ]; then
    PYTHON="\$TOMAS_DIR/.venv/bin/python"
fi

if [ ! -f "\$PYTHON" ]; then
    PYTHON="\$TOMAS_DIR/.venv/Scripts/python"
fi

if [ ! -f "\$PYTHON" ]; then
    echo "ERROR: TOMAS venv not found"
    echo "Reinstall with: curl -fsSL https://raw.githubusercontent.com/oleksandkov/Agent-For-TOM/prototype2-refactoring/install.sh | bash"
    exit 1
fi

exec "\$PYTHON" "\$CLI" "\$@"
LAUNCHEREOF
chmod +x "$LAUNCHER"
ok "Created launcher: $LAUNCHER"

# Also create a TOMAS (uppercase) symlink for consistency
if [ ! -f "$BIN_DIR/TOMAS" ]; then
    ln -sf "$LAUNCHER" "$BIN_DIR/TOMAS" 2>/dev/null || true
fi

# ── Create default instructions ──
# Default AGENT.md (local-level agent identity)
AGENT_INSTR_FILE="$INSTRUCTIONS_DIR/AGENT.md"
if [ ! -f "$AGENT_INSTR_FILE" ]; then
    cat > "$AGENT_INSTR_FILE" << 'EOF'
# Agent Identity

- Your name is TOMAS agent.
- Each report must be ended with My Lord.
EOF
    ok "Created agent identity: $AGENT_INSTR_FILE"
else
    ok "Agent identity file already exists (keeping existing)"
fi

# Instructions README
INSTR_README="$INSTRUCTIONS_DIR/README.md"
if [ ! -f "$INSTR_README" ]; then
    cat > "$INSTR_README" << 'EOF'
# TOMAS Agent Instructions

This folder contains **global instructions** that apply to every TOMAS
session, regardless of the project you're working on.

## How it works

- Every `.md` file in this folder is loaded in alphabetical order and
  merged into the agent's system prompt.
- Use these files to set persistent preferences, coding standards, and
  default behaviour.

## Project-level instructions

You can also add instructions per project:

1. Place `AGENT.md` or `agent.md` in the project root directory.
2. OR place `<project-name>.md` in the `project/` subfolder here.

Project-level instructions are loaded on top of global instructions.

## Example files

- `AGENT.md` — local agent identity (safe to edit or delete)
- `project/` — per-project instruction files
EOF
    ok "Created instructions README: $INSTR_README"
fi

ok "Sessions directory: $SESSIONS_DIR"

# ── Set up .env ──
header "Configuring environment..."
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << EOF
# TOMAS configuration (created by install.sh)
# Required: set your API key below
ANTHROPIC_API_KEY=
# Optional: API base URL (default: https://api.anthropic.com)
# ANTHROPIC_BASE_URL=
# Optional: model name (default: claude-sonnet-4-5)
# AGENT_MODEL=claude-sonnet-4-5
# Optional: "1" to auto-approve low-risk tools
# AGENT_AUTO_APPROVE=1
EOF
    ok "Created $ENV_FILE"
    warn "Edit $ENV_FILE to add your ANTHROPIC_API_KEY"
else
    ok ".env already exists"
fi

# ── Add to PATH ──
header "Updating PATH..."
SHELL_CONFIG=""

if [ -f "$HOME/.zshrc" ]; then
    SHELL_CONFIG="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_CONFIG="$HOME/.bashrc"
elif [ -f "$HOME/.bash_profile" ]; then
    SHELL_CONFIG="$HOME/.bash_profile"
fi

if [ -n "$SHELL_CONFIG" ]; then
    PATH_LINE="export PATH=\"\$PATH:$BIN_DIR\""
    if ! grep -qF "$BIN_DIR" "$SHELL_CONFIG" 2>/dev/null; then
        echo "" >> "$SHELL_CONFIG"
        echo "# Added by TOMAS install" >> "$SHELL_CONFIG"
        echo "$PATH_LINE" >> "$SHELL_CONFIG"
        ok "Added $BIN_DIR to PATH in $SHELL_CONFIG"
    else
        ok "Already in PATH: $BIN_DIR"
    fi
else
    warn "Could not detect shell config. Add this line manually:"
    echo "    export PATH=\"\$PATH:$BIN_DIR\""
fi

# Also update current session PATH
export PATH="$BIN_DIR:$PATH"

# ── Create uninstaller ──
header "Creating uninstaller..."
cat > "$BIN_DIR/uninstall-tomas" << UNINSTALLEOF
#!/usr/bin/env bash
# Uninstall TOMAS
echo ""
echo "  Removing TOMAS..."
TOMAS_DIR="$INSTALL_DIR"
BIN_DIR="$BIN_DIR"

# Remove from shell config
for cfg in "\$HOME/.zshrc" "\$HOME/.bashrc" "\$HOME/.bash_profile"; do
    if [ -f "\$cfg" ]; then
        sed -i '' "\|$BIN_DIR|d" "\$cfg" 2>/dev/null || sed -i "\|$BIN_DIR|d" "\$cfg" 2>/dev/null || true
    fi
done
echo "  ✓ Removed \$BIN_DIR from PATH config"

# Remove install directory
if [ -d "\$TOMAS_DIR" ]; then
    rm -rf "\$TOMAS_DIR"
    echo "  ✓ Deleted \$TOMAS_DIR"
fi

echo ""
echo "  TOMAS has been uninstalled."
echo "  Close and reopen your terminal for PATH changes to take effect."
UNINSTALLEOF
chmod +x "$BIN_DIR/uninstall-tomas"
ok "Created uninstaller: $BIN_DIR/uninstall-tomas"

# ── Done ──
echo ""
echo -e "  ${CYAN}╔══════════════════════════════════════════╗${RESET}"
echo -e "  ${CYAN}║        Installation Complete! 🎉         ║${RESET}"
echo -e "  ${CYAN}╚══════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${BOLD}📍 Installed to:${RESET} $INSTALL_DIR"
echo -e "  ${BOLD}🐍 Python:${RESET}       $VENV_DIR"
echo -e "  ${BOLD}🔧 Launchers:${RESET}    $BIN_DIR"
echo -e "  ${BOLD}📋 Instructions:${RESET} $INSTRUCTIONS_DIR"
echo -e "  ${BOLD}💾 Sessions:${RESET}     $SESSIONS_DIR"
echo -e "  ${BOLD}🧠 Self-improve:${RESET}  $SELF_IMPROVE_DIR"
echo -e "  ${BOLD}📝 Memory:${RESET}        $MEMORY_DIR"
echo -e "  ${BOLD}📒 Self-notes:${RESET}    $SELF_NOTES_DIR"
echo ""
echo -e "  ${YELLOW}─────────────────────────────────────────────${RESET}"
echo -e "  ${YELLOW}New features:${RESET}"
echo -e "  ${CYAN}💾 Sessions${RESET}     Auto-saved on exit. Browse/continue from menu."
echo -e "  ${CYAN}🧠 Self-improve${RESET}  Patterns, tips, and auto-generated skills."
echo -e "  ${CYAN}📝 Memory${RESET}        Agent memory persists across projects."
echo -e "  ${CYAN}📒 Self-notes${RESET}    The agent can write and retrieve notes about itself."
echo -e "  ${CYAN}📋 Instructions${RESET} Edit ~/.tomas/instructions/ for global agent rules."
echo -e "  ${CYAN}📄 Project config${RESET} Put AGENT.md in your project root for per-project rules."
echo ""
echo -e "  ${YELLOW}─────────────────────────────────────────────${RESET}"
echo -e "  ${YELLOW}To use TOMAS now:${RESET}"
echo ""
echo -e "  Close this terminal and open a NEW one, then:"
echo -e "    ${CYAN}tomas${RESET}"
echo ""
echo -e "  First time? Edit your API key in:"
echo -e "    ${DIM}$ENV_FILE${RESET}"
echo ""
echo -e "  Or use this in your current terminal:"
echo -e "    ${CYAN}tomas --help${RESET}"
echo ""
echo -e "  ${DIM}To update TOMAS, re-run the install command.${RESET}"
echo -e "  ${DIM}To uninstall:  uninstall-tomas${RESET}"
echo -e "  ${YELLOW}─────────────────────────────────────────────${RESET}"
echo ""

"""ANSI colour constants for terminal rendering.

These live in adapters/ rather than core/ on purpose: colour is a property of
one particular front end. `agent.py` keeps its own copies for the TUI it still
owns; this module is what the event renderer uses.
"""

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
BOLD_OFF = '\033[22m'

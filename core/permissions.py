"""
Permission and continuation decisions, as an interface rather than a blocking
console read.

The core never reads stdin. It asks a PermissionResponder, and each front end
implements that however it likes: the terminal with a typed prompt, a desktop
app with a modal, a test with a canned script.
"""

from __future__ import annotations

from typing import Literal, Protocol

Decision = Literal["allow", "deny", "always_allow_this_call"]


class PermissionResponder(Protocol):
    def ask(self, event) -> Decision: ...

    def ask_continue(self, event) -> bool: ...


class AutoApprove:
    """Headless / test / YOLO: approve everything and keep working."""

    def ask(self, event) -> Decision:
        return "allow"

    def ask_continue(self, event) -> bool:
        return True


class DenyAll:
    """Safe default for unattended runs."""

    def ask(self, event) -> Decision:
        return "deny"

    def ask_continue(self, event) -> bool:
        return False


class ApprovalStore:
    """Session-scoped approvals. Never modifies risk tiers.

    Answering "always" used to rewrite RISK_LEVELS[name] = "low" for the rest
    of the process, so approving one `run_command` (say `git status`) silently
    auto-approved every later run_command, any command at all. Approvals are
    scoped to the argument the user actually saw instead.
    """

    def __init__(self) -> None:
        self._approved: set[tuple[str, str]] = set()

    @staticmethod
    def _signature(name: str, args: dict) -> str:
        # Scope to the meaningful argument, not the whole payload — a user
        # approving `git status` should not thereby approve `rm -rf`.
        args = args or {}
        key = args.get("command") or args.get("file_path") or args.get("path") or ""
        return f"{name}:{str(key)[:200]}"

    def is_approved(self, name: str, args: dict) -> bool:
        return (name, self._signature(name, args)) in self._approved

    def approve(self, name: str, args: dict) -> None:
        self._approved.add((name, self._signature(name, args)))

    def clear(self) -> None:
        self._approved.clear()

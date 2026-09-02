"""Which local models this session put in memory, and who else still wants them.

Ollama keeps a model resident for five minutes after the last request
(`OLLAMA_KEEP_ALIVE`), which is the right default for a chat window somebody
is still typing in and the wrong one for a session that has ended. Measured on
a machine with three models left over from earlier runs: gemma3:4b,
smallthinker:3b and qwen2.5-coder:3b, **8.5 GB of VRAM** held by nothing.

Freeing it is one HTTP call. Knowing whether it is safe to make is the whole
of this module, because "the session ended" is not the same question as
"nothing is using this model": a second TOMAS window, or the same user's other
terminal, is a live claim on a runner this process must not evict out from
under it.

**Liveness is a held lock, not a recorded PID.** A claim file naming a process
id proves only that the id was written down — the process may have crashed, or
been killed, or the id may since have been reused by something unrelated, and
in each case a stale file would suppress unloading forever with no way to tell
that from a real session. So each session holds an exclusive lock on its own
lock file for as long as it runs, and the operating system releases it when
the process dies however it dies. Another session tests a claim by trying to
take that lock: refused means the owner is alive, granted means the owner is
gone and the claim is litter to be swept up. `msvcrt` is what does the locking
and this project is a Windows REPL already (see CLAUDE.md); `os.kill(pid, 0)`
stands in elsewhere so the module imports and degrades rather than raising.

Nothing here raises. A session that cannot write a claim, cannot read someone
else's, or cannot reach Ollama at all must still exit cleanly — the worst
outcome of every failure in this file is that a model stays loaded for the
five minutes it would have stayed loaded anyway.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

CLAIMS_DIR = Path.home() / ".tomas" / "ollama_claims"

#: Seconds to wait for Ollama to acknowledge an unload. Generous because the
#: server may be mid-generation for another client when the request lands, and
#: mean because this runs on the exit path: a user who has closed the session
#: is not waiting to watch VRAM be freed.
UNLOAD_TIMEOUT = 10.0

#: This process's open lock file handle, held for the life of the session.
#: Module-level because the lock *is* the handle staying open — storing it in
#: a local would have it garbage-collected, closing the file and releasing the
#: lock while the session was still running.
_lock_handle = None

#: model -> (native API root, request headers) for everything this session has
#: actually sent a request to. Held in memory and never written to the claim
#: file: another session needs to know *which* models are spoken for, and has
#: no business reading an endpoint's credentials to find out. Keyed by model
#: rather than kept as a set so that a provider switch mid-session still
#: unloads each model against the server it was loaded on.
_claimed: dict = {}


def _pid() -> int:
    return os.getpid()


def _paths(pid: int) -> tuple:
    return (CLAIMS_DIR / f"{pid}.lock", CLAIMS_DIR / f"{pid}.json")


def _try_lock(handle) -> bool:
    """Take an exclusive lock on the first byte, without blocking.

    True when this call acquired it — which, for another session's file, means
    that session is gone. False when it is held by a live process.
    """
    try:
        import msvcrt
    except ImportError:
        return False
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _owner_is_alive(pid: int) -> bool:
    """Whether the session that wrote claim `pid` is still running."""
    lock_path, _ = _paths(pid)
    try:
        import msvcrt  # noqa: F401
    except ImportError:
        # POSIX fallback, for a port or a test run off Windows. Signal 0
        # performs the permission and existence checks and delivers nothing.
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
        except Exception:
            return True
    if not lock_path.exists():
        # A json with no lock beside it was never a running session, or its
        # lock has already been swept. Either way nothing holds the model.
        return False
    try:
        with open(lock_path, "r+b") as handle:
            if _try_lock(handle):
                # We got it, so nobody else has it: the owner is gone. The
                # lock is dropped when this `with` closes the handle.
                return False
            return True
    except OSError:
        # Cannot even open it — on Windows that usually means it is open
        # elsewhere. Read as alive: refusing to unload is the safe error.
        return True


def claim(model: str, root: str = "", headers: Optional[dict] = None) -> None:
    """Record that this session is using `model`, served from `root`.

    Called on the turn path, so it does nothing at all once the model is
    already claimed — the file is rewritten only when the set actually
    changes, which is at most once per model per session.
    """
    if not model or model in _claimed:
        return
    global _lock_handle
    _claimed[model] = (root, dict(headers or {}))
    try:
        CLAIMS_DIR.mkdir(parents=True, exist_ok=True)
        lock_path, json_path = _paths(_pid())
        if _lock_handle is None:
            # "a+b" rather than "w+b": truncating would momentarily empty a
            # file another process may be testing at that instant.
            _lock_handle = open(lock_path, "a+b")
            _lock_handle.write(b"\0")
            _lock_handle.flush()
            _try_lock(_lock_handle)
        json_path.write_text(
            json.dumps({"pid": _pid(), "models": sorted(_claimed),
                        "started": time.time()}),
            encoding="utf-8")
    except OSError:
        # A session that cannot write its claim still runs; it simply will not
        # unload on the way out, which is what happened before this existed.
        pass


def release() -> None:
    """Drop this session's claim. Safe to call twice, and on a session that
    never claimed anything."""
    global _lock_handle
    lock_path, json_path = _paths(_pid())
    if _lock_handle is not None:
        try:
            import msvcrt
            _lock_handle.seek(0)
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        try:
            _lock_handle.close()
        except OSError:
            pass
        _lock_handle = None
    for path in (json_path, lock_path):
        try:
            path.unlink()
        except OSError:
            pass
    # Cleared last, so a second call is a no-op rather than a second round of
    # unload attempts against models this session has already let go of.
    _claimed.clear()


def claimed_models() -> list:
    """What this session has actually sent requests to, in a stable order."""
    return sorted(_claimed)


def other_holders(model: str) -> list:
    """PIDs of *other* live sessions using `model`.

    Sweeps dead claims as it goes: a crashed session's files are removed the
    first time anyone looks, so one bad exit cannot suppress unloading for
    every session after it.
    """
    holders = []
    try:
        entries = list(CLAIMS_DIR.glob("*.json"))
    except OSError:
        return holders
    for path in entries:
        try:
            pid = int(path.stem)
        except ValueError:
            continue
        if pid == _pid():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            continue
        if _owner_is_alive(pid):
            if model in (data.get("models") or []):
                holders.append(pid)
            continue
        # Dead owner: sweep both files, whether or not it wanted this model.
        for stale in _paths(pid):
            try:
                stale.unlink()
            except OSError:
                pass
    return holders


def unload(root: str, model: str, headers: Optional[dict] = None) -> bool:
    """Ask Ollama to drop `model` from memory now.

    `keep_alive: 0` on the native API is the documented way and the only one:
    the OpenAI shim has no equivalent field, the same reason it has no
    `num_ctx` (see `provider_manager.OLLAMA_DEFAULT_NUM_CTX`). `/api/generate`
    with no prompt performs no generation — it is a load/unload control call.
    """
    if not root or not model:
        return False
    import urllib.request
    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    request = urllib.request.Request(
        f"{root}/api/generate", data=body,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=UNLOAD_TIMEOUT) as response:
            response.read()
        return True
    except Exception:
        return False


def unload_session_models() -> list:
    """Release this session's claims and unload what nobody else wants.

    Returns `(model, freed, note)` per model this session used, so the caller
    can say what happened rather than guessing: `freed` is True only when
    Ollama acknowledged the unload, and `note` names the reason when it did
    not — a live sibling session, or a server that did not answer.

    Order matters. This session's own claim is released *first*, so that
    `other_holders` is asking about everyone else and cannot find this
    process holding the model it is about to unload.
    """
    targets = dict(_claimed)
    release()
    results = []
    for model in sorted(targets):
        root, headers = targets[model]
        holders = other_holders(model)
        if holders:
            count = len(holders)
            results.append((model, False,
                            f"still in use by {count} other session"
                            f"{'' if count == 1 else 's'}"))
            continue
        if unload(root, model, headers):
            results.append((model, True, ""))
        else:
            results.append((model, False, "Ollama did not answer"))
    return results

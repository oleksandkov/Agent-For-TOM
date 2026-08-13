"""Bounded probes for local services, plus a small TTL cache.

Every slow menu in the TUI was slow for the same reason: it asked a local
service whether it was there, over HTTP, with a timeout measured in seconds.
On this machine a TCP connect to a closed port neither completes nor gets
refused — it is silently dropped — so *every* such check paid its full
timeout. Measured before this module existed:

    zen_proxy.check_status()      2,099 ms   (timeout=2, always hit in full)
    list_models(ollama)          12,261 ms   (3 sequential attempts, 8 s each)

The fix is not a smaller HTTP timeout. It is to ask a cheaper question first:
*is anything listening?* `port_open` answers that with a raw socket and a
timeout we choose, so a dead service costs a fixed ~200 ms instead of seconds,
and a live one costs about a millisecond.

`cached` then makes the second visit to a page free. Both are deliberately
tiny and dependency-free — they run before the heavy imports do.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any, Callable, Iterable, Optional, Sequence
from urllib.parse import urlparse

# Long enough for a loopback service to answer, short enough that a dead port
# is not felt as a hang. Loopback connects that succeed take well under 1 ms.
DEFAULT_PORT_TIMEOUT = 0.20

# How long a port's up/down answer is trusted. Short, so that starting a
# local service by hand is picked up almost immediately.
PORT_CACHE_TTL = 3.0


def port_open(host: str, port: int, timeout: float = DEFAULT_PORT_TIMEOUT) -> bool:
    """True if something accepts a TCP connection on host:port within timeout.

    Never raises. A dropped (firewalled) port costs exactly `timeout`, which
    is the whole point — the caller gets a bounded answer instead of inheriting
    an HTTP stack's multi-second default.

    The answer is memoised for `PORT_CACHE_TTL`, because callers ask it in
    bursts: `list_models` alone probes the same dead endpoint three times in a
    row while trying three URL shapes. The TTL is short enough that a service
    started by hand is noticed a moment later.
    """
    return cached(f"port:{host}:{port}", PORT_CACHE_TTL,
                  lambda: _port_open_now(host, port, timeout))


def _port_open_now(host: str, port: int, timeout: float) -> bool:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return False

    # A name like "localhost" resolves to both ::1 and 127.0.0.1 on Windows,
    # and the dead one is tried first. Budget is per family so the total stays
    # bounded, and any success wins.
    per_family = timeout / max(1, min(len(infos), 2))
    for family, socktype, proto, _canon, sockaddr in infos[:2]:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(per_family)
            if sock.connect_ex(sockaddr) == 0:
                return True
        except OSError:
            continue
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    return False


def url_port_open(url: str, timeout: float = DEFAULT_PORT_TIMEOUT) -> bool:
    """`port_open` for a URL, inferring the default port from the scheme.

    A non-local URL is reported open without probing: this guard exists to
    avoid waiting on services that are not running on this machine, and a
    remote host's reachability is the HTTP layer's business, not ours.
    """
    try:
        parsed = urlparse(url if "://" in url else f"http://{url}")
    except ValueError:
        return True
    host = parsed.hostname
    if not host:
        return True
    if host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return port_open(host, port, timeout)


# ── Abandonable fan-out ──────────────────────────────────────────────────

#: Long enough for a cold MCP server that is still downloading its package,
#: short enough that nothing waits on it forever.
DEFAULT_FAN_OUT_TIMEOUT = 30.0


class _Unfinished:
    """Marks a slot no worker has written yet.

    A sentinel rather than `None`, because `None` is a perfectly good result
    for a probe that ran and found nothing.
    """

    def __repr__(self) -> str:
        return "<unfinished>"


_UNFINISHED = _Unfinished()


def fan_out(work: Callable[[Any], Any],
            items: Sequence[Any],
            max_workers: int = 8,
            timeout: Optional[float] = DEFAULT_FAN_OUT_TIMEOUT,
            ) -> tuple[list[tuple[Any, Any]], list[Any]]:
    """Run `work(item)` over `items` in parallel, and be able to walk away.

    Returns `(done, unfinished)` — `done` as `[(item, result), …]` and
    `unfinished` as the items still running when the budget ran out. Anything
    `work` raises is returned as the exception object rather than propagated:
    one bad server must not take the fan-out down.

    **Both lists come back in the order `items` was given**, never completion
    order. Not a nicety: MCP tool names are resolved by claim order — the
    second server to claim a name gets `mcp_<server>_<tool>` — so results
    arriving in whatever order the network happened to answer renamed a user's
    tools differently on every startup. Returning completion order bought
    nothing and cost determinism, which is the one property `pool.map` had
    that was worth keeping.

    **Why not `ThreadPoolExecutor`.** Its worker threads are non-daemon and
    `concurrent.futures` registers an `atexit` hook that joins every one of
    them. So a probe that is "abandoned" is not abandoned at all — it is
    deferred to interpreter shutdown, where it blocks with no UI, no message
    and no way to interrupt it. Measured: a process that did nothing but start
    the MCP connection probe on a daemon thread and then exit took 6.6 s to
    die, 6.2 s of it after `main` had finished. Two call sites carried comments
    explaining that they had bounded the wait, and both were wrong in the same
    way — `wait(timeout=…)` bounds the *foreground* wait and nothing else.

    Daemon threads have exactly the property those comments claimed: the
    interpreter does not wait for them, so a slow server delays a menu redraw
    and never delays quitting.
    """
    items = list(items)
    if not items:
        return [], []

    #: Slot per input index, never a shared list — this is what makes the
    #: result order the *input* order regardless of who finishes first. Keying
    #: on the item itself would not do: two servers may compare equal, and
    #: `id()` is only unique while every object is alive.
    slots: list[Any] = [_UNFINISHED] * len(items)
    queue = list(range(len(items)))
    lock = threading.Lock()
    finished = threading.Event()
    remaining = [len(items)]

    def worker():
        while True:
            with lock:
                if not queue:
                    return
                index = queue.pop(0)
            try:
                result = work(items[index])
            except Exception as exc:               # returned, never raised
                result = exc
            with lock:
                slots[index] = result
                remaining[0] -= 1
                if remaining[0] == 0:
                    finished.set()

    for _ in range(max(1, min(max_workers, len(items)))):
        threading.Thread(target=worker, daemon=True).start()

    finished.wait(timeout)
    with lock:
        snapshot = list(slots)
    done = [(items[i], value) for i, value in enumerate(snapshot)
            if value is not _UNFINISHED]
    unfinished = [items[i] for i, value in enumerate(snapshot)
                  if value is _UNFINISHED]
    return done, unfinished


# ── TTL cache ────────────────────────────────────────────────────────────

_CACHE: dict[str, tuple[float, Any]] = {}


def cached(key: str, ttl: float, produce: Callable[[], Any]) -> Any:
    """Return a cached value for `key`, or produce and store one.

    Used for answers that are expensive, stable over the seconds a user spends
    in a menu, and re-asked every time a page is redrawn.
    """
    hit = _CACHE.get(key)
    now = time.monotonic()
    if hit is not None and (now - hit[0]) < ttl:
        return hit[1]
    value = produce()
    _CACHE[key] = (now, value)
    return value


def invalidate(key: str | None = None) -> None:
    """Drop one cached answer, or all of them. Called by explicit refreshes."""
    if key is None:
        _CACHE.clear()
    else:
        _CACHE.pop(key, None)


def peek(key: str, ttl: float) -> tuple[bool, Any]:
    """(is_fresh, value) without producing. Lets a caller draw now and fill later."""
    hit = _CACHE.get(key)
    if hit is None:
        return False, None
    return (time.monotonic() - hit[0]) < ttl, hit[1]


def put(key: str, value: Any) -> None:
    """Store a value produced elsewhere (e.g. by a background thread)."""
    _CACHE[key] = (time.monotonic(), value)

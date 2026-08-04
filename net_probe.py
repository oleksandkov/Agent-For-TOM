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
import time
from typing import Any, Callable
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

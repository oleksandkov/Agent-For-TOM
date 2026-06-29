"""FastAPI router for the memory subsystem (Section 4 contract).

Endpoints (contract frozen here — see ``docs/api.md``):

- ``GET  /v1/memory/core``  → :class:`CoreMemory`
- ``PATCH /v1/memory/core`` → :class:`CoreMemory`

PATCH is optimistically concurrent: the body MUST carry
``expected_version`` matching the stored value, else ``409 Conflict``.

The router holds a single :class:`TomMemory` instance. Tests can
swap it via :func:`set_memory` to inject a stub.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from backend.tom.memory.core import CoreMemoryConflictError
from backend.tom.memory.tom_memory import TomMemory
from backend.tom.memory.types import CoreMemory, CoreMemoryPatch

router = APIRouter(prefix="/v1/memory", tags=["memory"])


def get_memory() -> TomMemory:
    """Dependency override target — tests replace via ``app.dependency_overrides``."""
    return _memory


def set_memory(instance: TomMemory) -> None:
    """Replace the singleton. Process-wide — meant for startup/tests only."""
    global _memory
    _memory = instance


_memory: TomMemory = TomMemory()


@router.get("/core", response_model=CoreMemory)
def read_core(
    memory: Annotated[TomMemory, Depends(get_memory)],
) -> CoreMemory:
    """Return the current core memory snapshot."""
    return memory.read_core()


@router.patch("/core", response_model=CoreMemory)
def patch_core(
    patch: CoreMemoryPatch,
    memory: Annotated[TomMemory, Depends(get_memory)],
) -> CoreMemory:
    """Apply a structured patch; respond ``409`` on version mismatch."""
    try:
        return memory.write_core(patch)
    except CoreMemoryConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "version_mismatch",
                "expected": exc.expected,
                "current_version": exc.current.version,
            },
        ) from exc


__all__: list[str] = ["get_memory", "router", "set_memory"]

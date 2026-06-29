"""FastAPI router for the provider subsystem (Section 5).

Public endpoints:

- ``GET /v1/providers``        — list configured providers + default
- ``GET /v1/providers/{name}/health`` — per-provider health probe
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.tom.providers.registry import ProviderRegistry

router = APIRouter(prefix="/v1/providers", tags=["providers"])
_registry: ProviderRegistry = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    return _registry


def set_registry(registry: ProviderRegistry) -> None:
    """Replace the singleton. Tests use this to inject a stub registry."""
    global _registry
    _registry = registry


@router.get("")
def list_providers(
    registry: Annotated[ProviderRegistry, Depends(get_registry)],
) -> dict[str, object]:
    return {
        "providers": registry.list_config(),
        "default": next(
            (p["name"] for p in registry.list_config() if p["is_default"]),
            None,
        ),
    }


@router.get("/{name}/health")
async def provider_health(
    name: str,
    registry: Annotated[ProviderRegistry, Depends(get_registry)],
) -> dict[str, object]:
    try:
        provider = registry.get(name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    report = await provider.health()
    return report.to_dict()


__all__: list[str] = ["get_registry", "router", "set_registry"]

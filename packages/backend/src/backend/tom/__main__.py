"""TOM CLI entry point.

Subcommands land across the plan:
- `serve`  — Section 2 (skeleton here, full wiring in Section 6)
- `db`     — Section 3
- `providers` — Section 5

Hard rule (AGENTS.md): backend binds only 127.0.0.1:7878.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NoReturn

import uvicorn
from fastapi import FastAPI

from backend.tom import __version__
from backend.tom.branding import (
    API_VERSION,
    COPYRIGHT_NOTICE,
    LICENSE_NAME,
    PRODUCT_NAME,
    PRODUCT_TAGLINE,
    VERSION,
)
from backend.tom.config import Settings, get_settings
from backend.tom.db import init_db as _init_db
from backend.tom.providers.api import router as providers_router


@dataclass(frozen=True)
class _ServeArgs:
    host: str
    port: int


@dataclass(frozen=True)
class _DbInitArgs:
    show_path: bool


@dataclass(frozen=True)
class _ProvidersHealthArgs:
    name: str
    json_output: bool


async def _run_provider_health(name: str) -> int:
    """Resolve and probe a provider by name. Returns shell exit code."""
    import json

    _init_db()  # idempotent; tests use isolated TOM_DATA_DIR
    from backend.tom.providers.api import get_registry

    registry = get_registry()
    try:
        provider = registry.get(name)
    except LookupError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    report = await provider.health()

    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.ok else 1


def _cmd_providers_health(args: _ProvidersHealthArgs) -> int:
    return int(asyncio.run(_run_provider_health(args.name)))


def _build_app(settings: Settings) -> FastAPI:
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    from backend.tom.api.chat import router as chat_router
    from backend.tom.api.deps import set_orchestrator
    from backend.tom.api.messages import router as messages_router
    from backend.tom.api.sessions import router as sessions_router
    from backend.tom.chat import ChatOrchestrator
    from backend.tom.chat.tool_dispatcher import StubDispatcher
    from backend.tom.mcp_bridge.dispatcher import MCPDispatcher
    from backend.tom.mcp_bridge.loader import load_all as load_mcp_manifests
    from backend.tom.mcp_bridge.registry import ServerRegistry
    from backend.tom.memory import TomMemory
    from backend.tom.providers.registry import ProviderRegistry

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        registry = ServerRegistry()
        try:
            discovered = load_mcp_manifests()
            await registry.start_all_async([d.manifest for d in discovered])
            dispatcher: object = MCPDispatcher(registry)
        except Exception as exc:
            print(f"startup: MCP init failed: {exc}", file=sys.stderr)
            dispatcher = StubDispatcher()

        orchestrator = ChatOrchestrator(
            memory=TomMemory(),
            providers=ProviderRegistry(),
            dispatcher=dispatcher,  # type: ignore[arg-type]
        )
        set_orchestrator(orchestrator)
        app.state.mcp_registry = registry
        try:
            yield
        finally:
            await registry.stop_all()

    app = FastAPI(
        title=PRODUCT_NAME,
        version=VERSION,
        description=PRODUCT_TAGLINE,
        license_info={"name": LICENSE_NAME},
        lifespan=lifespan,
    )

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {
            "status": "ok",
            "product": PRODUCT_NAME,
            "version": VERSION,
            "api": API_VERSION,
        }

    @app.get("/v1/brand", tags=["meta"])
    def brand() -> dict[str, str]:
        return {
            "product": PRODUCT_NAME,
            "tagline": PRODUCT_TAGLINE,
            "version": VERSION,
            "api_version": API_VERSION,
            "copyright": COPYRIGHT_NOTICE,
            "license": LICENSE_NAME,
        }

    @app.get("/", tags=["meta"], include_in_schema=False)
    def root() -> dict[str, str]:
        return {"product": PRODUCT_NAME, "version": VERSION}

    from backend.tom.memory.api import router as memory_router

    app.include_router(memory_router)
    app.include_router(providers_router)
    app.include_router(sessions_router)
    app.include_router(messages_router)
    app.include_router(chat_router)
    return app


def _cmd_serve(args: _ServeArgs) -> int:
    if args.host != "127.0.0.1":
        print(
            f"refusing to bind {args.host}:{args.port} — TOM only binds 127.0.0.1",
            file=sys.stderr,
        )
        return 2
    _init_db()  # idempotent; ensures the encrypted DB exists
    settings = get_settings()
    app = _build_app(settings)
    uvicorn.run(app, host=args.host, port=args.port, log_level=settings.log_level.lower())
    return 0


def _cmd_version(_: argparse.Namespace) -> int:
    print(f"{PRODUCT_NAME} {VERSION} (api {API_VERSION}, package {__version__})")
    return 0


def _cmd_db_init(args: _DbInitArgs) -> int:
    path = _init_db()
    print(f"{PRODUCT_NAME} db initialised at {path}")
    if args.show_path:
        print(f"data_dir={path.parent}")
    return 0


def _cmd_db(_: argparse.Namespace) -> int:
    print("usage: tom db <init> [--show-path]", file=sys.stderr)
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tom",
        description=f"{PRODUCT_NAME} — {PRODUCT_TAGLINE}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the HTTP API on the loopback interface")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=7878)

    p_db = sub.add_parser("db", help="database operations")
    db_sub = p_db.add_subparsers(dest="db_command")
    p_db_init = db_sub.add_parser("init", help="initialise the encrypted database")
    p_db_init.add_argument(
        "--show-path",
        action="store_true",
        help="also print the data dir path",
    )

    p_providers = sub.add_parser("providers", help="LLM provider operations")
    providers_sub = p_providers.add_subparsers(dest="providers_command")
    p_health = providers_sub.add_parser(
        "health", help="probe a provider's /health or /tags endpoint"
    )
    p_health.add_argument("name", help="provider name (per provider_configs table)")
    p_health.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="emit machine-readable JSON (default is also JSON)",
    )

    sub.add_parser("version", help="print version and exit")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 1

    if args.command == "serve":
        return _cmd_serve(_ServeArgs(host=args.host, port=args.port))
    if args.command == "version":
        return _cmd_version(args)
    if args.command == "db":
        if args.db_command == "init":
            return _cmd_db_init(_DbInitArgs(show_path=args.show_path))
        return _cmd_db(args)
    if args.command == "providers":
        if args.providers_command == "health":
            return _cmd_providers_health(
                _ProvidersHealthArgs(name=args.name, json_output=args.json_output)
            )
        print("usage: tom providers <health> <name>", file=sys.stderr)
        return 2

    parser.print_help()
    return 1


def _entrypoint() -> NoReturn:
    raise SystemExit(main(sys.argv[1:]))


if __name__ == "__main__":
    _entrypoint()

"""TOM CLI entry point.

Subcommands land across the plan:
- `serve`  — Section 2 (skeleton here, full wiring in Section 6)
- `db`     — Section 3
- `providers` — Section 5

Hard rule (AGENTS.md): backend binds only 127.0.0.1:7878.
"""

from __future__ import annotations

import argparse
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


@dataclass(frozen=True)
class _ServeArgs:
    host: str
    port: int


def _build_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title=PRODUCT_NAME,
        version=VERSION,
        description=PRODUCT_TAGLINE,
        license_info={"name": LICENSE_NAME},
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

    return app


def _cmd_serve(args: _ServeArgs) -> int:
    if args.host != "127.0.0.1":
        print(
            f"refusing to bind {args.host}:{args.port} — TOM only binds 127.0.0.1",
            file=sys.stderr,
        )
        return 2
    settings = get_settings()
    app = _build_app(settings)
    uvicorn.run(app, host=args.host, port=args.port, log_level=settings.log_level.lower())
    return 0


def _cmd_version(_: argparse.Namespace) -> int:
    print(f"{PRODUCT_NAME} {VERSION} (api {API_VERSION}, package {__version__})")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tom",
        description=f"{PRODUCT_NAME} — {PRODUCT_TAGLINE}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the HTTP API on the loopback interface")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=7878)

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

    parser.print_help()
    return 1


def _entrypoint() -> NoReturn:
    raise SystemExit(main(sys.argv[1:]))


if __name__ == "__main__":
    _entrypoint()

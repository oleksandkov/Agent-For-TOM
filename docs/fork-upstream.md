# Fork & upstream policy

> **Hard rule (AGENTS.md):** Never edit Langflow core. All new logic in
> `packages/backend/src/backend/tom/`. Document any divergence here.

## Current state (Section 2)

| Component | Source | Version | Notes |
|---|---|---|---|
| **Letta SDK** | PyPI | `letta == 0.16.8` | Pinned in `pyproject.toml`. Wraps agent memory + tool orchestration. |
| **Langflow runtime** | *not a direct dep* | n/a | Letta 0.16.x no longer pulls `langflow-base` transitively. The frameworks have been split upstream. TOM's "deep fork of Langflow" is now expressed as: TOM imports Letta, all custom logic lives in `backend/tom/`, Langflow is not in our dep graph at all. |

> Decision recorded: we are NOT vendoring Langflow. If/when we need
> Langflow's flow-editor surface (visual graph), it ships as its own
> process and talks to Letta via HTTP — not embedded. Revisit if a future
> section needs in-process Langflow.

## Where TOM custom code lives

```
packages/backend/src/backend/tom/
├── __init__.py           # package marker, exposes __version__
├── __main__.py           # CLI entry point (tom serve, tom version)
├── branding/             # product name, version, support links (single source of truth)
├── config.py             # pydantic-settings (TOM_HOST, TOM_PORT, TOM_LOG_LEVEL, ...)
├── instructions.py       # system prompt + per-user overrides (Section 9)
├── memory/               # 3-tier memory (Section 4)
├── db/                   # SQLCipher + sqlite-vec + keyring (Section 3)
├── api/                  # FastAPI routes (Section 6)
├── providers/            # LLM provider abstraction (Section 5)
├── mcp_bridge/           # MCP client + server lifecycle (Section 7)
├── improvement/          # self-improvement pipeline (Section 10)
└── security/             # path guards, audit, rate limiting (Section 11)
```

Nothing under `packages/backend/src/backend/tom/` may import from
`langflow.*` or `letta.server.*` directly. All wrapping happens behind
interfaces owned by TOM (`TomMemory`, `Provider`, `McpClient`).

## Versioning cadence

- **Patch bump** (`0.1.x`): bug fixes inside TOM, no upstream dep moves.
- **Minor bump** (`0.x.0`): new TOM feature, or upstream dep bump that
  doesn't change public surface.
- **Major bump** (`x.0.0`): Letta/Langflow breaking change, or a TOM API
  contract change that the frontend colleague has been notified about via
  `docs/api.md`.

## Upgrade procedure (when we do bump Letta)

1. Bump `letta = "==X.Y.Z"` in `packages/backend/pyproject.toml`.
2. `uv lock --upgrade-package letta` (or just `uv sync` after the bump).
3. `uv run pytest -q` — fix anything that broke (mostly signature changes).
4. `uv run mypy src/backend/tom` — fix type fallout.
5. Smoke `scripts/dev.sh` and curl `/healthz` on loopback.
6. Update the row in the table above.
7. Mention the bump in the next PR description.

## When we'd actually fork Langflow

Today we don't. If a future need arises (a Langflow bug we can't work
around, or a TOM feature that needs to hook into Langflow internals):

1. Open a GitHub issue describing the gap and why wrapping isn't enough.
2. Vendor a fork at `packages/backend/vendor/langflow/` (read-only import
   path, not on `sys.path` by default).
3. Add `langflow = "<source>"` to `pyproject.toml` via `[tool.uv.sources]`.
4. Update this document with the fork SHA, the rationale, and the
   rebase/merge plan (who, when, how).
5. Add a CI gate that fails the build if a fork commit is older than N
   weeks without a rebase attempt.

## Sync points (api contract freezes)

The following contracts must be frozen in `docs/api.md` and the frontend
colleague notified **before** implementation starts. See AGENTS.md.

- Section 4 — `GET/PATCH /v1/memory/core`
- Section 6 — `POST /v1/chat`, `/v1/sessions*`, `/v1/sessions/{id}/messages`
- Section 7 — MCP manifest schema (`schemas/mcp-manifest.schema.json`)
- Section 10 — `GET /v1/improvement/inbox`, `POST /v1/skills/{id}/{approve,reject}`
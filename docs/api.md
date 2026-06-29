# TOM REST API — contract (Section 6 frozen)

> Frozen at end of Section 6. Subsequent sections may **add** new
> endpoints/endpoints-fields; they MUST NOT change existing response
> shapes or status codes without an explicit contract bump here.

The base URL is `http://127.0.0.1:7878` (loopback only — see
`AGENTS.md` §Hard rules). All `/v1/*` endpoints accept and return
`application/json` unless called out as `text/event-stream`.

Standard error envelope:

```json
{ "detail": "..." }
```

For `409` (version mismatch):

```json
{ "detail": { "reason": "version_mismatch", "expected": 0, "current_version": 1 } }
```

---

## Memory (Section 4)

| Method | Path              | Body                                  | Result                                |
| ------ | ----------------- | ------------------------------------- | ------------------------------------- |
| GET    | `/v1/memory/core` | —                                     | `CoreMemory`                          |
| PATCH  | `/v1/memory/core` | `CoreMemoryPatch` (with `expected_version`) | `CoreMemory` (new version)            |

PATCH 200 → new snapshot. 409 → version mismatch. 422 → bad body.

---

## Providers (Section 5)

| Method | Path                                | Body | Result                          |
| ------ | ----------------------------------- | ---- | ------------------------------- |
| GET    | `/v1/providers`                     | —    | `{providers: [...], default}`  |
| GET    | `/v1/providers/{name}/health`      | —    | `HealthReport.to_dict()`        |
|        |                                     |      | 404 if name unknown              |

---

## Sessions (Section 6)

| Method | Path                                    | Body                                       | Result |
| ------ | --------------------------------------- | ------------------------------------------ | ------ |
| POST   | `/v1/sessions`                          | `{title?, provider?, model?}`             | new session row, 201 |
| GET    | `/v1/sessions`                          | —                                          | list of sessions     |
| GET    | `/v1/sessions/{id}`                     | —                                          | one session          |
| PATCH  | `/v1/sessions/{id}`                     | `{title?, provider?, model?}`             | updated session      |
| DELETE | `/v1/sessions/{id}`                     | —                                          | 204                  |
| POST   | `/v1/sessions/{id}/close`               | —                                          | closed session + archive trigger |

```jsonc
// Session shape (request body too)
{
  "id": "uuid",
  "title": "...",
  "provider": "ollama",
  "model": "qwen2:1.5b",
  "status": "open" | "closed",
  "total_tokens": 0,
  "created_at": "...",
  "updated_at": "..."
}
```

`POST /v1/sessions/{id}/close` returns the same shape with
`status="closed"`. If the archive hook produced an `MemoryRecord`,
`archived_memory_id` is included; otherwise omitted.

---

## Messages (Section 6)

| Method | Path                                          | Query                | Result |
| ------ | --------------------------------------------- | -------------------- | ------ |
| GET    | `/v1/sessions/{id}/messages`                  | `?limit=200&offset=0` | message list (oldest-first) |

```jsonc
{
  "session_id": "...",
  "count": 2,
  "limit": 200,
  "offset": 0,
  "items": [
    { "id": "...", "session_id": "...", "role": "user" | "assistant" | "system" | "tool",
      "content": "...", "tokens": 0, "tool_calls": null | {...},
      "created_at": "..." }
  ]
}
```

`limit` range is 1..1000. Errors: 400 (limit out of range / negative offset),
404 (session missing).

---

## Chat (Section 6) — SSE streaming

`POST /v1/chat` — Server-Sent Events.

Request:

```jsonc
{
  "session_id": "uuid",
  "content": "user message",
  "attachments": [...]   // v0.1: recorded, ignored otherwise
}
```

Response: `200 OK` with `Content-Type: text/event-stream`. The body is a
sequence of SSE messages of the form:

```
event: <type>
data: <json payload>

```

`event: token` — content delta from the model.

```jsonc
{ "text_delta": "Hel" }
```

`event: tool_call` — model-emitted call before dispatch.

```jsonc
{ "id": "t1", "name": "echo", "arguments": { ... } }
```

`event: tool_result` — dispatcher response (or error string).

```jsonc
{ "id": "t1", "name": "echo", "result": { ... } }
// or:
{ "id": "t1", "name": "echo", "error": "Reason: ..." }
```

`event: done` — turn complete.

```jsonc
{
  "user_message_id": "...",
  "assistant_message_id": "...",
  "total_tokens": 42
}
```

`event: session_required` — session id missing / unknown / closed.
Turn aborts; no further events.

```jsonc
{ "reason": "session_not_found" | "session_closed", "session_id": "..." }
```

`event: error` — anything else; turn aborts.

```jsonc
{ "reason": "provider_unavailable" | "no_default_provider"
        | "internal_error" | "tool_loop_exceeded" | ...,
  "detail": "..." }
```

---

## curl examples

### Open a session

```bash
curl -s -X POST http://127.0.0.1:7878/v1/sessions \
     -H 'Content-Type: application/json' \
     -d '{"title":"first chat","provider":"local"}'
# → { "id": "...", ... }
```

### Send a message (stream the SSE)

```bash
curl -N -X POST http://127.0.0.1:7878/v1/chat \
     -H 'Content-Type: application/json' \
     -d "{\"session_id\":\"$sid\",\"content\":\"hi\"}"
```

### List messages

```bash
curl -s http://127.0.0.1:7878/v1/sessions/$sid/messages | jq
```

### Close

```bash
curl -X POST http://127.0.0.1:7878/v1/sessions/$sid/close | jq
# → includes "archived_memory_id" if the hook produced one
```

---

## Notes for the desktop colleague

- The `/v1/chat` SSE protocol is the *only* streaming chat API. The
  desktop UI MUST consume `event:`/`data:` lines and surface only the
  `token` events as user-visible text. Everything else is system-level
  bookkeeping.
- `tool_call`/`tool_result` events are how the agent loop is signalled
  to the UI; §7 (MCP) wires the dispatcher behind them. v0.1 emits
  `tool_call` events without executing them.
- `session_required` events are the UI's prompt to call
  `POST /v1/sessions` (or surface the inline session picker) and retry.
- The desktop SHOULD NOT bind to anything other than `127.0.0.1:7878`.

# Phase 0 — Make It Work

> **STATUS: IMPLEMENTED — 2026-08-02.** All four items done, verified live against
> `deepseek-v4-flash-free`. Tool round-trip 7.8s (was 100% failure), streaming 2.4s
> (was 100% 502), conversation memory confirmed. 25 unit tests pass (17 new);
> integration suite 40/42, the 2 failures pre-existing and unrelated
> (they assert text that is no longer in `CLAUDE.md`).
>
> **Deviations from the spec below, all deliberate:**
> - Tool-limit branch: the "please summarize" nudge is merged into the *same*
>   user message as the tool results rather than appended as a second user
>   message, so the transcript keeps alternating cleanly.
> - `zen_proxy` SSE sends `Connection: close`, not `keep-alive` as originally
>   sketched. Found in live testing: this server speaks HTTP/1.0 and sends no
>   `Content-Length`, so end-of-stream is the socket closing — advertising
>   keep-alive leaves clients waiting for data that never comes.
> - Added `os.system("")` alongside the encoding fix to enable VT100 on legacy
>   consoles.
> - 17 tests written rather than the 3 sketched here.

**Goal:** TOMAS completes a multi-turn conversation with tool calls, on the shipped configuration, without crashing.
**Effort:** 1-2 days.
**Blocks:** everything. No other phase can be verified until this is done.

Today, out of the box, the first message a user sends fails after ~70 seconds of retries, and every tool-using turn fails after that. Three defects, all small.

---

## P0-1 · The agent never records its own turns

**Severity: BLOCKING**

### The problem

`agent_loop` executes tools and appends the results — but never appends the assistant message that *requested* them. There is no `messages.append({"role": "assistant", ...})` anywhere in `agent.py`. All four appends in the file are `role: "user"`:

```
agent.py:1162   messages.append({"role": "user", "content": [...]})   # tool-limit notice
agent.py:1219   messages.append({"role": "user", "content": tool_results})
agent.py:1543   messages.append({"role": "user", "content": result})  # skill injection
agent.py:2386   messages.append({"role": "user", "content": user_input})
```

Two distinct failures follow.

**(a) Every tool-using turn dies.** The transcript becomes `user → user[tool_result]`. `zen_proxy.py:293-302` converts `tool_result` blocks into OpenAI `role: "tool"` messages, and those must immediately follow an assistant message carrying `tool_calls`. The assistant message is missing, so the upstream rejects the request:

```
502 upstream_error → invalid_request_error: "Messages with role 'tool'..."
```

`agent.py` classifies the 502 as transient and retries 3× (5s/10s/20s backoff), then returns *"I'm sorry, but the AI service is unavailable right now."*

Verified: reproduced on 100% of tool-using turns across file operations, code search, web search, URL fetch, MCP `sequentialthinking` and MCP `memory`. In every case the tool itself executed correctly — the agent simply never saw the result.

Proof it is the missing message and not the models: a hand-built round-trip that *does* include the assistant turn succeeds on all six free Zen models at full 120-tool payload (3.3s-11.9s). Some upstreams tolerate the orphan `tool` message, which is why a few turns appeared to work — but even then the model never sees its own tool call.

**(b) There is no conversation.** The final text reply is never appended either, so the model only ever sees a list of user messages. Verified with tools disabled:

```
turn1 reply: '472'          # "pick a number 100-999"
turn2 reply: 'NO MEMORY'    # "what number did you just pick?"
roles in history: ['user', 'user']
```

This also means `session_manager.save_session(messages, ...)` persists **user turns only**. The session browser's `▌ assistant` branch (`agent_cli.py:1023`) can never render, and `/session continue` restores half a conversation.

### The fix

**Step 1 — append the assistant turn inside the tool loop.** In `agent.py`, at line 1219, replace:

```python
        messages.append({"role": "user", "content": tool_results})
```

with:

```python
        # The assistant's tool_use blocks MUST be in the transcript before the
        # tool results: an OpenAI-format `role: "tool"` message is only valid as
        # a response to a preceding message carrying `tool_calls`.
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})
```

**Step 2 — append the final text reply.** Still in `agent_loop`, at the non-tool return path (currently lines 1147-1153):

```python
        if response.stop_reason != "tool_use":
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            if text:
                messages.append({"role": "assistant", "content": text})
                print(f'  {MAGENTA}{BOLD}▌ TOMAS{RESET}')
                print(f'  {text}')
            return text
```

**Step 3 — do the same on the streaming path.** `_agent_loop_streamed` (`agent.py:1222`) returns the streamed text without touching `messages`. It receives `messages` as a parameter, so append there too, right before returning the final text — otherwise streaming silently reintroduces the amnesia bug the moment Phase 0's streaming fix lands.

**Step 4 — handle the tool-limit branch.** At `agent.py:1160-1176` the loop appends a `user` nudge after the limit is hit, with the same missing-assistant problem. Append `{"role": "assistant", "content": response.content}` and the tool results before that nudge, or the final summary call will fail the same way.

**Step 5 — clean up the dead branch in `main()`.** `agent.py:2394-2402` is a comment block ending in `pass` that does nothing:

```python
            if result and not result.startswith("I'm sorry"):
                pass  # streaming handles output; non-streaming also handles it
            elif result:
                print(...)
```

Once `agent_loop` owns both printing and appending, reduce this to the error branch only. Do **not** append the reply again in `main()` — that would double it.

### Watch out for

- `response.content` holds SDK block objects, not dicts. The Anthropic SDK serialises them correctly on the next request, so pass them through as-is. If you ever persist `messages` to JSON directly (session saving does), make sure the serialiser handles them — `session_manager.save_session` may need `.model_dump()` on block objects. **Test session save/load after this change.**
- After the fix, `messages` grows roughly 2× faster. Confirm `maybe_compact` (`agent.py:981`) still triggers correctly — it was previously being fed a transcript missing half its content, so its thresholds have never been exercised realistically.

---

## P0-2 · Streaming is 100% broken, and the fallback never fires

**Severity: BLOCKING**

### The problem

Two independent bugs that compound.

**(a) The proxy cannot stream.** `zen_proxy.py:462` forwards the client's `stream: true` straight upstream:

```python
oai_body["stream"] = stream
```

But `_upstream_request` (`:214`) always does a plain `.read()`, and `_handle_anthropic` always does `json.loads(zen_raw)` (`:502`). Upstream returns SSE (`data: {...}\n\n` chunks), which is not valid JSON → `JSONDecodeError` → `502 "Invalid upstream response"` (`:504-508`). There is also no `text/event-stream` writer anywhere in the file — only `_send_json` (`:395`) — so even a correctly parsed stream could not be returned in the shape the Anthropic SDK expects.

Verified across models, with and without tools:

```
deepseek-v4-flash-free  tools=False  STREAM FAIL 7.6s  502
deepseek-v4-flash-free  tools=True   STREAM FAIL 7.0s  502
mimo-v2.5-free          tools=False  STREAM FAIL 7.0s  502
mimo-v2.5-free          tools=True   STREAM FAIL 9.9s  502
```

**(b) The working fallback is unreachable.** `agent.py:1085-1101`:

```python
                if not _streaming_disabled:
                    try:
                        stream_result = _agent_loop_streamed(...)
                        ...
                    except (AttributeError, TypeError):
                        _streaming_disabled = True          # ← only these disable it
                    except anthropic.InternalServerError:
                        raise                               # ← 502 goes to the retry loop
```

The non-streaming call at line 1102 works perfectly. It is never reached, because the 502 is re-raised into the outer retry handler (`:1110`), which retries the *streaming* call three times and gives up.

### The fix

**Step 1 — make the fallback reachable (2 lines, do this first).** In `agent.py:1099`:

```python
                    except anthropic.InternalServerError:
                        # A provider that 502s on streaming still works
                        # non-streamed. Disable streaming and fall through
                        # instead of burning the retry budget.
                        _streaming_disabled = True
```

Remove the `raise`. This alone restores the agent on any non-streaming provider and is worth shipping on its own.

**Step 2 — implement SSE in the proxy.** In `_handle_anthropic`, always fetch a complete response upstream, then synthesise the Anthropic SSE sequence if the client asked for one. Replace line 462:

```python
        # Always request a complete (non-streamed) response upstream — the
        # retry/parse path below needs whole JSON. If the client asked for
        # streaming, we synthesise the SSE frames from the finished response.
        oai_body["stream"] = False
```

Then replace the final two lines of the handler (`:520-521`):

```python
        ant_resp = openai_to_anthropic(zen_json, model, input_tokens)
        if stream:
            self._send_anthropic_sse(ant_resp)
        else:
            self._send_json(200, ant_resp)
```

And add the writer next to `_send_json` (`:395`):

```python
    def _send_sse_event(self, event: str, data: dict) -> None:
        self.wfile.write(f"event: {event}\n".encode())
        self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def _send_anthropic_sse(self, ant_resp: dict) -> None:
        """Replay a completed Anthropic response as an SSE stream.

        Not true token streaming — the upstream call has already finished —
        but it is the exact frame sequence the Anthropic SDK expects, so
        `client.messages.stream(...)` works instead of failing.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        blocks = ant_resp.get("content", [])
        usage = ant_resp.get("usage", {})

        self._send_sse_event("message_start", {
            "type": "message_start",
            "message": {**{k: v for k, v in ant_resp.items() if k != "content"},
                        "content": []},
        })

        for i, block in enumerate(blocks):
            self._send_sse_event("content_block_start", {
                "type": "content_block_start", "index": i,
                "content_block": ({"type": "text", "text": ""} if block.get("type") == "text"
                                  else {**block, "input": {}}),
            })
            if block.get("type") == "text":
                self._send_sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": i,
                    "delta": {"type": "text_delta", "text": block.get("text", "")},
                })
            elif block.get("type") == "tool_use":
                self._send_sse_event("content_block_delta", {
                    "type": "content_block_delta", "index": i,
                    "delta": {"type": "input_json_delta",
                              "partial_json": json.dumps(block.get("input", {}))},
                })
            self._send_sse_event("content_block_stop",
                                 {"type": "content_block_stop", "index": i})

        self._send_sse_event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": ant_resp.get("stop_reason"),
                      "stop_sequence": None},
            "usage": {"output_tokens": usage.get("output_tokens", 0)},
        })
        self._send_sse_event("message_stop", {"type": "message_stop"})
```

**Step 3 — mirror it for the OpenAI passthrough** (`_handle_openai`, `:523`) if you want `/v1/chat/completions` streaming too. Lower priority — nothing in TOMAS uses that path today.

### Note on expectations

This is **replayed** streaming, not real token streaming: the user waits for the full upstream response, then sees it appear at once. That is a large UX improvement over "the AI service is unavailable", and it is the right amount of work for Phase 0. True incremental streaming means reading the upstream SSE line by line and re-emitting — worth doing in Phase 4 when the proxy becomes a proper provider adapter.

---

## P0-3 · `UnicodeEncodeError` crash on non-UTF-8 Windows consoles

**Severity: BLOCKING on affected systems (any non-English Windows locale)**

### The problem

`agent.py` prints `▌ ✧ ⚙ ◎ ▣ ⇧ ⚡ ↳` and box-drawing characters, and never reconfigures stdout or the console codepage. Grepping `agent.py` for `reconfigure|chcp|PYTHONIOENCODING|SetConsoleOutputCP|colorama` returns nothing.

On a machine whose default Python console encoding is cp1251, the agent crashes on its own output label:

```
File "agent.py", line 1151, in agent_loop
    print(f'  {MAGENTA}{BOLD}▌ TOMAS{RESET}')
UnicodeEncodeError: 'charmap' codec can't encode character '▌'
```

Hit live during testing. `/help`, `/model`, `/status` and `/mode` carry the same glyphs. `test_agent.py:22-26` already does the right thing — the fix just never made it into the agent itself.

### The fix

At the top of **both** `agent.py` and `agent_cli.py`, after the imports:

```python
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
```

`errors="replace"` matters: it guarantees that a glyph can degrade to `?` rather than take down the process, even if reconfiguration fails on some exotic host.

**Also enable ANSI on legacy consoles** while you are here — one line, prevents raw escape codes showing as garbage on old `cmd.exe`:

```python
if sys.platform == "win32":
    os.system("")   # enables VT100 processing in legacy conhost
```

---

## P0-4 · Add the integration test that would have caught all three

**Severity: HIGH — without this, all three bugs silently return**

None of these bugs were caught by the existing suite (8 tests, 112 lines) because nothing tests the agent loop itself. One test with a stub client catches all of them.

Create `tests/test_agent_loop.py`:

```python
import unittest
from unittest.mock import MagicMock
import agent


class FakeBlock:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class FakeResponse:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = MagicMock(input_tokens=10, output_tokens=5)


class TestAgentLoopTranscript(unittest.TestCase):
    def setUp(self):
        agent.YOLO_MODE = True
        agent._streaming_disabled = True
        agent.COMBINED_TOOLS = agent.TOOLS

    def test_tool_round_trip_keeps_assistant_turn(self):
        """Regression: P0-1(a). The assistant's tool_use message must be in
        the transcript before the tool_result, or OpenAI-format upstreams
        reject the orphaned `role: tool` message."""
        tool_block = FakeBlock("tool_use", id="tu_1", name="list_files", input={})
        responses = [
            FakeResponse([tool_block], "tool_use"),
            FakeResponse([FakeBlock("text", text="done")], "end_turn"),
        ]
        client = MagicMock()
        client.messages.create.side_effect = responses
        agent._get_client = lambda: client

        messages = [{"role": "user", "content": "list the files"}]
        reply = agent.agent_loop("sys", messages)

        roles = [m["role"] for m in messages]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant"])
        self.assertEqual(reply, "done")

    def test_conversation_history_retains_replies(self):
        """Regression: P0-1(b). The agent must remember what it said."""
        client = MagicMock()
        client.messages.create.return_value = FakeResponse(
            [FakeBlock("text", text="472")], "end_turn")
        agent._get_client = lambda: client

        messages = [{"role": "user", "content": "pick a number"}]
        agent.agent_loop("sys", messages)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])

    def test_streaming_failure_falls_back(self):
        """Regression: P0-2(b). A provider that 502s on streaming must fall
        through to the non-streaming call, not consume the retry budget."""
        import anthropic
        agent._streaming_disabled = False

        def boom(*a, **kw):
            raise anthropic.InternalServerError(
                "502", response=MagicMock(), body=None)
        agent._agent_loop_streamed = boom

        client = MagicMock()
        client.messages.create.return_value = FakeResponse(
            [FakeBlock("text", text="ok")], "end_turn")
        agent._get_client = lambda: client

        reply = agent.agent_loop("sys", [{"role": "user", "content": "hi"}])
        self.assertEqual(reply, "ok")
        self.assertTrue(agent._streaming_disabled)
        self.assertEqual(client.messages.create.call_count, 1)  # no retry storm


if __name__ == "__main__":
    unittest.main()
```

Add a proxy test too — `zen_proxy.py` has zero tests and produced the most production failures found:

```python
# tests/test_zen_proxy.py — assert _send_anthropic_sse emits the required
# frame sequence in order, for both a text response and a tool_use response.
EXPECTED = ["message_start", "content_block_start", "content_block_delta",
            "content_block_stop", "message_delta", "message_stop"]
```

---

## Verification

Run in order:

```powershell
# 1. Unit + regression suites
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"

# 2. Live single-turn, no tools
.venv\Scripts\python.exe -c "import agent; agent._streaming_disabled=False; print(agent.agent_loop(agent.build_system_prompt(), [{'role':'user','content':'say hi'}]))"

# 3. Live tool round-trip — this is the one that used to fail 100% of the time
#    Expect: a tool executes AND the agent reports its result.

# 4. Two-turn memory check
#    Turn 1: "Pick a number between 100 and 999."
#    Turn 2: "What number did you pick?"  → must repeat it, not say NO MEMORY.

# 5. Non-UTF-8 console check
chcp 1251
.venv\Scripts\python.exe agent.py    # must not raise UnicodeEncodeError
```

## Acceptance criteria

- [ ] A tool-using turn completes and the agent reports the tool's result.
- [ ] The agent can repeat a number it picked one turn earlier.
- [ ] `messages` roles alternate correctly; a saved session contains assistant turns.
- [ ] Streaming either works, or degrades to non-streaming on the **first** attempt with no retry storm.
- [ ] `agent.py` runs under `chcp 1251` without crashing.
- [ ] `tests/test_agent_loop.py` and `tests/test_zen_proxy.py` pass and fail if the fixes are reverted.

## Next

Phase 1 — [close the learning loop](PHASE-1-close-the-loop.md).

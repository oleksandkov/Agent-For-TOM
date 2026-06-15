# Agent-For-Labs Backend Pipeline — Implementation Status

This document describes what is implemented in `app/backend/pipeline/`
as of the current commit, what works end-to-end, and the **known
gaps** — most importantly the fact that the attached-file content
currently does not reach the main LLM in fallback mode.

## What runs end-to-end on the example session

```text
example_session (transit snapshot)
   ├── session_context.json
   ├── general_instructions.md      (52 KB; system prompt)
   ├── lab1_fill.md                 (template-specific instructions)
   ├── lab1_params.json             (gap_values from the UI)
   ├── library_files.json
   ├── context.json
   └── attached/
       └── 77599…000dd9.txt         (47-page LMV_LabRob.pdf → text)

                              ↓  PipelineRunner.run()
                              ↓
   Stage 1 (transit)              ok
   Stage 2 (compact)              ok
   Stage 5 (synthesize + execute) warn (1 warning)
   ─────────────────────────────────────────────────────────────────
   app/debug/output/example_session/
       Lab_Template_Final.docx    36 KB  ✓ real Ukrainian content
       Lab_Template_Final.pdf    108 KB  ✓ valid %PDF-1.4
       filled.py                           ✓ runs, has create_docx / create_pdf
       index.json                          ✓ token counts, timings, stages
       filled_stdout.log                  ✓ "Успішно створено файл: …"
       output.log
```

Run it yourself:

```powershell
& .\.venv\Scripts\python.exe run_pipeline.py example_session
```

Or run the full test suite:

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_pipeline -v
```

Both complete successfully.

## Pipeline architecture (as implemented)

```
app/backend/pipeline/
├── __init__.py               public surface; re-exports
├── utils.py                  env, paths, tokens, count_tokens
├── types.py                  PipelineContext + StageResult dataclasses
├── stage1_transit.py         validate snapshot, ensure attached .txt
├── stage2_compact.py         summarise >4000-token inputs via Qwen
├── stage5_output.py          synthesise filled.py + run in sandbox
├── orchestrator.py           end-to-end runner
└── bridge_adapter.py         emits QML-compatible signals

app/backend/compact/
└── qwen_runner.py            Qwen2.5-1.5B GGUF (llama-cpp-python)
                              with heuristic_truncate fallback

app/backend/llm/
└── gap_assembler.py          local filled.py synthesizer (no HF token)
```

Stages 3 (remote text LLM) and 4 (image generation) from the full
backend plan are intentionally **collapsed into stage 5's synthesis
step** in this scope (per the user's "stages 1, 2, 5 only" answer).

## Known gap: attached file content does NOT reach the main LLM in fallback mode

**This is the most important caveat to know about right now.**

When `HUGGY_FACE_TOKEN` is empty (current state of `.env`), the
pipeline behaves as follows:

| Stage | What it does with the attached PDF | Reach? |
|---|---|---|
| Stage 1 (transit) | Converts the PDF to `attached/<hash>.txt` and counts tokens | ✓ used in this stage only |
| Stage 2 (compact) | Compacts the attached text with Qwen (or heuristic fallback) into `compact/attached_compact/<hash>.txt` | ✓ compacted, but no further consumer |
| Stage 5 (synthesize) | Falls back to `gap_assembler.render_filled_py()` which uses **only** `lab1_params.json` (theme, goal, tasks, control_questions, bibliography) | **✗ the attached file is NOT used** |

In other words: the 47 pages of `LMV_LabRob.pdf` are parsed and
summarised, but the resulting summary is never injected into the
final `filled.py`. The reason is that the local gap_assembler only
knows how to fill the canonical gap fields declared in
`lab1_fill.md`; it has no slot for "free-form context from attached
files".

**This is by design for the scoped-down plan**, but it means the
user's "Створити лабораторну роботу з алгоритмів сортування.
Використовуйте теоретичний матеріал з прикладеного файлу
LMV_LabRob.pdf як джерело" instruction is ignored in the fallback
path.

### How to make the attached content actually influence the output

There are two paths. Both happen automatically as soon as the
relevant dependency is present:

1. **Add `HUGGY_FACE_TOKEN` to `.env`.** The remote text LLM
   (`meta-llama/Llama-3.3-70B-Instruct`) will then be called with a
   prompt that includes the compacted attached text (see
   `stage5_output._maybe_remote_text_llm`). Stage 5 will set
   `source_mode="remote"` in `index.json` instead of `"local_fallback"`.
   This is the **recommended** path.

2. **Wire the local gap_assembler to a "general_info" extension.**
   Stage 2 already compacts the attached file to ~500 tokens. The
   `gap_assembler` could be extended to read the compacted file and
   inject it into the "Загальні відомості" section. This is a
   small code change (~30 lines) and would let the pipeline be
   useful even without an HF token. To do it, add to the
   `render_filled_py` flow:
   - read `compact/attached_compact/<hash>.txt` if present
   - pass it to `_render_lab1` and concatenate it into the
     `general_info` block

Both paths preserve the contract: identical text in DOCX and PDF,
no unfilled placeholders, DSTU-compliant typography.

## Why the gap_assembler does not use attached text today

The `lab1_fill.md` instruction file (the source of truth for what
"lab1" means) defines exactly seven sections, each with a labelled
gap. The `general_info` gap is the closest slot to "free-form context
from an attached file", but it is meant to hold a polished
theoretical paragraph in academic style — not raw 500 tokens of
PDF text. Putting the compacted attached text there verbatim would
violate the ДСТУ 3008:2015 style.

A future refinement: have the gap_assembler treat the compacted
attached text as **input** to its own (very small) "summarise to
academic prose" pass. That is, even without a remote LLM, we could
run a small local model to rewrite the attached summary into
academic style. This is the natural extension of the current
heuristic_truncate → academic-style-summariser step.

## Behaviour with `HUGGY_FACE_TOKEN` present

| Step | Behaviour |
|---|---|
| Stage 1 | unchanged |
| Stage 2 | unchanged |
| Stage 5 synthesis | calls `InferenceClient.chat.completions(...)` with model = `HUGGING_FACE_MODEL` (default `meta-llama/Llama-3.3-70B-Instruct`); prompt = global instructions + template instructions + **compacted attached files** + user input snapshot |
| Stage 5 execution | unchanged (sandbox runs the LLM's `filled.py` to produce DOCX + PDF) |
| `index.json` | `source_mode: "remote"` |

Failure modes: invalid token, quota exhausted, network timeout →
Stage 5 catches the exception, adds a warning, falls back to
`gap_assembler` (the same code path as the no-token case). This is
why the pipeline is robust to missing credentials.

## What the bridge does on `startGeneration`

`app/bridge.py.startGeneration(...)` (Python side only; the QML
contract is unchanged) now:

1. Materialises a fresh transit snapshot under
   `app/debug/transit/<new_id>/` from the cached `sessionPayloadJson`
   + the per-generation args (template_id, length, hardness, image_mode,
   goal).
2. Spawns a daemon thread that runs `PipelineRunner().run(snap_dir)`.
3. The thread emits the existing QML signals
   (`pipelineStarted`, `pipelineProgress`, `pipelineStepActive`,
   `pipelineStepDone`, `pipelineLog`, `pipelineFinished`,
   `pipelineError`) via `BridgePipelineAdapter`.
4. On completion, the bridge updates `_sessions` with the real
   status, duration, and the DOCX/PDF paths from `index.json`.

The `ProgressScreen.qml` continues to receive the same six-step
shape (0–5); the adapter maps our 3 internal stages to indices 0, 1
and 4 of the legacy table.

## Quick reference

| What you want | Command |
|---|---|
| Run pipeline on the example session | `& .venv\Scripts\python.exe run_pipeline.py example_session` |
| Run all tests | `& .venv\Scripts\python.exe -m unittest tests.test_pipeline -v` |
| Force heuristic fallback (skip Qwen) | `& .venv\Scripts\python.exe run_pipeline.py example_session --no-qwen` |
| Re-emit JSON of the whole run | add `--json` to the above |
| Run every transit session | `& .venv\Scripts\python.exe run_pipeline.py --all` |

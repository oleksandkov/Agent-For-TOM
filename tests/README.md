# Tests

Smoke + integration tests for the Agent-For-TOM backend pipeline.

## Running

The tests use the frozen transit snapshot under
`app/debug/transit/example_session/` as the fixture. They validate
that each stage produces the right artifacts and that the full
orchestrator run produces a working DOCX + PDF.

```powershell
& .\.venv\Scripts\python.exe -m unittest tests.test_pipeline -v
```

```bash
.venv/bin/python -m pytest tests/ -v
```

## What's covered

| Test | Purpose |
|---|---|
| `TestUtils` | `count_tokens`, `load_env`, `session_paths` |
| `TestGapAssembler` | Local fallback filled.py synthesizer (no HF token needed) |
| `TestStage1` | Validates the example transit snapshot and converts files |
| `TestStage2` | Compacts the attached text with the heuristic fallback (no Qwen) |
| `TestOrchestrator.test_full_pipeline` | End-to-end run on `example_session` |

The full-pipeline test cleans the per-stage directories before each
run, then asserts:

- Every stage reports `ok` or `warn` (no `fail`).
- `index.json` reports `status="completed"`.
- The DOCX and PDF files exist and are non-empty.
- `filled.py` is syntactically valid and contains `def create_docx`
  and `def create_pdf`.

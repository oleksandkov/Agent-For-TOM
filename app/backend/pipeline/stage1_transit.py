"""app/backend/pipeline/stage1_transit.py

Stage 1 of the pipeline: validate the frozen transit snapshot and
(optionally) ensure every attached file has been converted to plain text.

The transit snapshot is what the UI writes when the user clicks
"Generate". It lives in `app/debug/transit/<session_id>/` and contains:

  - session_context.json   (canonical metadata, input_snapshot)
  - context.json           (file hash + converted_text_path + token_count)
  - library_files.json     (DB-equivalent row for the attached PDF/DOCX/…)
  - lab1_params.json / lab2_params.json (gap_values for the template)
  - general_instructions.md
  - lab1_fill.md / lab2_fill.md
  - attached/<hash>.txt    (converted plain text, one per file)

What this stage does:
  1. Verify the canonical files all exist and parse as JSON.
  2. Verify every `attached_file` referenced in `input_snapshot` has a
     corresponding `attached/<hash>.txt` next to the snapshot.
  3. If a hash is missing, attempt to convert it from
     `storage/library/<prefix>/<sha256>.<ext>` using the existing
     pdf2txt / docx2txt / pptx2txt / image2txt scripts.
  4. Compute token counts for the merged attached text.
  5. Write a `transit.log` next to the snapshot.

This stage NEVER calls an LLM. It is a pure validation / preparation
step. Failures here are HARD — there is no point compacting malformed
input.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .types import PipelineContext, StageResult, STAGE_OK, STAGE_WARN
from .utils import (
    APP_DIR,
    Timer,
    count_tokens,
    ensure_dir,
    now_iso,
)

# Import the existing converters. We do this lazily so that unit tests
# can mock the converters without importing PyMuPDF.
_CONVERTERS: dict[str, str] = {
    "application/pdf": "pdf2txt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx2txt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx2txt",
    "image/png": "image2txt",
    "image/jpeg": "image2txt",
    "image/jpg": "image2txt",
}

_EXT_TO_CONVERTER: dict[str, str] = {
    ".pdf": "pdf2txt",
    ".docx": "docx2txt",
    ".pptx": "pptx2txt",
    ".png": "image2txt",
    ".jpg": "image2txt",
    ".jpeg": "image2txt",
}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _attach_converter_path() -> None:
    """Make `app.backend.<converter>` importable from the script's CWD."""
    backend_dir = APP_DIR / "backend"
    s = str(backend_dir)
    if s not in sys.path:
        sys.path.insert(0, s)


def _convert_to_text(src: Path, original_type: str) -> str:
    """Run the appropriate converter and return the extracted plain text."""
    _attach_converter_path()
    ext = src.suffix.lower()
    script_name = _CONVERTERS.get(original_type) or _EXT_TO_CONVERTER.get(ext)
    if not script_name:
        raise RuntimeError(
            f"No converter registered for type='{original_type}', ext='{ext}'"
        )
    module = __import__(f"app.backend.{script_name}", fromlist=["main"])
    # pdf2txt etc. expose `main(argv)` and return exit code. We invoke via
    # a temp output path so we get the converted text on disk.
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as tmp:
        out_path = Path(tmp.name)
    try:
        rc = module.main([str(src), "-o", str(out_path), "--no-format"])
        if rc != 0:
            raise RuntimeError(f"{script_name} exited with rc={rc}")
        return out_path.read_text(encoding="utf-8")
    finally:
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass


def _resolve_library_file(library_record: dict[str, Any]) -> Path | None:
    """Return the on-disk path for a library_file row, if it exists.

    The transit snapshot stores `stored_path` relative to the repo root
    (per `Plan/backendPlan/08_storage_and_env.md`). We resolve that
    relative to REPO_ROOT.
    """
    stored = library_record.get("stored_path")
    if not stored:
        return None
    p = Path(stored)
    if not p.is_absolute():
        p = (APP_DIR.parent / p).resolve()
    return p if p.is_file() else None


def run_stage1(ctx: PipelineContext) -> StageResult:
    """Validate the transit snapshot and ensure all attached files are .txt."""
    result = StageResult(name="stage1_transit")
    transit_dir: Path = ctx.transit_dir
    ensure_dir(transit_dir)

    with Timer(ctx.timings, "stage1") as t:
        log_lines: list[str] = []

        # 1. Required top-level files.
        required = [
            "session_context.json",
            "context.json",
            "library_files.json",
        ]
        for fname in required:
            if not (transit_dir / fname).is_file():
                result.errors.append(f"Missing required file: {fname}")
                result.status = "fail"
                _write_log(transit_dir, result, log_lines)
                return result

        # 2. Parse the canonical JSON files.
        try:
            session_ctx = _load_json(transit_dir / "session_context.json")
            context_doc = _load_json(transit_dir / "context.json")
            library_doc = _load_json(transit_dir / "library_files.json")
        except json.JSONDecodeError as exc:
            result.errors.append(f"Malformed JSON in transit snapshot: {exc}")
            result.status = "fail"
            _write_log(transit_dir, result, log_lines)
            return result

        log_lines.append(
            f"session_id={session_ctx.get('id')} template_id={session_ctx.get('template_id')}"
        )

        # 3. Resolve per-template filenames from input_snapshot.
        snap = session_ctx.get("input_snapshot", {})
        template_id = snap.get("template_id") or session_ctx.get("template_id", "")
        params_path = transit_dir / f"{template_id}_params.json"
        if not params_path.is_file():
            # Fall back to whatever the snapshot names explicitly.
            gap_ref = snap.get("gap_values_ref")
            if gap_ref:
                params_path = transit_dir / gap_ref
        if not params_path.is_file():
            result.warnings.append(
                f"Template params file not found for template_id={template_id!r}; "
                f"expected {params_path.name}"
            )
        else:
            log_lines.append(f"params={params_path.name}")
            result.artifacts["params"] = str(params_path)

        # 4. Special-instructions file and global instructions.
        include_special_instructions = snap.get("include_special_instructions", True)
        if include_special_instructions:
            template_ins = transit_dir / f"{template_id}_fill.md"
            if not template_ins.is_file():
                # The snapshot may have a custom name. We accept any *_fill.md.
                candidates = sorted(transit_dir.glob("*_fill.md"))
                if candidates:
                    template_ins = candidates[0]
                else:
                    result.warnings.append(
                        f"Template instructions file ({template_id}_fill.md) missing"
                    )
            if template_ins.is_file():
                log_lines.append(f"template_instructions={template_ins.name}")
                result.artifacts["template_instructions"] = str(template_ins)

        global_ins = transit_dir / "general_instructions.md"
        if not global_ins.is_file():
            result.warnings.append("general_instructions.md missing in transit")
        else:
            result.artifacts["global_instructions"] = str(global_ins)

        # 5. Attached files: ensure each has a .txt.
        attached_dir = ensure_dir(transit_dir / "attached")
        library_by_hash: dict[str, dict[str, Any]] = {
            entry.get("file_hash"): entry
            for entry in library_doc.get("files", [])
            if entry.get("file_hash")
        }
        context_by_hash: dict[str, dict[str, Any]] = {
            entry.get("file_hash"): entry
            for entry in context_doc.get("files", [])
            if entry.get("file_hash")
        }

        from .utils import get_support_attach_files
        if get_support_attach_files():
            attached_hashes = list(snap.get("attached_files") or [])
        else:
            attached_hashes = []
        merged_parts: list[str] = []
        total_tokens = 0
        converted_now = 0
        reused = 0

        for file_hash in attached_hashes:
            txt_path = attached_dir / f"{file_hash}.txt"
            if txt_path.is_file() and txt_path.stat().st_size > 0:
                text = txt_path.read_text(encoding="utf-8")
                reused += 1
            else:
                # Try to convert from the library_file's stored_path.
                lib_row = library_by_hash.get(file_hash)
                if not lib_row:
                    result.warnings.append(
                        f"Attached file hash {file_hash[:10]}… has no library_file row"
                    )
                    continue
                src = _resolve_library_file(lib_row)
                if not src:
                    result.warnings.append(
                        f"Library file for hash {file_hash[:10]}… not on disk: "
                        f"stored_path={lib_row.get('stored_path')}"
                    )
                    continue
                try:
                    text = _convert_to_text(src, lib_row.get("original_type", ""))
                except Exception as exc:  # noqa: BLE001
                    result.warnings.append(
                        f"Conversion failed for {src.name}: {exc}"
                    )
                    continue
                txt_path.write_text(text, encoding="utf-8")
                converted_now += 1

            tokens = count_tokens(text)
            total_tokens += tokens
            merged_parts.append(
                f"===== {file_hash[:12]} ({tokens} tokens) =====\n{text}"
            )
            log_lines.append(
                f"attached {file_hash[:10]}…: {tokens} tokens "
                f"({'reused' if txt_path.is_file() and reused > 0 else 'converted'})"
            )

        result.metrics["attached_files"] = len(attached_hashes)
        result.metrics["attached_reused"] = reused
        result.metrics["attached_converted"] = converted_now
        result.metrics["attached_total_tokens"] = total_tokens
        result.artifacts["transit_dir"] = str(transit_dir)
        result.artifacts["attached_dir"] = str(attached_dir)
        result.artifacts["session_context"] = str(transit_dir / "session_context.json")
        result.artifacts["context"] = str(transit_dir / "context.json")
        result.artifacts["library_files"] = str(transit_dir / "library_files.json")

        if attached_hashes and not merged_parts:
            result.warnings.append(
                "No attached files produced usable text — downstream stages will use "
                "instructions only"
            )

        status = STAGE_OK if not result.warnings else STAGE_WARN
        result.status = status
        t.duration_ms if hasattr(t, "duration_ms") else None  # noqa: ERA001
        log_lines.append(
            f"stage1 done: {len(attached_hashes)} attached, "
            f"{total_tokens} total tokens, "
            f"{converted_now} converted, {reused} reused, "
            f"{len(result.warnings)} warnings"
        )
        _write_log(transit_dir, result, log_lines)

    result.duration_ms = ctx.timings.get("stage1", 0.0)
    return result


def _write_log(transit_dir: Path, result: StageResult, lines: list[str]) -> None:
    log_path = transit_dir / "transit.log"
    header = (
        f"[{now_iso()}] stage={result.name} status={result.status} "
        f"duration_ms={result.duration_ms:.1f}\n"
    )
    body = "\n".join(lines) + "\n"
    warnings = "".join(f"WARN  {w}\n" for w in result.warnings)
    errors = "".join(f"ERROR {e}\n" for e in result.errors)
    try:
        log_path.write_text(header + body + warnings + errors, encoding="utf-8")
    except OSError:
        pass

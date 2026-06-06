"""response_parser.py — LLM output extraction & validation.

Extracts Python code and optional image_manifest.json from the raw LLM
response. Handles common failure modes: markdown fences around code,
explanations instead of code, malformed JSON, syntax errors.

Usage:
    parser = ResponseParser()
    result = parser.parse(raw_response)
    if result.is_valid:
        result.filled_py          # str
        result.manifest           # dict | None
        result.manifest_json      # str | None
        result.python_ast         # ast.Module
    else:
        result.errors             # list[str]
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any


class ResponseParseResult:
    """Structured result of parsing an LLM response."""

    def __init__(
        self,
        filled_py: str = "",
        manifest: dict | None = None,
        manifest_json: str | None = None,
        errors: list[str] | None = None,
    ) -> None:
        self.filled_py = filled_py
        self.manifest = manifest
        self.manifest_json = manifest_json
        self.errors = errors or []

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0 and bool(self.filled_py)

    @property
    def has_manifest(self) -> bool:
        return self.manifest is not None


class ResponseParser:
    """Parse and validate LLM responses for the Agent-For-TOM pipeline."""

    MANIFEST_SEPARATOR = "<!--IMAGE_MANIFEST-->"

    # Patterns that models commonly wrap code in - ordered by specificity
    CODE_FENCE_PATTERNS = [
        re.compile(r"```python\s*\n(.*?)\n```", re.DOTALL),
        re.compile(r"```\s*\n(.*?)\n```", re.DOTALL),
        re.compile(r"```(.*?)```", re.DOTALL),
    ]

    APOLOGY_PATTERNS = re.compile(
        r"^(вибач|пробач|sorry|i apologize|here.?s (your|the) |"
        r"я (не |не можу |буду радий )|звісно|з радістю)",
        re.IGNORECASE,
    )

    def parse(self, raw: str) -> ResponseParseResult:
        """Parse a raw LLM response into code + optional manifest."""
        result = ResponseParseResult()

        if not raw or not raw.strip():
            result.errors.append("Empty response from LLM")
            return result

        stripped = raw.strip()

        # Split on manifest separator
        code_part = stripped
        manifest_part: str | None = None

        if self.MANIFEST_SEPARATOR in stripped:
            parts = stripped.split(self.MANIFEST_SEPARATOR, 1)
            code_part = parts[0].strip()
            manifest_part = parts[1].strip()

        # Extract Python code (handle markdown fences)
        result.filled_py = self._extract_python(code_part)

        if not result.filled_py:
            result.errors.append(
                "No valid Python code found in LLM response"
            )
            return result

        # Validate Python syntax
        syntax_errors = self._validate_python_syntax(result.filled_py)
        result.errors.extend(syntax_errors)

        # Parse manifest if present
        if manifest_part:
            manifest_errors = self._validate_manifest_json(manifest_part)
            result.errors.extend(manifest_errors)
            if not manifest_errors:
                result.manifest_json = manifest_part
                result.manifest = json.loads(manifest_part)

        # Check for apology/explanation patterns
        self._check_apology_pattern(result)

        return result

    def _extract_python(self, text: str) -> str:
        """Extract Python code, stripping markdown fences if present."""
        # Try to extract from code fences first
        for pattern in self.CODE_FENCE_PATTERNS:
            match = pattern.search(text)
            if match:
                candidate = match.group(1).strip()
                if candidate:
                    return candidate

        # No fences found — assume raw Python if it starts with import
        if text.startswith("import ") or text.startswith("from "):
            return text

        # Some models put `import os` as first code line
        # Try finding the first import statement
        for line in text.split("\n"):
            stripped_line = line.strip()
            if stripped_line.startswith("import ") or stripped_line.startswith("from "):
                idx = text.index(line)
                return text[idx:].strip()

        return ""

    def _validate_python_syntax(self, code: str) -> list[str]:
        """Validate Python syntax using ast.parse."""
        errors: list[str] = []
        try:
            ast.parse(code)
        except SyntaxError as e:
            errors.append(f"Python syntax error: {e}")
            # Extract context around the error
            lines = code.split("\n")
            if e.lineno is not None:
                start = max(0, e.lineno - 3)
                end = min(len(lines), e.lineno + 2)
                context = "\n".join(
                    f"{i+1}: {lines[i]}" for i in range(start, end)
                )
                errors.append(f"Context around line {e.lineno}:\n{context}")
        return errors

    def _validate_manifest_json(self, manifest_str: str) -> list[str]:
        """Validate JSON manifest structure."""
        errors: list[str] = []
        try:
            data = json.loads(manifest_str)
        except json.JSONDecodeError as e:
            errors.append(f"Manifest JSON error: {e}")
            return errors

        if not isinstance(data, dict):
            errors.append("Manifest must be a JSON object")
            return errors

        if "images" not in data:
            errors.append("Manifest missing 'images' array")
            return errors

        if not isinstance(data["images"], list):
            errors.append("'images' must be an array")
            return errors

        required_image_fields = {"id", "slot", "kind", "caption", "anchor_marker", "render"}
        valid_kinds = {"diagram", "illustration"}
        valid_engines = {"matplotlib", "graphviz", "huggingface"}
        seen_ids: set[str] = set()
        seen_anchors: set[str] = set()

        for i, img in enumerate(data["images"]):
            missing = required_image_fields - set(img.keys())
            if missing:
                errors.append(f"images[{i}] missing fields: {missing}")
                continue

            if img["id"] in seen_ids:
                errors.append(f"Duplicate image id: {img['id']}")
            seen_ids.add(img["id"])

            if img["kind"] not in valid_kinds:
                errors.append(
                    f"images[{i}].kind must be one of {valid_kinds}, got '{img['kind']}'"
                )

            if img["anchor_marker"] in seen_anchors:
                errors.append(f"Duplicate anchor_marker: {img['anchor_marker']}")
            seen_anchors.add(img["anchor_marker"])

            anchor_pattern = re.compile(
                r"^\[\[ANCHOR:" + re.escape(img["id"]) + r":[A-Za-z0-9]{6}\]\]$"
            )
            if not anchor_pattern.match(img["anchor_marker"]):
                errors.append(
                    f"images[{i}].anchor_marker format invalid: {img['anchor_marker']}"
                )

            render = img.get("render", {})
            if render.get("engine") not in valid_engines:
                errors.append(
                    f"images[{i}].render.engine must be one of {valid_engines}"
                )

            if img["kind"] == "diagram" and not render.get("script"):
                errors.append(
                    f"images[{i}]: diagram kind requires render.script"
                )

            if img["kind"] == "illustration" and not render.get("prompt"):
                errors.append(
                    f"images[{i}]: illustration kind requires render.prompt"
                )

        if not data["images"]:
            errors.append("Manifest 'images' array is empty — remove manifest entirely")

        return errors

    def _check_apology_pattern(self, result: ResponseParseResult) -> None:
        """Warn if the LLM response starts with an apology/explanation."""
        if result.filled_py and self.APOLOGY_PATTERNS.match(result.filled_py):
            result.errors.append(
                "LLM response appears to contain explanatory text before code"
            )

    @staticmethod
    def strip_anchor_markers(code: str) -> str:
        """Remove all [[ANCHOR:...]] markers from code (for cache key)."""
        return re.sub(r"\[\[ANCHOR:[^\]]+\]\]", "", code)

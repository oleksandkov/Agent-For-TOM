"""manifest_validator.py — Post-Pass-1 consistency validation.

Ensures the image_manifest.json and filled.py are in sync:
- Every manifest image entry has [[ANCHOR:<id>:<rand>]] in both DOCX and PDF
- No stray [[ANCHOR:...]] markers without manifest entries
- Image count within limits
- Manifest structure is valid

Usage:
    validator = ManifestValidator()
    result = validator.validate(filled_py, manifest_dict)
    if result.is_valid:
        print("Manifest is consistent with filled.py")
    else:
        print(result.errors)
"""

from __future__ import annotations

import re
from typing import Any


class ManifestValidationResult:
    """Result of manifest vs filled.py validation."""

    def __init__(
        self,
        is_valid: bool = True,
        errors: list[str] | None = None,
        anchor_count: int = 0,
        manifest_count: int = 0,
        matched_count: int = 0,
    ) -> None:
        self.is_valid = is_valid
        self.errors = errors or []
        self.anchor_count = anchor_count
        self.manifest_count = manifest_count
        self.matched_count = matched_count


class ManifestValidator:
    """Validate consistency between image_manifest.json and filled.py."""

    ANCHOR_PATTERN = re.compile(r"\[\[ANCHOR:([^:]+):([A-Za-z0-9]{6})\]\]")
    MAX_IMAGES_DEFAULT = 5

    def __init__(self, max_images: int = MAX_IMAGES_DEFAULT) -> None:
        self.max_images = max_images

    def validate(self, filled_py: str, manifest: dict[str, Any] | None) -> ManifestValidationResult:
        """Validate manifest consistency with filled.py code."""
        errors: list[str] = []
        has_manifest = manifest is not None and bool(manifest.get("images"))

        # Extract all anchor markers from filled.py
        anchors_found = self._extract_anchors(filled_py)

        if not has_manifest:
            # If no manifest, there should be no anchors
            if anchors_found:
                errors.append(
                    f"Found {len(anchors_found)} [[ANCHOR:...]] markers "
                    f"in filled.py but no image_manifest.json. "
                    f"Either remove anchors or create manifest."
                )
            return ManifestValidationResult(
                is_valid=len(errors) == 0,
                errors=errors,
                anchor_count=len(anchors_found),
                manifest_count=0,
            )

        # Manifest exists — validate
        manifest_ids = {img["id"] for img in manifest["images"]}
        manifest_count = len(manifest["images"])

        # Check image count limit
        if manifest_count > self.max_images:
            errors.append(
                f"Manifest has {manifest_count} images, "
                f"exceeds max of {self.max_images}"
            )

        # Group anchors by their ID
        anchor_ids = set(anchors_found.keys())
        anchor_by_func = self._split_anchors_by_function(filled_py)

        # Every manifest ID must have an anchor in filled.py
        for img in manifest["images"]:
            img_id = img["id"]
            if img_id not in anchor_ids:
                errors.append(
                    f"Manifest image '{img_id}' has no [[ANCHOR:{img_id}:...]] "
                    f"in filled.py"
                )
            else:
                # Check anchor exists in both DOCX and PDF functions
                if img_id not in anchor_by_func.get("docx", set()):
                    errors.append(
                        f"Anchor [[ANCHOR:{img_id}:...]] missing from "
                        f"create_docx function"
                    )
                if img_id not in anchor_by_func.get("pdf", set()):
                    errors.append(
                        f"Anchor [[ANCHOR:{img_id}:...]] missing from "
                        f"create_pdf function"
                    )

        # Every anchor must have a manifest entry
        for anchor_id in anchor_ids:
            if anchor_id not in manifest_ids:
                errors.append(
                    f"[[ANCHOR:{anchor_id}:...]] in filled.py has no "
                    f"corresponding entry in image_manifest.json"
                )

        # Verify anchor marker format in manifest matches what's in code
        for img in manifest["images"]:
            expected_marker = img.get("anchor_marker", "")
            if expected_marker and expected_marker not in filled_py:
                errors.append(
                    f"Anchor marker '{expected_marker}' from manifest "
                    f"not found in filled.py"
                )

        matched = sum(1 for mid in manifest_ids if mid in anchor_ids)

        return ManifestValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            anchor_count=len(anchors_found),
            manifest_count=manifest_count,
            matched_count=matched,
        )

    def _extract_anchors(self, code: str) -> dict[str, str]:
        """Extract all [[ANCHOR:<id>:<rand>]] markers from code.

        Returns {id: rand} dict (last occurrence wins for each id).
        """
        anchors: dict[str, str] = {}
        for match in self.ANCHOR_PATTERN.finditer(code):
            anchor_id, rand = match.group(1), match.group(2)
            anchors[anchor_id] = rand
        return anchors

    def _split_anchors_by_function(
        self, code: str
    ) -> dict[str, set[str]]:
        """Split anchors into those in create_docx vs create_pdf functions."""
        result: dict[str, set[str]] = {"docx": set(), "pdf": set()}

        # Simple heuristic: find function boundaries
        docx_start = code.find("def create_docx")
        pdf_start = code.find("def create_pdf")

        if docx_start < 0 or pdf_start < 0:
            return result

        docx_code = code[docx_start:pdf_start]
        pdf_code = code[pdf_start:]

        for match in self.ANCHOR_PATTERN.finditer(docx_code):
            result["docx"].add(match.group(1))
        for match in self.ANCHOR_PATTERN.finditer(pdf_code):
            result["pdf"].add(match.group(1))

        return result

    @staticmethod
    def estimate_image_count(topic: str, theory_text: str) -> int:
        """Heuristic: how many images are appropriate for a given topic/text.

        Returns recommended count (0-5). Use as guidance, not rule.
        """
        # Structural/algorithmic topics benefit from diagrams
        diagram_keywords = [
            "сортуван", "алгоритм", "структур", "графік", "схем",
            "діаграм", "блок-схем", "порівнян", "залежност",
            "stack", "queue", "list", "tree", "graph",
        ]
        keyword_count = sum(
            1 for kw in diagram_keywords if kw.lower() in theory_text.lower()
        )

        if keyword_count >= 5:
            return 3
        elif keyword_count >= 3:
            return 2
        elif keyword_count >= 1:
            return 1
        return 0

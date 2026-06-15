"""pdf2txt.py — convert any PDF to a TXT file while preserving as much
formatting detail as possible.

Strategy
--------
Uses PyMuPDF (``pymupdf`` / ``fitz``), the best general-purpose PDF text
extractor. For each page it walks the document dictionary (blocks -> lines
-> spans) and reconstructs a readable, layout-aware plain-text rendering.

What it preserves
-----------------
* Page boundaries — explicit ``===== Page N =====`` markers
* Reading order — top-to-bottom, left-to-right, with multi-column support
  (text blocks at the same y-coordinate are joined with tab separators)
* Indentation — left x-coordinate is mapped to leading spaces
* Headers / footers — auto-detected by y-position and grouped separately
* Font name + size — optional, per-line annotation (``--annotate-fonts``)
* Bold / italic / monospace — inline markers: ``**...**``, ``*...*``, `` `...` ``
* Embedded images — noted inline, optionally extracted to a folder
  (``--extract-images <dir>``)
* Full structured dump — optional sidecar ``.json`` (``--json``)

Limitations
-----------
* Scanned / image-only PDFs need OCR. Pass ``--ocr`` to enable it
  (requires the ``pytesseract`` package and a Tesseract install on PATH).
* Fonts are reported by their internal PDF names; these rarely match the
  human-readable family name ("ABCDEE+TimesNewRoman" vs "Times New Roman").

Usage
-----
    python pdf2txt.py file.pdf
    python pdf2txt.py file.pdf -o out.txt
    python pdf2txt.py file.pdf --annotate-fonts
    python pdf2txt.py file.pdf --extract-images img/
    python pdf2txt.py file.pdf --json
    python pdf2txt.py file.pdf --ocr
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import pymupdf as fitz  # pymupdf >= 1.24
except ImportError:  # fall back to the old module name
    try:
        import fitz  # type: ignore[no-redef]
    except ImportError:
        sys.stderr.write(
            "ERROR: PyMuPDF is required. Install it with:\n"
            "    python -m pip install pymupdf\n"
        )
        sys.exit(1)


# PyMuPDF font-flag bits
F_ITALIC = 1 << 1
F_MONO = 1 << 3
F_BOLD = 1 << 4

# Approximate width of one monospace char in points (used to translate
# x-coordinates into leading spaces for indentation).
CHAR_WIDTH_PT = 4.5


def font_flag_str(flags: int) -> str:
    """Short label (e.g. ``B``, ``BI``, ``M``) describing a span's font flags."""
    out = []
    if flags & F_BOLD:
        out.append("B")
    if flags & F_ITALIC:
        out.append("I")
    if flags & F_MONO:
        out.append("M")
    return "".join(out)


def wrap_inline(text: str, flags: int) -> str:
    """Wrap a span with bold/italic/mono inline markers for readability."""
    if not text.strip():
        return text
    bold = bool(flags & F_BOLD)
    italic = bool(flags & F_ITALIC)
    mono = bool(flags & F_MONO)
    if mono:
        return f"`{text}`"
    if bold and italic:
        return f"***{text}***"
    if bold:
        return f"**{text}**"
    if italic:
        return f"*{text}*"
    return text


def indent_for(x0: float) -> str:
    """Translate a left x-coordinate into leading spaces (capped at 80)."""
    if x0 <= 0:
        return ""
    return " " * min(int(round(x0 / CHAR_WIDTH_PT)), 80)


def classify_blocks(page: fitz.Page, data: dict) -> tuple[list, list, list]:
    """Split page blocks into header / footer / body based on y-position."""
    page_h = page.rect.height
    header_threshold = page_h * 0.08
    footer_threshold = page_h * 0.92

    header_lines: list = []
    footer_lines: list = []
    body_blocks: list = []

    for block in data.get("blocks", []):
        btype = block.get("type", 0)
        bbox = block.get("bbox", (0, 0, 0, 0))
        _, y0, _, y1 = bbox

        if btype == 1:  # image — handled later
            body_blocks.append({"_kind": "image", "bbox": bbox})
            continue

        if y1 < header_threshold:
            header_lines.extend(block.get("lines", []))
        elif y0 > footer_threshold:
            footer_lines.extend(block.get("lines", []))
        else:
            body_blocks.append(
                {"_kind": "text", "bbox": bbox, "lines": block.get("lines", [])}
            )

    return header_lines, footer_lines, body_blocks


def render_lines(lines: list, *, indent: bool, annotate: bool) -> list[str]:
    """Render a flat list of line dicts to a list of formatted text lines."""
    out: list[str] = []
    for line in lines:
        spans = line.get("spans", [])
        if not spans:
            continue
        x0 = line.get("bbox", [0, 0, 0, 0])[0]
        pieces = [wrap_inline(sp.get("text", ""), sp.get("flags", 0)) for sp in spans]
        joined = "".join(pieces).rstrip()
        if not joined.strip():
            continue

        prefix = indent_for(x0) if indent else ""
        if annotate:
            first = spans[0]
            font = first.get("font", "?")
            size = first.get("size", 0)
            tag = font_flag_str(first.get("flags", 0))
            joined = f"[{font} {size:.1f}pt {tag}] {joined}"
        out.append(prefix + joined)
    return out


def extract_page(
    page: fitz.Page,
    page_num: int,
    *,
    annotate_fonts: bool,
    detect_columns: bool,
) -> list[str]:
    """Return a list of formatted text lines for one page."""
    data = page.get_text("dict")
    header_lines, footer_lines, body_blocks = classify_blocks(page, data)

    out: list[str] = []

    if header_lines:
        out.append(f"\n----- Page {page_num} (header) -----")
        out.extend(render_lines(header_lines, indent=True, annotate=annotate_fonts))

    out.append(f"\n===== Page {page_num} =====")

    if detect_columns:
        # Group all body text spans by visual y-row, then sort each row by x.
        rows: dict[int, list[tuple[float, list]]] = defaultdict(list)
        for blk in body_blocks:
            if blk["_kind"] != "text":
                continue
            for line in blk["lines"]:
                spans = line.get("spans", [])
                if not spans:
                    continue
                # Tolerance: snap y to the nearest 2 pt to merge sub-line fragments.
                ly = round(line["bbox"][1] / 2) * 2
                rows[ly].append((line["bbox"][0], spans))

        for y in sorted(rows.keys()):
            row_pieces: list[str] = []
            for x0, spans in sorted(rows[y], key=lambda t: t[0]):
                pieces = [wrap_inline(sp.get("text", ""), sp.get("flags", 0))
                          for sp in spans]
                joined = "".join(pieces).rstrip()
                if joined.strip():
                    row_pieces.append(joined)
            if not row_pieces:
                continue
            # Tab-separated columns keep them visually distinguishable.
            row_text = "\t".join(row_pieces)
            if annotate_fonts:
                # Pull the dominant font from the first span of the first column.
                first_span = rows[y][0][1][0]
                font = first_span.get("font", "?")
                size = first_span.get("size", 0)
                tag = font_flag_str(first_span.get("flags", 0))
                row_text = f"[{font} {size:.1f}pt {tag}] {row_text}"
            out.append(indent_for(rows[y][0][0]) + row_text)
    else:
        # Raw block-by-block rendering, in the order the PDF lists them.
        for blk in body_blocks:
            if blk["_kind"] == "image":
                x0, y0, x1, y1 = blk["bbox"]
                out.append(
                    f"[image at x={x0:.0f},y={y0:.0f} "
                    f"size={x1 - x0:.0f}x{y1 - y0:.0f} pt]"
                )
                continue
            out.extend(render_lines(blk["lines"], indent=True, annotate=annotate_fonts))

    if footer_lines:
        out.append(f"----- Page {page_num} (footer) -----")
        out.extend(render_lines(footer_lines, indent=True, annotate=annotate_fonts))

    return out


def extract_images(doc: fitz.Document, out_dir: Path) -> int:
    """Extract every embedded image to *out_dir*. Returns the number saved."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    for page_index, page in enumerate(doc, start=1):
        for img_index, img in enumerate(page.get_images(full=True), start=1):
            xref = img[0]
            try:
                pix = fitz.Pixmap(doc, xref)
            except Exception:
                continue
            # Convert CMYK / alpha to RGB so the PNG writer accepts it.
            if pix.n - pix.alpha >= 4:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            ext = "png"
            path = out_dir / f"page{page_index:03d}_img{img_index:03d}.{ext}"
            pix.save(path)
            pix = None
            saved += 1
    return saved


def ocr_page(page: fitz.Page) -> str:
    """Render a page to a high-DPI image and OCR it with Tesseract."""
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
        import io
    except ImportError:
        return "[OCR unavailable: install pytesseract and Pillow]"

    pix = page.get_pixmap(dpi=300)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img)


_STRIP_PATTERNS = [
    (re.compile(r"\*\*\*(.+?)\*\*\*"), r"\1"),  # bold+italic
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),       # bold
    (re.compile(r"\*(.+?)\*"), r"\1"),          # italic
    (re.compile(r"`(.+?)`"), r"\1"),             # mono
]


def strip_formatting(text: str) -> str:
    for pattern, repl in _STRIP_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Convert a PDF to a formatting-preserving TXT file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("pdf", help="Input PDF file")
    ap.add_argument("-o", "--output", help="Output TXT path (default: <pdf>.txt)")
    ap.add_argument("--no-format", action="store_true",
                    help="Strip **bold**/*italic*/`mono` markers, plain text only")
    ap.add_argument("--no-columns", action="store_true",
                    help="Disable multi-column detection (raw block order)")
    ap.add_argument("--annotate-fonts", action="store_true",
                    help="Prefix each line with its font name and size")
    ap.add_argument("--extract-images", metavar="DIR",
                    help="Extract embedded images into DIR")
    ap.add_argument("--json", action="store_true",
                    help="Also write a sidecar .json with full structured data")
    ap.add_argument("--ocr", action="store_true",
                    help="Use Tesseract OCR for image-only pages (fallback)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        sys.stderr.write(f"ERROR: file not found: {pdf_path}\n")
        return 1

    out_path = Path(args.output) if args.output else pdf_path.with_suffix(".txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    n_pages = doc.page_count
    all_lines: list[str] = []
    structured: dict = {"source": str(pdf_path), "pages": []}

    pages_with_no_text: list[int] = []

    for i, page in enumerate(doc, start=1):
        page_data = page.get_text("dict")
        structured["pages"].append(page_data)

        body_text = page.get_text("text").strip()
        if not body_text:
            pages_with_no_text.append(i)
            if args.ocr:
                ocr_text = ocr_page(page).rstrip()
                all_lines.append(f"\n===== Page {i} (OCR) =====")
                if ocr_text:
                    all_lines.append(ocr_text)
                else:
                    all_lines.append("[OCR returned no text]")
                continue
            all_lines.append(f"\n===== Page {i} =====")
            all_lines.append(
                "[No extractable text on this page. "
                "It may be a scanned image — re-run with --ocr.]"
            )
            continue

        lines = extract_page(
            page, i,
            annotate_fonts=args.annotate_fonts,
            detect_columns=not args.no_columns,
        )
        all_lines.extend(lines)
        all_lines.append("")  # blank separator between pages

    text = "\n".join(all_lines)
    if args.no_format:
        text = strip_formatting(text)

    out_path.write_text(text, encoding="utf-8")
    print(f"Wrote {out_path}  ({n_pages} page{'s' if n_pages != 1 else ''})")

    if pages_with_no_text and not args.ocr:
        print(
            f"NOTE: {len(pages_with_no_text)} page(s) had no extractable text "
            f"(pages {pages_with_no_text}). Re-run with --ocr to OCR them.",
            file=sys.stderr,
        )

    if args.extract_images:
        n = extract_images(doc, Path(args.extract_images))
        print(f"Extracted {n} image{'s' if n != 1 else ''} -> {args.extract_images}")

    if args.json:
        json_path = out_path.with_suffix(".json")
        json_path.write_text(
            json.dumps(structured, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Wrote {json_path}  (full structured data)")

    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

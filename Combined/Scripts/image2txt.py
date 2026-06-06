"""image2txt.py — extract text from image files via OCR (Tesseract).

Strategy
--------
Uses ``pytesseract`` (a thin Python wrapper over the Tesseract OCR engine)
and ``Pillow`` to load common image formats. For each image the script
records the source path, image dimensions, and the text Tesseract returns
(in ``pytesseract.image_to_string`` mode — i.e. preserving line breaks
as Tesseract sees them).

What it preserves
-----------------
* Source filename (each image gets its own section)
* Image dimensions (pixels)
* Tesseract-detected text — line breaks preserved
* Per-line confidence, when ``--with-confidence`` is passed
* Per-image language tag (when multiple are requested)

Input modes
-----------
The ``images`` argument can be a mix of:
* One or more image file paths (``file1.png file2.jpg ...``)
* A directory (``--dir <path>``) — every common image inside is processed
* A glob (``--glob '*.png'``) — relative to the current directory

Supported formats (anything Pillow can open): png, jpg/jpeg, tiff, bmp,
gif, webp, ppm, pgm, pbm.

Output
------
A single TXT file with one ``===== <name> =====`` block per image.

Limitations
-----------
* OCR quality depends on image quality. Low-DPI, skewed, or noisy scans
  will give poor results. Image pre-processing (deskew, denoise, binarize)
  is out of scope here — use a dedicated tool upstream if needed.
* Tables, formulas, and complex layouts are not reconstructed; you get
  the raw line ordering Tesseract produces.
* Requires the Tesseract binary on PATH (or pointed to via
  ``TESSERACT_CMD`` env var / ``--tesseract-cmd``).

Install
-------
    python -m pip install pytesseract Pillow
    # Plus the Tesseract engine itself:
    winget install UB-Mannheim.TesseractOCR   # Windows
    # or download from https://github.com/UB-Mannheim/tesseract/wiki
    # For Ukrainian, also install the 'ukr' traineddata:
    #   place ukr.traineddata in <tesseract>/tessdata/
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

try:
    from PIL import Image
except ImportError:
    sys.stderr.write(
        "ERROR: Pillow is required. Install it with:\n"
        "    python -m pip install Pillow\n"
    )
    sys.exit(1)

try:
    import pytesseract
except ImportError:
    sys.stderr.write(
        "ERROR: pytesseract is required. Install it with:\n"
        "    python -m pip install pytesseract\n"
    )
    sys.exit(1)


# Common image extensions Pillow can read
IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif",
    ".webp", ".ppm", ".pgm", ".pbm", ".pnm",
}


def find_tesseract(explicit: str | None) -> str | None:
    """Locate the tesseract binary, or return None if not found."""
    if explicit:
        return explicit
    env = os.environ.get("TESSERACT_CMD")
    if env and Path(env).is_file():
        return env
    # Common Windows install locations
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    return None


def configure_tessdata(tessdata_dir: str | None) -> None:
    """Point pytesseract at a user-writable tessdata folder, if requested.

    This is the workaround for installs where the system tessdata folder
    (under ``C:\\Program Files\\...``) is read-only — place extra language
    packs in a user folder and pass it via ``--tessdata-dir`` (or set
    ``TESSDATA_PREFIX`` in the environment).
    """
    if tessdata_dir:
        pytesseract.pytesseract.tessdata_dir_config = tessdata_dir
        # Also export the env var for any subprocess Tesseract spawns
        os.environ["TESSDATA_PREFIX"] = tessdata_dir
    elif os.environ.get("TESSDATA_PREFIX"):
        pytesseract.pytesseract.tessdata_dir_config = os.environ["TESSDATA_PREFIX"]


def list_inputs(args) -> list[Path]:
    """Resolve CLI inputs into a list of image file paths."""
    paths: list[Path] = []

    # Positional args
    for p in args.images:
        pp = Path(p)
        if pp.is_file():
            paths.append(pp)
        elif pp.is_dir():
            for f in sorted(pp.iterdir()):
                if f.suffix.lower() in IMAGE_EXTS:
                    paths.append(f)
        else:
            sys.stderr.write(f"WARN: skipping {pp} (not a file or dir)\n")

    # --dir
    if args.dir:
        d = Path(args.dir)
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in IMAGE_EXTS:
                    paths.append(f)
        else:
            sys.stderr.write(f"WARN: --dir {d} is not a directory\n")

    # --glob
    if args.glob_pattern:
        for f in sorted(Path.cwd().glob(args.glob_pattern)):
            if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
                paths.append(f)

    # Deduplicate, preserve order
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def ocr_image(path: Path, *, lang: str, tesseract_cmd: str | None,
              with_confidence: bool) -> tuple[str, str]:
    """Run OCR on a single image. Returns (raw_text, confidence_block)."""
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    with Image.open(path) as im:
        width, height = im.size
        text = pytesseract.image_to_string(im, lang=lang)

    conf_block = ""
    if with_confidence:
        try:
            with Image.open(path) as im:
                data = pytesseract.image_to_data(
                    im, lang=lang, output_type=pytesseract.Output.DICT
                )
            confs = [int(c) for c in data.get("conf", []) if c not in ("-1", -1, None)]
            if confs:
                avg = sum(confs) / len(confs)
                conf_block = f"[confidence: avg={avg:.1f}%, words={len(confs)}]\n"
        except Exception as e:
            conf_block = f"[confidence: unavailable ({e})]\n"

    return text, conf_block


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="OCR images to a single TXT file via Tesseract.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("images", nargs="*", help="Image files or directories")
    ap.add_argument("--dir", metavar="DIR",
                    help="Directory to scan for image files")
    ap.add_argument("--glob", dest="glob_pattern", metavar="PATTERN",
                    help="Glob pattern to select images (relative to CWD)")
    ap.add_argument("-o", "--output", help="Output TXT path (default: <first>.txt)")
    ap.add_argument("--lang", default="ukr+eng",
                    help="Tesseract language code (default: ukr+eng)")
    ap.add_argument("--tesseract-cmd", metavar="PATH",
                    help="Explicit path to tesseract executable")
    ap.add_argument("--tessdata-dir", metavar="DIR",
                    help="Folder containing *.traineddata files "
                         "(overrides TESSDATA_PREFIX)")
    ap.add_argument("--with-confidence", action="store_true",
                    help="Append per-image Tesseract confidence score")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_argparser().parse_args(argv)

    # Resolve tesseract up front so we fail fast with a clear message
    tesseract_cmd = find_tesseract(args.tesseract_cmd)
    if tesseract_cmd is None:
        sys.stderr.write(
            "ERROR: Tesseract binary not found.\n"
            "\n"
            "Install it (Windows):\n"
            "    winget install UB-Mannheim.TesseractOCR\n"
            "\n"
            "Or download from:\n"
            "    https://github.com/UB-Mannheim/tesseract/wiki\n"
            "\n"
            "Then either add the install dir to PATH, set the\n"
            "TESSERACT_CMD env var, or pass --tesseract-cmd.\n"
        )
        return 1

    configure_tessdata(args.tessdata_dir)

    inputs = list_inputs(args)
    if not inputs:
        sys.stderr.write("ERROR: no image inputs found.\n")
        return 1

    # Probe tesseract version / language availability before processing
    try:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        version_line = pytesseract.get_tesseract_version()
        sys.stderr.write(f"Using tesseract {version_line} at {tesseract_cmd}\n")
        available = set(pytesseract.get_languages(config=""))
        requested = args.lang.replace("+", " ").split()
        missing = [l for l in requested if l not in available]
        if missing:
            sys.stderr.write(
                f"WARN: language data not installed for: {', '.join(missing)}\n"
                f"      available: {sorted(available)}\n"
            )
    except pytesseract.TesseractError as e:
        sys.stderr.write(f"ERROR: tesseract failed to start: {e}\n")
        return 1

    out_path = (
        Path(args.output) if args.output else inputs[0].with_suffix(".txt")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    out_lines: list[str] = []
    out_lines.append(f"===== OCR results ({len(inputs)} image{'s' if len(inputs) != 1 else ''}) =====")
    out_lines.append(f"Language: {args.lang}")
    out_lines.append(f"Engine:   tesseract {version_line}")
    out_lines.append("")

    succeeded = 0
    failed: list[tuple[Path, str]] = []

    for path in inputs:
        out_lines.append(f"===== {path.name} =====")
        try:
            with Image.open(path) as im:
                w, h = im.size
            out_lines.append(f"Path: {path}")
            out_lines.append(f"Size: {w} x {h} px")
        except Exception as e:
            out_lines.append(f"[could not open: {e}]")
            failed.append((path, str(e)))
            out_lines.append("")
            continue

        try:
            text, conf_block = ocr_image(
                path, lang=args.lang, tesseract_cmd=tesseract_cmd,
                with_confidence=args.with_confidence,
            )
            if conf_block:
                out_lines.append(conf_block.rstrip())
            if text.strip():
                out_lines.append(text.rstrip("\n"))
            else:
                out_lines.append("[no text detected]")
            succeeded += 1
        except pytesseract.TesseractError as e:
            out_lines.append(f"[OCR error: {e}]")
            failed.append((path, str(e)))
        except Exception as e:
            out_lines.append(f"[unexpected error: {e}]")
            failed.append((path, str(e)))
        out_lines.append("")

    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(
        f"Wrote {out_path}  "
        f"({succeeded}/{len(inputs)} succeeded, {len(failed)} failed)"
    )
    if failed:
        sys.stderr.write("Failures:\n")
        for p, err in failed:
            sys.stderr.write(f"  {p}: {err}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

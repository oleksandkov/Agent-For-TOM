---
name: document-style-match
description: Reproduce an example document's exact layout and text formatting when the user provides a PDF or DOCX file as a sample and asks for a similar document with new content.
triggers: ["зроби схожий", "схожий файл", "схожий", "подібний файл", "подібний", "такий же", "збережи форматування", "зберегти форматування", "зберіг форматування", "форматування тексту", "форматування", "таке саме форматування", "як приклад", "візьми як приклад", "проаналізуй файл", "проаналізуй", "по типу", "як цей", "як у прикладі", "за зразком", "на основі", "шаблон", "зразок", "приклад", "методичні вказівки", "методичка", "like this", "same style", "match formatting", "preserve formatting", "document template", "as an example"]
source: bundled
version: 4
---

# Matching an Example Document (PDF / DOCX)

Used when the user provides a sample document (PDF/DOCX) and asks to reproduce its exact visual formatting (e.g. "зроби схожий файл", "збережи форматування", "як приклад", "same style as this file") with new content.

## The Rule: Do Not Build Piecemeal

**Do not construct the document via basic `add_paragraph` / `add_heading` calls.** They omit alignments and indentation, producing plain left-aligned text.
Instead:
1. Analyse the reference document via Python.
2. Build the new document with **one Python script** (`python-docx`) using explicit formatting helpers.
3. **Verify and compare** the generated document against reference metrics before finalizing.

---

## Step 1 — Analyze the Reference Document

Inspect structural properties using Python scripts via `run_command`:

### DOCX Analysis:
```python
import docx
d = docx.Document(r"<sample.docx>")
for i, p in enumerate(d.paragraphs):
    if p.text.strip():
        runs_info = [(r.text[:20], r.bold, r.italic) for r in p.runs[:3]]
        print(f"P{i:02d} | align={p.alignment} | indent={p.paragraph_format.first_line_indent} | {runs_info} | {repr(p.text[:50])}")
```

### PDF Analysis:
```python
import fitz  # PyMuPDF
doc = fitz.open(r"<sample.pdf>")
for i in range(min(len(doc), 3)):
    p = doc[i]
    fonts = { (s["font"], round(s["size"], 1)) for b in p.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l.get("spans", []) if s["text"].strip() }
    print(f"Page {i+1} rect={p.rect} fonts={fonts}")
```

---

## Step 2 — State the Style Contract

Document the extracted rules:
- **Font & size**: e.g., Times New Roman 14 pt (Body), 14 pt Bold (Headings).
- **Margins**: Top/Bottom 2.0 cm, Left 2.5–3.0 cm, Right 1.5 cm.
- **Body**: Justified (`A.JUSTIFY`), first-line indent 1.25 cm (`Cm(1.25)`), line spacing 1.15.
- **Headings & Titles**: Centered (`A.CENTER`), Bold, spacing before/after.
- **Lists / Numbering**: `List Number` or indented paragraphs with bold lead labels.

---

## Step 3 — Generate in One Script

Write a single script with formatting helpers and run it via `run_command`:

```python
import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH as A
from docx.shared import Pt, Cm

OUT = r"<output.docx>"
FONT_NAME, BASE_SIZE = "Times New Roman", Pt(14)

doc = docx.Document()
doc.styles["Normal"].font.name = FONT_NAME
doc.styles["Normal"].font.size = BASE_SIZE

for section in doc.sections:
    section.top_margin, section.bottom_margin = Cm(2.0), Cm(2.0)
    section.left_margin, section.right_margin = Cm(2.5), Cm(1.5)

def para(text="", *, align=A.JUSTIFY, bold=False, italic=False, indent=None, style=None, space_after=Pt(2), space_before=Pt(0)):
    p = doc.add_paragraph(style=style)
    p.alignment = align
    p.paragraph_format.line_spacing = 1.15
    p.paragraph_format.space_before, p.paragraph_format.space_after = space_before, space_after
    if indent is not None:
        p.paragraph_format.first_line_indent = indent
    if text:
        r = p.add_run(text)
        r.bold, r.italic = bold, italic
        r.font.name, r.font.size = FONT_NAME, BASE_SIZE
    return p

body   = lambda t: para(t, align=A.JUSTIFY, indent=Cm(1.25))
centre = lambda t, bold=True: para(t, align=A.CENTER, bold=bold, space_before=Pt(4), space_after=Pt(4))
item   = lambda t: para(t, align=A.JUSTIFY, style="List Number")

def lead(label, rest, indent=Cm(1.25)):
    p = para(align=A.JUSTIFY, indent=indent)
    r1 = p.add_run(label); r1.bold = True; r1.font.name, r1.font.size = FONT_NAME, BASE_SIZE
    r2 = p.add_run(rest); r2.font.name, r2.font.size = FONT_NAME, BASE_SIZE
    return p

# Content assembly
centre("НАЗВА ДОКУМЕНТА", bold=True)
body("Вступний текст із відступом 1.25 см та вирівнюванням по ширині.")
lead("Мета роботи: ", "опис мети...")
centre("Завдання")
item("Пункт перший")
item("Пункт другий")

doc.save(OUT)
print("Saved:", OUT)
```

---

## Step 4 — Verification and Comparison (Mandatory)

Run an inspection script to compare generated output against the contract:

```python
import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH as A

d = docx.Document(r"<output.docx>")
stats = {
    "total": len(d.paragraphs),
    "centred": sum(1 for p in d.paragraphs if p.alignment == A.CENTER),
    "justified": sum(1 for p in d.paragraphs if p.alignment == A.JUSTIFY),
    "indented": sum(1 for p in d.paragraphs if p.paragraph_format.first_line_indent),
    "lists": sum(1 for p in d.paragraphs if p.style.name.startswith("List")),
    "tables": len(d.tables),
}
print("VERIFICATION:", stats)
assert stats["total"] > 0 and stats["centred"] > 0 and stats["justified"] > 0 and stats["indented"] > 0, "Style check failed!"
```

If PDF is requested, convert via `convert_to_pdf` (MCP `word-docs` or `docx2pdf`/LibreOffice).

---

## Step 5 — Reporting

In your final reply:
1. Provide paths to `.docx` (and `.pdf` if generated).
2. Report verification metrics (centred, justified, indented counts, lists, tables).
3. Confirm style parity with the sample document.

---
name: document-style-match
description: Reproduce an example document's exact layout and text formatting when the user provides a PDF or DOCX file as a sample and asks for a similar document with new content.
triggers: ["зроби схожий", "схожий файл", "схожий", "подібний файл", "подібний", "такий же", "збережи форматування", "зберегти форматування", "зберіг форматування", "форматування тексту", "форматування", "таке саме форматування", "як приклад", "візьми як приклад", "проаналізуй файл", "проаналізуй", "по типу", "як цей", "як у прикладі", "за зразком", "на основі", "шаблон", "зразок", "приклад", "методичні вказівки", "методичка", "like this", "same style", "match formatting", "preserve formatting", "document template", "as an example"]
source: bundled
version: 9
---

# Matching an Example Document

Content changes, formatting does not: page size, margins, fonts, sizes,
alignment, spacing numerically identical to the sample — "looks about
right" is not the bar. This skill ships scripts next to this file so the
numbers come from one canonical, tested measurement, not one re-derived
each session: `analyze_pdf.py`/`analyze_docx.py`, `verify_docx.py`/
`verify_pdf.py`, `template_generate.py` (reference patterns).

Each measured a real failure this skill now catches: generic text read →
US Letter instead of A4; left margin at the 2.5cm convention vs the
sample's measured 2.35cm; a *correct* DOCX → PDF with an **invisible
~0.5pt title** no DOCX-only check catches. Rules:

1. **Never read the sample with a generic file/text tool** — only Step 1.
2. **Contract is a file, not a memory** — written/read/diffed in Steps 1/3/4.
3. **Verify the rendered PDF, not just the DOCX.**
4. **Step 1 ends at the contract; content research is bounded too.** One
   session re-audited unrelated files, another re-researched the same names
   25 times — neither ever reached Step 3. Verify a fact once, move on.

---

## Step 1 — Get the Contract

**If `<name>_style_contract.json` exists**, read it and skip to Step 2 — a
prior run already paid this cost.

Otherwise, run the matching script from this skill's own directory:
```
python skills/document-style-match/analyze_pdf.py "<sample.pdf>" "<name>_style_contract.json"
```
or, for a DOCX sample:
```
python skills/document-style-match/analyze_docx.py "<sample.docx>" "<name>_style_contract.json"
```
Read the printed JSON: page size, body/title font+size, left/right margins
(mode, not min/max — see each docstring). DOCX *also* measures
indent/line-spacing/heading-space exactly; PDF can't (tested: inferring
spacing from glyph positions gave a different wrong "rule" per heading —
Word merges adjacent spacing by *max*, not sum). PDF top/bottom margins and
all spacing stay convention — **say so in Step 5.** Then stop — rule 4.

## Step 2 — State the Contract

One line: `Contract: A4 21.0×29.7cm, Times New Roman, body 14pt, title
20pt, margins 2.35/2.1/2.0/2.0cm.`

---

## Step 3 — Generate

**Several tool calls instead of one script is how alignment/indent end up
on some paragraphs and not others — measured.** Every content string below
is illustrative: replace all of them.

```python
import docx, json
from docx.enum.text import WD_ALIGN_PARAGRAPH as A
from docx.shared import Pt, Cm

c = json.load(open(r"<name>_style_contract.json"))
FONT = c.get("body_font") or "Times New Roman"
BODY, TITLE = Pt(c["body_size_pt"] or 14), Pt(c["title_size_pt"] or 20)
INDENT, LS = Cm(c.get("body_indent_cm", 1.25)), c.get("line_spacing", 1.15)
HSB, HSA = Pt(c.get("heading_space_before_pt", 0)), Pt(c.get("heading_space_after_pt", 4))

doc = docx.Document()
doc.styles["Normal"].font.name, doc.styles["Normal"].font.size = FONT, BODY
m = c.get("margins_cm", {})
for sec in doc.sections:
    sec.page_width, sec.page_height = Cm(c["page_width_cm"]), Cm(c["page_height_cm"])
    sec.top_margin, sec.bottom_margin = Cm(m.get("top_margin", 2.0)), Cm(m.get("bottom_margin", 2.0))
    sec.left_margin, sec.right_margin = Cm(m.get("left_margin", 2.5)), Cm(m.get("right_margin", 1.5))

def para(text="", *, align=A.JUSTIFY, bold=False, indent=None, style=None, size=None, sb=Pt(0), sa=Pt(2)):
    p = doc.add_paragraph(style=style)
    p.alignment, p.paragraph_format.line_spacing = align, LS
    p.paragraph_format.space_before, p.paragraph_format.space_after = sb, sa
    if indent is not None:
        p.paragraph_format.first_line_indent = indent
    if text:
        r = p.add_run(text); r.bold = bold; r.font.name, r.font.size = FONT, (size or BODY)
    return p

body   = lambda t: para(t, indent=INDENT)
centre = lambda t, size=None: para(t, align=A.CENTER, bold=True, size=size, sb=HSB, sa=HSA)
item   = lambda t: para(t, style="List Number")  # ONE list — 2nd list below

def lead(label, rest, indent=None):  # "Мета роботи - текст" ONE line, not a heading
    p = para(align=A.JUSTIFY, indent=indent or INDENT)
    r1 = p.add_run(label); r1.bold = True; r1.font.name, r1.font.size = FONT, BODY
    r2 = p.add_run(rest); r2.font.name, r2.font.size = FONT, BODY
    return p

centre("<ACTUAL TITLE — replace this>", size=TITLE)
lead("<label, e.g. Мета роботи> - ", "<same line, not a new paragraph>")
body("<actual body text — replace this>")
doc.save(r"<output.docx>")
```
**2nd numbered list or a table?** Plain `item()` continues numbering
instead of restarting at 1 (measured: rendered 4,5,6) — read
`template_generate.py` (same folder) for tested `new_list()`/`table()`.

**`centre` vs `lead` — check the sample, don't assume.** Skipping this once
rendered `"Мета роботи"` as a heading with the description on the next
paragraph; the sample has it bold, inline, one line. True headings
("Загальні відомості", "Контрольні запитання", "Завдання", "Література")
are centered/standalone; a label + its own text on one line is `lead`.

---

## Step 4 — Verify

Run both scripts before reporting done; on failure, fix Step 3, re-run, up
to 3 attempts, then report exactly what's wrong, not success.

**4a — Diff the DOCX against the contract**, not a self-check (would pass
the Letter-vs-A4 bug above):
```
python skills/document-style-match/verify_docx.py "<name>_style_contract.json" "<output.docx>"
```

**4b — After `convert_to_pdf`, verify the PDF too**:
```
python skills/document-style-match/verify_pdf.py "<name>_style_contract.json" "<output.pdf>"
```
Confirm the file exists next to the DOCX (conversion can report success
while writing elsewhere).

**4c — Grep for `"<...>"` brackets** — Step 3's placeholder syntax, left
over if not replaced.

## Step 5 — Reporting

State the contract used (Step 2's line), flag anything assumed, and confirm
both verify scripts passed — not "looks similar". A referenced figure
("Рис. 1") keeps its caption text — don't fabricate one.

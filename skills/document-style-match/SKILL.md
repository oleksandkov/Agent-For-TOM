---
name: document-style-match
description: Reproduce an example document's exact layout when the user asks for something "like this file" — analyse the sample's alignment, fonts, numbering and spacing, then build a .docx/.pdf that matches it rather than a generic Word document
triggers: ["like this", "same style", "as an example", "по типу", "як цей", "як у прикладі", "за зразком", "на основі", "методичні вказівки", "методичка", "methodichka", "docx", "pdf", "template", "шаблон", "зразок", "приклад"]
source: bundled
version: 3
---

# Matching an example document

For when the user points at a file and asks for something *like it*: "по типу
до цих", "за зразком", "same style as", "use this as a template".

## The rule, before anything else

**Do not build the document with `add_paragraph` and `add_heading`.** They
take no alignment and no indent parameter, so a document built from them
cannot be centred, justified or indented — it will not match the sample
however well you word it. Reaching for them is the failure this skill exists
to prevent, and it is the natural mistake because they sit right there in your
tool list. Measured: a build that used 30 of those calls scored **0 centred,
0 justified, 0 indented, 0 list items** and took 39 tool calls over 7 minutes.

Write **one python-docx script** and run it with `run_command` (Step 3). Use
the word-docs MCP server for two things only: `convert_to_pdf` at the end, and
small edits to a document that already exists.

## Step 1 — Read the sample first

Layout cannot be inferred from extracted text; the text is what loses it.

**PDF:** `read_file` it in pages until you have seen one complete unit (for a
methodichka, one full lab: title through references). Note what is centred,
what is bold, how items are numbered, and the exact section names and order.

**DOCX:** get the real properties via `run_command`:

```python
import docx
d = docx.Document(r"<sample.docx>")
for i, p in enumerate(d.paragraphs):
    if p.text.strip():
        print(i, p.style.name, p.alignment,
              p.paragraph_format.first_line_indent, repr(p.text[:60]),
              [(r.text[:20], r.bold, r.italic) for r in p.runs])
```

## Step 2 — State the style contract

List the conventions before building. A typical Ukrainian methodichka:

| Element | Convention |
|---|---|
| Title page | centred, bold, ALL CAPS, own page (page break after) |
| `ЛАБОРАТОРНА РОБОТА №N`, topic | centred, bold |
| `Мета роботи` | bold inline run, then plain text, same paragraph |
| Section headings | centred bold plain paragraph — **not** a Word Heading style |
| Body | justified, first-line indent 1.25 cm |
| Numbered items | one paragraph each, `List Number` style |
| `Зауваження.` | italic, inline with its sentence |
| `Література` | centred bold heading, one numbered item per source |

Keep the sample's own section names. Do not translate or improve them.

## Step 3 — Build it in one script

Write it to a temp path — it is a helper, not a deliverable — and run it.

```python
import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH as A
from docx.shared import Pt, Cm

OUT = r"<output.docx>"
FONT, SIZE = "Times New Roman", Pt(14)
d = docx.Document()
d.styles["Normal"].font.name = FONT
d.styles["Normal"].font.size = SIZE

def para(text="", *, align=A.JUSTIFY, bold=False, italic=False,
         indent=None, style=None):
    p = d.add_paragraph(style=style)
    p.alignment = align
    if indent is not None:
        p.paragraph_format.first_line_indent = indent
    if text:
        r = p.add_run(text)
        r.bold, r.italic = bold, italic
        r.font.name, r.font.size = FONT, SIZE
    return p

body   = lambda t: para(t, align=A.JUSTIFY, indent=Cm(1.25))
centre = lambda t: para(t, align=A.CENTER, bold=True)
item   = lambda t: para(t, align=A.JUSTIFY, style="List Number")

def lead(label, rest):                      # bold label + plain rest, one para
    p = para(align=A.JUSTIFY, indent=Cm(1.25))
    for txt, b in ((label, True), (rest, False)):
        r = p.add_run(txt); r.bold = b
        r.font.name, r.font.size = FONT, SIZE

for _ in range(8):
    para()                                  # title page sits low on the page
centre("МЕТОДИЧНІ ВКАЗІВКИ ДО ВИКОНАННЯ")
centre("ЛАБОРАТОРНИХ РОБІТ З ДИСЦИПЛІНИ")
centre("«...»")
d.add_page_break()

centre("ЛАБОРАТОРНА РОБОТА №N")
centre("<topic>")
lead("Мета роботи", " - <goal>.")
centre("Загальні відомості")
body("<theory>")
centre("Контрольні запитання")
for q in ["<q1>", "<q2>"]:
    item(q)
centre("Завдання")
for t in ["<t1>", "<t2>"]:
    item(t)
para("Зауваження. <text>", italic=True, indent=Cm(1.25))
centre("Література")
for s in ["<ref1>"]:
    item(s)

d.save(OUT)
print("saved", OUT)
```

Keep the helpers, fill in real content. For a variants table use
`d.add_table(rows, cols)` and `table.style = "Table Grid"`.

## Step 4 — Verify, then convert

Count your own output. If centred is 0, it is not done:

```python
import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH as A
d = docx.Document(r"<output.docx>")
for name, test in (("centred", lambda p: p.alignment == A.CENTER),
                   ("justified", lambda p: p.alignment == A.JUSTIFY),
                   ("indented", lambda p: p.paragraph_format.first_line_indent),
                   ("list items", lambda p: p.style.name.startswith("List"))):
    print(name, sum(1 for p in d.paragraphs if test(p)))
```

Then `convert_to_pdf` on the finished .docx — it preserves what you applied.
Never re-typeset extracted text into a new PDF with fpdf2; that discards every
property above.

## Reporting

Give the Step 4 counts and name anything you could not reproduce. "Matched
centring (10), justification (22), 1.25 cm indent, 17 numbered items; footer
page numbers not reproduced" is useful. "Created both files" is not.

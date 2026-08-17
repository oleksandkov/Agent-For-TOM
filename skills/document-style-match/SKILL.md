---
name: document-style-match
description: Reproduce an example document's exact layout and text formatting when the user provides a PDF or DOCX file as a sample and asks for a similar document with new content.
triggers: ["зроби схожий", "схожий файл", "схожий", "подібний файл", "подібний", "такий же", "збережи форматування", "зберегти форматування", "зберіг форматування", "форматування тексту", "форматування", "таке саме форматування", "як приклад", "візьми як приклад", "проаналізуй файл", "проаналізуй", "по типу", "як цей", "як у прикладі", "за зразком", "на основі", "шаблон", "зразок", "приклад", "методичні вказівки", "методичка", "лабораторну", "лабораторна робота", "like this", "same style", "match formatting", "preserve formatting", "document template", "as an example"]
skip_when: the user is asking about, reviewing, summarising or advising on a document rather than asking for one to be produced. Words like "проаналізуй"/"приклад"/"форматування" retrieve this skill, and most of the time they appear in a question, not an order.
source: bundled
version: 19
---
# Matching an Example Document

Content changes, formatting does not. **Two commands do the whole job:**

```
python skills/document-style-match/run.py measure "<sample>" <name>
python skills/document-style-match/run.py build   "<sample>" <name> <name>_content_plan.json "<output.docx>"
```

`build` runs the shape check, the render, the PDF conversion and all three
verifications, and prints one line: `VERDICT: PASS` or `VERDICT: FAIL` with
what to fix. On a pass it deletes its own scaffolding. **Do not assemble the
steps by hand, and never report a document `build` has not passed.**

Assembling the steps by hand is how the one measured session that ignored this
skill shipped a US-Letter document at 1.15 line spacing with its headings
flush left, and called it "looks great".

---

## The four rules

1. **Never read the sample with `read_file`, `search_code` or a markdown
   converter.** They return text. Every defect this skill prevents lives in
   what they drop: page size, margins, spacing, alignment, weight.
2. **Never take a previous output as the reference.** Not `tests/*/v6/`, not
   your own last attempt. One run copied the shape of its own earlier output
   and inherited every defect in it. The reference is the file the user
   pointed at, and nothing else.
3. **Reach a `VERDICT: PASS` first, then improve the prose.** A short plan
   that passes beats a long one that never renders. Two of three sessions
   built the whole generator up front and ended with no deliverable. A turn
   is 1200s / 40 calls.
4. **Research is bounded, and comes after the first PASS.** One session
   re-researched the same names 25 times and generated nothing; another spent
   five of nineteen calls on the web and died before the first render.
   **Look inside the sample first** — a methodichka names its own authors in
   its literature list, and one run went to the web for that, picked the
   wrong department, and contradicted the document it was copying.

---

## The block schema

Read this instead of the scripts — sessions spend ~11k tokens on their
source otherwise.

```jsonc
{"text": "…", "align": "justify"|"center"|"left"|"right",
 "bold": true, "size_pt": 14.0, "indent_cm": 1.25,
 "source_index": 12,          // position in the structure — carry it through
 "page_break_before": true,   // optional
 "style_name": "Heading 1",   // optional, a real Word style
 "list_id": 3, "list_level": 0}   // optional, numbering restarts per list_id
{"kind": "spacer"}                    // empty paragraph = the vertical rhythm
{"kind": "table", "rows": [["a","b"], …]}
```

- **Indices are 0-based into `blocks`**, not `read_file`'s line numbers.
  Hand-subtracting the JSON header cost two sessions their turn. Enumerate
  in code, or use `source_index`.
- **`indent_cm: 0.0` means flush left** and is honoured. Never write `0.001`
  to defeat a fallback — that turned indentation off for a whole document.
  Only an *absent* `indent_cm` falls back.
- **A spacer has no `text`.** Check `kind` before reading `b["text"]`.
- **A block is a paragraph, not a line.** `target_chars` is how much text it
  held; write about that much. A block given one sentence where the sample
  had five ends on a half-empty line, and a page of those reads broken.
- **`align` and `bold` are the answer, not decoration** — what a reader
  checks first. Carry them through from the structure unchanged.

---

## Step 1 — `measure`

```
python skills/document-style-match/run.py measure "<sample>" <name>
```

Writes `<name>_style_contract.json` (~2 KB — page size, margins, fonts,
`line_spacing`, `body_indent_cm`, `style_signatures`) and
`<name>_structure.json` (the blocks). The contract is printed; **read it and
state it in one line**: `A4 21.0×29.7cm, Times New Roman, body 14pt, title
20pt, margins 2.35/2.1/2.0/2.0cm.`

If it prints a `page_window` NOTE, only part of the sample was opened. List
the rest of its sections before building the plan:

```
python -c "import pymupdf;d=pymupdf.open(r'<sample.pdf>');print(chr(10).join(l for p in d for l in p.get_text().splitlines() if l.strip() and l.strip()==l.strip().upper())[:3000])"
```

Then name, in your final answer, every section of the sample your document
does not contain. Not a choice: three of three runs read this field, said
nothing, and shipped a document missing those sections.

Spacers and breaks are *inferred* from geometry — say so, then stop
researching the sample.

## Step 2 — write the plan

**Route B (PDF sample) — `<name>_content_plan.json`:** load
`<name>_structure.json`'s `blocks`, replace each `"text"`, keep everything
else — **including `source_index`, `align` and `bold`** — and honour
`target_chars`. Do not author from scratch: one such run gave 57 blocks
against 400 with no tables or headings. For a long sample write a generator;
a 1599-block literal cost one session 29k tokens.

**Route A (DOCX sample) — `<name>_edits.json` instead**, and `run.py build`
picks it automatically from the `.docx` suffix. Rebuilding a DOCX reproduces
only what a contract describes: on a real coursework it kept 57 of 400 blocks
and lost **all 4 tables, all 22 heading styles and 2 of 3 sections** while
passing every style check. Copying loses none. List the blocks with
`edit_copy.py --list "<sample.docx>" [start] [count]`, then key the edits by
those indices — only listed blocks change:

```json
{ "9": "Ілон Маск",
  "11": ["Мета роботи - ", "новий текст"],
  "12": null,
  "31": {"rows": [["ВСТУП", "4"]]} }
```

string = replace text · list = one entry per run (keeps a bold label bold)
· `null` = delete · `{"rows": …}` = table cell text.

**A label plus its own text is one line, not a heading.** The sample has
`"Мета роботи"` bold, inline, justified — not a heading with the
description below it.

## Step 3 — `build`

```
python skills/document-style-match/run.py build "<sample>" <name> <plan> "<output.docx>"
```

Add `--pdf <path>` if you converted separately, `--keep` to keep the working
files on a pass. It stops at the first step whose failure makes the next one
meaningless, so fix what it names and re-run the same command.

---

## Reading a failure

| Check | Passes when | If it fails |
|---|---|---|
| `check_plan` | blocks ≥ 50 % of sample; each category (spacers, tables, breaks, headings, lists) ≥ 50 %; ≥ 80 % of plan blocks carry a `source_index`; total text within ±35 % of the summed `target_chars` | the plan was authored, not adapted — start again from the structure's blocks |
| `verify_docx` | every contract field within ±0.05 cm / ±0.5 pt, **and** the sample's formatting vocabulary survived | a scalar mismatch means the generator ignored the contract for that field |
| `verify_pdf` | no page-1 block narrower than 15 pt (collapsed text) | the font size never reached the run |
| `verify_render` | split-line rate ≤ 1.5× the sample's; blank lines per 100 rows ≥ 55 % of the sample's; characters per row within ±30 %; line pitch within ±12 % | see below |

**`formatting lost` / `formatting invented`** — the reader-visible one.
`verify_docx` compares the *share* of each `(align, bold, size, indent)`
combination against the sample's. Measured on one real pair:

```
center+bold 20.0pt   7.3%  ->   absent   (title style gone)
center+bold 14.0pt  12.2%  ->     1.6%   (headings no longer centered)
left+bold   14.0pt  absent ->     6.3%   (what they became instead)
```

Fix it in the plan — the blocks kept their text and lost their `align`/`bold`.

**`line pitch`** — rendered line spacing. python-docx defaults to 1.15 and
writes nothing unless told; against a 1.5 sample that measures 18.60 against
24.12, and every other check passes it.

**`stretched lines`** — justification is opening up the word gaps because a
paragraph ends on a half-filled line. **Fix the text, not the renderer.**
Give the short blocks more prose, up to their `target_chars`. Every PDF
converter splits an over-stretched line; one session spent *half its turn*
disproving that — it tried reportlab, then Word COM, got the same number both
times, and shipped a final report explaining it away as a converter artifact.
It was not one.

**`NOT checked: …`** is part of the result. Say it out loud.

## Reporting

State the contract, flag what was assumed, name anything `NOT checked`, name
any section of the sample you could not reproduce, and quote the `VERDICT:
PASS` line. Never "looks similar" — that phrase, in a measured session,
described a document on the wrong paper size. A referenced figure keeps its
caption.

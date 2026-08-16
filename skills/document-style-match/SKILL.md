---
name: document-style-match
description: Reproduce an example document's exact layout and text formatting when the user provides a PDF or DOCX file as a sample and asks for a similar document with new content.
triggers: ["зроби схожий", "схожий файл", "схожий", "подібний файл", "подібний", "такий же", "збережи форматування", "зберегти форматування", "зберіг форматування", "форматування тексту", "форматування", "таке саме форматування", "як приклад", "візьми як приклад", "проаналізуй файл", "проаналізуй", "по типу", "як цей", "як у прикладі", "за зразком", "на основі", "шаблон", "зразок", "приклад", "методичні вказівки", "методичка", "like this", "same style", "match formatting", "preserve formatting", "document template", "as an example"]
source: bundled
version: 17
---
# Matching an Example Document

Content changes, formatting does not. **The route depends on the sample:**

| Sample | Route | Why |
|---|---|---|
| **.docx** | **A — copy & replace** | Formatting carried, never re-derived |
| **.pdf** | B — measure & rebuild | Nothing to copy; must be reconstructed |

Rules, both routes:

1. **Never read the sample with a generic file/text tool.**
2. **A failing check is never a success.** Fix, re-run (3 attempts), then
   say exactly what is still wrong.
3. **Verify the rendered PDF, not just the DOCX.**
4. **Research is bounded.** One session re-researched the same names 25
   times and never generated anything. Verify a fact once, move on.
5. **Clean up (Step Z) before the final answer.**
6. **Reach a rendered, verified pair on a short plan first, then grow the
   text.** Two of three sessions built the whole generator up front, spent
   the turn on index arithmetic, and ended with a script never run and no
   deliverable. A turn is 1200s / 40 calls.

---

# The block schema

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
- **`indent_cm: 0.0` means flush left** and is honoured. Never write
  `0.001` to defeat a fallback — that turned indentation off for a whole
  document. Only an *absent* `indent_cm` falls back.
- **A spacer has no `text`.** Check `kind` before reading `b["text"]`.
- **A block is a paragraph, not a line.** `source_rows` = how many rendered
  lines it was, `target_chars` = how much text it held. Write about that
  much: a block given one sentence where the sample had five ends on a
  half-empty line, and a page of those reads broken.

## What each check enforces, with its numbers

| Script | Passes when |
|---|---|
| `check_plan` | blocks ≥ 50 % of sample; each category (spacers, tables, breaks, headings, lists) ≥ 50 %; ≥ 80 % of plan blocks carry a `source_index`; total text within ±35 % of the summed `target_chars` |
| `verify_docx` | every contract field within ±0.05 cm / ±0.5 pt; prints `NOT checked: …` for fields the contract lacks — that line is part of the result |
| `verify_pdf` | no page-1 block narrower than 15 pt (collapsed text) |
| `verify_render` | split-line rate ≤ 1.5× the sample's; blank lines per 100 rows within ±30 %; characters per row within ±30 % |

---

# Route A — DOCX sample: copy and replace

Rebuilding reproduces only what a contract describes: on a real coursework
it kept 57 of 400 blocks and lost **all 4 tables, all 22 heading styles and
2 of 3 sections**, while passing every style check. Copying loses none.

**A1 — see the blocks** (indices are what you edit):
```
python skills/document-style-match/edit_copy.py --list "<sample.docx>" [start] [count]
```

**A2 — write `<name>_edits.json`**, keyed by those indices. Only listed
blocks change.
```json
{ "9": "Ілон Маск",
  "11": ["Мета роботи - ", "новий текст"],
  "12": null,
  "31": {"rows": [["ВСТУП", "4"]]} }
```
string = replace text · list = one entry per run (keeps a bold label bold)
· `null` = delete · `{"rows": …}` = table cell text.

**A3 — apply:**
```
python skills/document-style-match/edit_copy.py "<sample.docx>" "<name>_edits.json" "<output.docx>"
```
Exits 1 counting **long paragraphs still holding the sample's own text**,
and separately anything the copy **lost** — formulas, images, hyperlinks,
tables. Copying carries those but cannot edit them, and deleting is the easy
way out: one run shipped a paper with **0 of its sample's 28 formulas** and
passed every other check. Removing them can be right; it must be deliberate
and stated. Then Step V.

---

# Route B — PDF sample: measure and rebuild

**B1 — contract + structure:**
```
python skills/document-style-match/analyze_pdf.py "<sample.pdf>" "<name>_style_contract.json"
```
Contract (~2 KB): page size, margins, fonts, `line_spacing`,
`body_indent_cm`, `style_signatures` — printed, read it. State it in one
line: `A4 21.0×29.7cm, Times New Roman, body 14pt, title 20pt, margins
2.35/2.1/2.0/2.0cm.`

Check `truncated_reason`, not just `truncated`:

- `block_limit` — whole document walked, 400-block cap stopped it. The
  rest is more of the same.
- `page_window` — only `pages_scanned` of `page_count` opened. **Before
  building the plan**, list the rest of the sample's sections:
  ```
  python -c "import pymupdf;d=pymupdf.open(r'<sample.pdf>');print(chr(10).join(l for p in d for l in p.get_text().splitlines() if l.strip() and l.strip()==l.strip().upper())[:3000])"
  ```
  Then name, in your final answer, every section of the sample your
  document does not contain. Not a choice: three of three runs read this
  field, said nothing, and shipped a document missing those sections.

Spacers and breaks are *inferred* from geometry — say so. Then stop
researching the sample.

**B2 — build `<name>_content_plan.json` from the structure**: load its
`blocks`, replace each `"text"`, keep everything else — **including
`source_index`** (how `check_plan` tells an adapted plan from an invented
one) and honouring `target_chars`. Do not author from scratch: one such run
gave 57 blocks against 400 with no tables or headings. For a long sample
write a generator — a 1599-block literal cost one session 29k tokens.

**B3 — check the shape, before rendering:**
```
python skills/document-style-match/check_plan.py "<name>_structure.json" "<name>_content_plan.json"
```

**B4 — render:**
```
python skills/document-style-match/render_from_structure.py "<name>_style_contract.json" "<name>_content_plan.json" "<output.docx>"
```
Covers paragraphs, spacers, page breaks, heading styles, tables, lists.
Images, footnotes, TOC fields and merged cells are not — add those with
`python-docx` and re-save; `template_generate.py` has tested patterns.

**A label plus its own text is one line, not a heading.** The sample has
`"Мета роботи"` bold, inline, justified — not a heading with the
description below it.

---

# Step V — Verify (both routes)

```
python skills/document-style-match/verify_docx.py "<contract>.json" "<output.docx>" ["<structure>.json"]
```
Route A needs a contract first, via `analyze_docx.py` on the **sample**.
Without the structure file the spacer/page-break check says `NOT checked` —
not a full pass.

After `convert_to_pdf`, confirm the PDF landed beside the DOCX, then:
```
python skills/document-style-match/verify_pdf.py "<contract>.json" "<output.pdf>"
python skills/document-style-match/verify_render.py "<sample.pdf>" "<output.pdf>"
```
`verify_render` is the only check comparing the **result with the sample**
rather than with the contract — it catches a document whose every style
number is right and whose page still reads wrong. Read its NOTE about page
1: a cover page you filled on purpose is expected to move, and saying so is
part of the report.

Grep the output for `"<...>"` — leftover `template_generate.py` placeholders.

# Step Z — Clean up

The deliverables are the `.docx` and `.pdf`. Everything else this skill
made is scaffolding and must not be left in the user's folder:
```
del "<name>_style_contract.json" "<name>_structure.json" "<name>_content_plan.json" "<name>_edits.json"
```
plus any scratch scripts. Delete after the last check passes, in the same
turn as the final answer — one run left six such files beside two
deliverables. If the turn ends early, still say where your working files are.

# Reporting

State the contract, flag what was assumed, name anything `NOT checked`,
name any section of the sample you could not reproduce, confirm the checks
passed — not "looks similar". A referenced figure keeps its caption.

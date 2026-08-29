---
name: document-style-match
description: Reproduce an example document's exact layout and text formatting when the user provides a PDF or DOCX file as a sample and asks for a similar document with new content.
triggers: ["зроби схожий", "схожий файл", "схожий", "подібний файл", "подібний", "такий же", "збережи форматування", "зберегти форматування", "зберіг форматування", "форматування тексту", "форматування", "таке саме форматування", "як приклад", "візьми як приклад", "проаналізуй файл", "проаналізуй", "по типу", "як цей", "як у прикладі", "за зразком", "на основі", "шаблон", "зразок", "приклад", "методичні вказівки", "методичка", "лабораторну", "лабораторна робота", "like this", "same style", "match formatting", "preserve formatting", "document template", "as an example"]
skip_when: the user is asking about, reviewing, summarising or advising on a document rather than asking for one to be produced. Words like "проаналізуй"/"приклад"/"форматування" retrieve this skill, and most of the time they appear in a question, not an order.
tools: [run_command, read_file, write_file, edit_file, list_files, ask_user_question]
source: bundled
version: 20
---
# Matching an Example Document

Content changes, formatting does not. **Two commands do the whole job**, and
they are the same two whether the sample is a PDF or a DOCX:

```
python skills/document-style-match/run.py measure "<sample>" <name>
python skills/document-style-match/run.py build   "<sample>" <name> <texts.json> "<output.docx>"
```

`measure` writes a `texts.json` you fill in. `build` renders it, converts it,
runs every check, and prints one line: `VERDICT: PASS`, or `VERDICT: FAIL`
**naming the blocks to change**. Fix what it names and re-run the same
command. **Do not assemble the steps by hand, and never report a document
`build` has not passed.**

Assembling the steps by hand is how the one measured session that ignored this
skill shipped a US-Letter document at 1.15 line spacing with its headings
flush left, and called it "looks great".

---

## The four rules

1. **Never read the sample with `read_file`, `search_code` or a markdown
   converter.** They return text. Every defect this skill prevents lives in
   what they drop: page size, margins, spacing, alignment, weight.
2. **Never take a previous output as the reference, and never edit the sample
   to make it easier to copy.** The reference is the file the user pointed at.
   If the sample carries something you need gone — equations from the old
   topic — say so in the edit file (`drop_math`), do not doctor the sample.
3. **Reach a `VERDICT: PASS` first, then improve the prose.** A short plan
   that passes beats a long one that never renders. A turn is 1200s / 40 calls.
4. **Do not read this skill's scripts.** Every failure names the block to
   change and the direction to change it. A session that read all six spent
   27k tokens to learn what the message already said.

---

## Step 1 — `measure`

```
python skills/document-style-match/run.py measure "<sample>" <name>
```

Prints the contract in one line — **quote it back**, e.g. `A4 21.0×29.7cm ·
Times New Roman · body 14pt · title 20pt · margins 2.35/2.1/2/2cm`. Writes
everything into `.dsm/<name>/`, which is kept between runs; measuring the same
sample twice costs nothing.

If it prints `NOTE: only part of the sample was opened`, name in your final
answer every section of the sample your document does not contain. Not a
choice: three of three runs read this field, said nothing, and shipped a
document missing those sections.

## Step 2 — fill in `texts.json`

`measure` leaves `.dsm/<name>/texts.json` (a PDF sample) or `edits.json` (a
DOCX one) already in the right shape — **one entry per block, values only:**

```json
{ "15": "ЛАБОРАТОРНА РОБОТА №7",
  "19": ["Мета роботи - ", "новий текст"],
  "23": {"text": "новий текст", "drop_math": true},
  "31": {"rows": [["Варіант", "Тема"], ["1", "…"]]},
  "12": null,
  "__insert__": [{"after": 49, "blocks": [
      {"kind": "spacer"},
      {"text": "Варіанти", "align": "center", "bold": true, "size_pt": 14.0},
      "1. Розпізнавання продуктів за фотографією."]}] }
```

string = replace the text · list = one entry per run, so a bold label stays a
bold label · `null` = delete the block · `{"rows": …}` = table cells ·
`{"…", "drop_math": true}` = replace the text *and* remove the equations this
paragraph carries · `"__doc__": {"drop_math": true}` = remove them everywhere.

- **Keys are block indices**, 0-based, as `measure` wrote them. Not line
  numbers in the file.
- **A `<<TODO: N chars…>>` value is refused by `build`.** N is how much text
  the sample had there; write about that much. A block given one sentence
  where the sample had five ends on a half-empty line, and a page of those
  reads broken.
- **A `CARRIES n math` note means replacing the text leaves the equations
  behind.** Keep them or `drop_math` them; both are defensible, saying
  nothing is not.
- **`The link text lives outside the runs`** — end your replacement where the
  hyperlink begins (`"…, e-mail: "`), and the address survives.
- **A label plus its own text is one line, not a heading.** The sample has
  `"Мета роботи"` bold, inline, justified — not a heading with the
  description below it.
- In `__insert__`, a plain string inherits the anchor block's formatting; a
  block object states its own.

## Step 3 — `build`

```
python skills/document-style-match/run.py build "<sample>" <name> <texts.json> "<output.docx>"
```

`--pdf <path>` if you converted separately. It stops at the first step whose
failure makes the next one meaningless, so fix what it names and re-run the
same command.

---

## Reading a failure

Every failure is already specific — it names blocks, counts and a direction.
Act on the line, not on this table.

| Check | Passes when |
|---|---|
| `check_plan` | blocks ≥ 50 % of the sample; each category (spacers, tables, breaks, headings, lists) ≥ 50 %; ≥ 80 % of paragraphs use a formatting combination the sample has; total text within ±35 % of the summed `target_chars` |
| `verify_docx` | every contract field within ±0.05 cm / ±0.5 pt, **and** the sample's formatting vocabulary survived |
| `verify_pdf` | no page-1 block narrower than 15 pt (collapsed text) |
| `verify_render` | stretched-line rate ≤ 1.5× the sample's; blank lines per 100 rows ≥ 55 % of the sample's; characters per row within ±30 %; line pitch within ±12 % |

Two of these say something worth repeating:

**`formatting lost` / `formatting invented`** is the reader-visible one — the
blocks kept their text and lost their `align`/`bold`. Filling `texts.json`
cannot cause it; hand-writing a plan can.

**`stretched lines`** is justification opening the word gaps because a
paragraph ends on a partly-filled line. It names each offending row, the block
it came from, and whether to lengthen, trim or reword. Swapping renderers
cannot move it — one session tried reportlab and Word COM in turn and got the
same number both times. Table rows and hyperlinks are *not* counted here; they
are reported separately as column merges.

**`NOT checked: …`** and any `NOTE:` line are part of the result. Say them out
loud.

## Reporting

State the contract, flag what was assumed, name anything `NOT checked` or
`NOTE:`-d, name any section of the sample you could not reproduce, and quote
the `VERDICT: PASS` line. Never "looks similar" — that phrase, in a measured
session, described a document on the wrong paper size.

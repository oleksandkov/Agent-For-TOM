---
name: coursework-authoring
description: Create, format, audit, or finish Ukrainian university coursework in DOCX and PDF from supplied requirements, examples, and source material, including structure, text styles, tables, figures, diagrams, contents, citations, and final visual verification. Use for course papers and explanatory notes; do not use for short homework or presentation-only tasks.
triggers: ["курсова", "курсовий", "курсову", "coursework", "course paper", "пояснювальна записка"]
skip_when: the user asks for short homework, a presentation, or anything unrelated to a course paper or explanatory note — "курс" alone usually means an exchange rate, not this skill.
source: bundled
version: 1
---

# Coursework Authoring

Create a submission-ready DOCX and matching PDF while preserving the student's facts, calculations, code, and authorship.

## Workflow

1. Inventory every supplied requirement, sample, draft, source file, figure, and expected output.
2. Read the methodological requirements before treating the sample as a pattern. Resolve conflicts in this order: explicit user instruction, official requirements, approved sample, existing draft.
3. Audit the whole draft before editing: sections, styles, margins, headers/footers, page numbering, tables, figures, captions, citations, contents, and appendices.
4. Make the smallest safe edits. Preserve wording and unrelated formatting unless the user explicitly asks for content revision.
5. Apply the rules in [formatting.md](references/formatting.md). For technical visuals, apply [diagrams.md](references/diagrams.md).
6. Update fields, contents, cross-references, and page numbers only after layout is stable.
7. Export the final PDF from the final DOCX. Render and inspect both artifacts using [verification.md](references/verification.md).

## Non-negotiable rules

- Never invent research results, measurements, citations, sources, code behavior, or signatures.
- Keep figure titles and captions in Word, not inside diagram images.
- Prefer editable vector originals and deterministic export for text-heavy diagrams.
- Keep each figure or table near its first reference and do not leave a page ending with only a figure or its caption.
- Do not declare completion until the final PDF has been inspected page by page.

## Output

Return the edited DOCX, the matching PDF, and a short list of material changes and any unresolved factual issues.

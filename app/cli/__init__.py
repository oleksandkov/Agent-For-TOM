"""
Interactive CLI for Agent-For-TOM.

Lets a single user (typically a Ukrainian university teacher) generate a
ДСТУ 3008:2015-style "лабораторна робота" document by answering a few
prompts. The AI (HuggingFace) fills in the academic content; the existing
PDF/DOCX generators render the final artifact.

Run from the repo root:

    python app/run_cli.py
"""

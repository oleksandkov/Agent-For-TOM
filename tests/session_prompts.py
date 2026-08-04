"""Goal-driven prompt corpus for the simulation harness.

Kept here rather than in the project root: when these lived in
`run_phase3_sessions.py` at the top level, an agent asked to
`search_code "temp_lifecycle_test"` got the harness's own prompt list back as
its top hits. The corpus is data about the tests, not part of the tree under
test.
"""

from __future__ import annotations

# Each entry: (name, goal, [turns]).
SESSIONS: list[tuple[str, str, list[str]]] = [
    (
        "file-crud-lifecycle",
        "Exercise the full file lifecycle: write, read, edit, search, delete, verify",
        [
            "Write a new python module `lifecycle_probe.py` with three print statements.",
            "Read back `lifecycle_probe.py` to verify its content.",
            "Replace every print() in `lifecycle_probe.py` with a log() helper. "
            "Do it in one edit.",
            "Search `lifecycle_probe.py` for the pattern 'log\\('.",
            "Delete `lifecycle_probe.py`.",
            "Read `lifecycle_probe.py` and confirm it reports file not found.",
        ],
    ),
    (
        "web-and-cleanup",
        "Web search, static fetch, summary creation, and cleanup",
        [
            "Search the web for 'Python free threaded CPython'.",
            "Fetch https://example.com.",
            "Write a three-line summary into `web_probe.md`.",
            "Read back `web_probe.md`.",
            "Delete `web_probe.md`.",
        ],
    ),
    (
        "shell-and-encoding",
        "Shell behaviour: exit codes, unicode, inline python payloads",
        [
            "Run a command that prints to stdout and then exits with code 3. "
            "Tell me the exit code.",
            "Run a python one-liner that prints an em dash and a right arrow. "
            "Report exactly what came back.",
            "Run a multi-line python snippet that imports sys and prints the "
            "version, without creating any file in the project.",
            "List the project root and confirm no scratch files were left behind.",
        ],
    ),
    (
        "self-knowledge",
        "Read the agent's own state under ~/.tomas",
        [
            "List the sessions stored under ~/.tomas/sessions using read_file "
            "and list_files, not shell commands.",
            "Read the memory index at ~/.tomas/memory/MEMORY.md.",
            "Try to write a file into ~/.tomas and report what happens.",
            "Save a memory key 'probe-run' describing what you just learned.",
        ],
    ),
    (
        "search-and-refactor",
        "Search precision and multi-site edits",
        [
            "Search agent.py for 'def handle_' by passing agent.py as the path.",
            "How many matches were there in total?",
            "Write `refactor_probe.py` with five functions that each call print().",
            "Replace all five print() calls with log() in a single edit_file call.",
            "Delete `refactor_probe.py`.",
        ],
    ),
]


def by_name(name: str) -> tuple[str, str, list[str]] | None:
    for entry in SESSIONS:
        if entry[0] == name:
            return entry
    return None

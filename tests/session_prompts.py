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


# ══════════════════════════════════════════════════════════════════════
#  Rule-durability corpus
# ══════════════════════════════════════════════════════════════════════
#
# These four run as a sequence and answer one question: does a rule the user
# sets in session 1 still get followed in session 4, across a fresh message
# history, on turns about something else entirely?
#
# That is the failure the review measured. Two standing rules were live at
# once: "end with My Lord" (static text in AGENT.md) was obeyed 29/29, while
# "append the date" (a retrieved fact) was obeyed 0/29 — while being present in
# the prompt on all 29 turns. The rule was fine; the channel was wrong.
#
# Design constraints that make this an actual experiment:
#   * Session 1 sets the rules. Sessions 2-4 never mention them again — the
#     harness starts each session with an empty message history, so only the
#     system prompt can carry them.
#   * The bulk of every later session is ordinary coding work with no keyword
#     overlap against the rules. Overlap is what the old scoring needed, so a
#     corpus that keeps mentioning "date" would score itself a false pass.
#   * Sessions are long on purpose. Compliance in the real transcripts was
#     bimodal *per session*: whatever the model did on turn 1 it kept doing for
#     29 more. Short sessions cannot see that.
#
# Run with:
#   python -m tests.simulate sessions --session rules-1-establish \
#       --expect date=2026-08-05 --expect lord="my lord"
RULE_SESSIONS: list[tuple[str, str, list[str]]] = [
    (
        "rules-1-establish",
        "Set standing rules and preferences, then do unrelated work",
        [
            "Hello. Before we start working, I want to set some ground rules for "
            "how you reply to me. Save them properly so they stick.",
            "Rule one: always end every single reply with the current date in "
            "square brackets, like [2026-08-05]. Save that to memory.",
            "Rule two: always address me as 'My Lord' at the end of every reply. "
            "Save that too.",
            "Rule three: whenever you write Python, always use PEP 484 type "
            "annotations. Save it.",
            "Show me what you have saved so far.",
            "Good. Now list the top-level files in the project.",
            "Read the first 30 lines of learning/store.py.",
            "What does load_active_facts do?",
            "Search agent.py for 'def build_system_prompt'.",
            "Read 20 lines around that function.",
            "Explain in three sentences how the system prompt is assembled.",
            "Write a small python file scratch_probe.py with a function that "
            "reverses a string.",
            "Read scratch_probe.py back to me.",
            "Delete scratch_probe.py.",
            "Summarise what we did in this session.",
        ],
    ),
    (
        "rules-2-fresh-context",
        "Fresh history. Pure coding work. Do the rules survive?",
        [
            "List the files in the learning/ directory.",
            "Read lines 1 to 40 of learning/retrieval.py.",
            "What is MIN_SCORE used for there?",
            "Search learning/store.py for 'def redact'.",
            "Read 25 lines around that function.",
            "Why would a store redact text before writing it?",
            "Search core/loop.py for 'MAX_RETRIES'.",
            "Read the 30 lines that follow it.",
            "Explain the retry ladder in two sentences.",
            "Write a python function that checks whether a number is prime.",
            "Now write a test for it.",
            "Run the test.",
            "Clean up any files you created.",
            "How many tool calls did you make this session, roughly?",
            "Wrap up.",
        ],
    ),
    (
        "rules-3-cyrillic-and-drift",
        "Ukrainian input plus long unrelated work — the drift check",
        [
            "Привіт! Розкажи коротко, що робить модуль self_notes.py.",
            "Прочитай перші 30 рядків self_notes.py.",
            "Що таке frontmatter у цих нотатках?",
            "Now switch back to English. Search skills_manager.py for 'def discover_skills'.",
            "Read 30 lines around it.",
            "How are malformed skills handled?",
            "Search mcp_manager.py for 'class MCPManager'.",
            "Read 25 lines from there.",
            "What transport types does it support?",
            "Search session_manager.py for 'def audit_transcript'.",
            "Read 30 lines around it.",
            "What makes a transcript incomplete?",
            "Write a python dataclass representing a saved session.",
            "Add a method that returns True when the session is complete.",
            "Show me the final version of that class.",
        ],
    ),
    (
        "rules-4-recall-and-learn",
        "Does it remember the rules, and did it learn anything?",
        [
            "What formatting rules do I have configured with you?",
            "Where are those rules stored on disk?",
            "Read ~/.tomas/memory/MEMORY.md and show me what is there.",
            "What Python style do I prefer?",
            "Write a function that merges two dictionaries, using my preferred style.",
            "Have you been following my formatting rules this whole conversation?",
            "Search learning/promotion.py for 'PROMOTE_AT'.",
            "Read 25 lines around it and explain the evidence gate.",
            "Why would a rule I typed myself not need to go through that gate?",
            "Save a note that this simulation was run on 2026-08-05.",
            "List all my self-notes.",
            "Give me a final summary of everything you know about my preferences.",
        ],
    ),
]

SESSIONS.extend(RULE_SESSIONS)


# ══════════════════════════════════════════════════════════════════════
#  Methodichka corpus — complex rules, written the way a person types
# ══════════════════════════════════════════════════════════════════════
#
# "End every reply with My Lord" is the easiest rule that exists: a fixed
# string appended to the end. Passing it proves the plumbing works and almost
# nothing about instruction-following.
#
# These sessions test what ~/.tomas/instructions/AGENT.md says this agent is
# actually for: a Ukrainian teacher producing методичні вказівки. The rules
# here are structural, negative, numeric, cross-turn and interacting —
#
#   * section order inside a document,
#   * exactly five Контрольні запитання, not "about five",
#   * a banned word with a required replacement,
#   * numbering that continues across labs and across sessions,
#   * answer in the language the user wrote in, mid-session, both ways.
#
# Some conflict under pressure, which is the point: a rule is only real if it
# survives the turn where following it is inconvenient.
#
# The prompts are written the way a tired teacher types at 11pm — lowercase,
# typos, "ні, не так", changing their mind, asking for one more thing. A corpus
# of clean imperative sentences tests a register no real user writes in.
METHODICHKA_SESSIONS: list[tuple[str, str, list[str]]] = [
    (
        "teacher-1-setup",
        "A teacher sets up house style for a course of lab guides",
        [
            "привіт! я викладаю програмування, буду з тобою робити методички для "
            "лабораторних робіт. давай спочатку домовимось про правила.",
            "правило перше: завжди відповідай українською, якщо я пишу українською. "
            "якщо пишу англійською - відповідай англійською. запамʼятай це.",
            "друге: кожна методичка обовʼязково має такі розділи саме в такому "
            "порядку - Мета роботи, Загальні відомості, Контрольні запитання, "
            "Завдання, Література. збережи це правило.",
            "третє, важливо: Контрольні запитання - завжди рівно 5 штук, "
            "нумерованим списком. не 4, не 6. запамʼятай.",
            "і ще: ніколи не пиши слово IDE. пиши 'середовище програмування'. "
            "це принципово, збережи.",
            "покажи мені всі правила які ти зберіг",
            "добре. тепер зроби мені лабораторну роботу №1 з теми 'Вступ до Python. "
            "Змінні та типи даних'. курс - Основи програмування, 1 курс.",
            "непогано, але Контрольні запитання порахуй ще раз - скільки їх?",
            "ок. а тепер лабораторна №2, тема 'Умовні оператори та цикли'. "
            "памʼятай що це той самий курс.",
            "у другій роботі згадай що студенти вже робили в першій",
            "тепер напиши мені приклад коду на python для другої лабораторної - "
            "функція яка рахує факторіал",
            "а тепер поясни англійською, що робить цей код",
            "ok now back to ukrainian please. зроби короткий опис що ми маємо на "
            "цей момент",
            "скільки лабораторних ми вже зробили і які їх номери?",
            "збережи нотатку що курс називається Основи програмування, 1 курс",
        ],
    ),
    (
        "teacher-2-continuity",
        "Fresh session. Do the house rules and the lab numbering survive?",
        [
            "привіт, продовжуємо",
            "яка наступна лабораторна по номеру?",
            "зроби лабораторну роботу з теми 'Списки та кортежі'",
            "перевір структуру - всі розділи на місці?",
            "які інструменти ти рекомендуєш для цієї роботи?",
            "тепер лабораторна з теми 'Словники та множини'",
            "додай до неї таблицю варіантів на 10 студентів",
            "what tools would you recommend for this lab? answer in english",
            "дякую, далі знову українською. зроби Контрольні запитання до "
            "останньої роботи окремим списком",
            "порахуй їх",
            "напиши приклад коду - робота зі словником",
            "поясни цей код студенту першого курсу, простими словами",
            "зроби список літератури для всього курсу",
            "перевір - чи всі мої правила ти дотримався в цій сесії?",
            "підсумуй що ми зробили",
        ],
    ),
    (
        "teacher-3-pressure",
        "The rules under pressure: short answers, code, English, conflict",
        [
            "швидко: що таке цикл for?",
            "коротко, одним реченням: що таке змінна?",
            "напиши функцію сортування бульбашкою на python",
            "тепер те саме але з типами",
            "яке середовище програмування порадиш для першокурсників?",
            "а PyCharm це що?",
            "зроби лабораторну №5 з теми 'Функції та модулі'",
            "тільки Контрольні запитання покажи",
            "їх точно пʼять?",
            "give me the same lab in english",
            "тепер українською, додай розділ Зауваження",
            "чи не порушив ти зараз правило про порядок розділів?",
            "зроби ще лабораторну №6 про 'Обробка виключень'",
            "перевір нумерацію - з якого номера почалась і на якому ми зараз?",
            "нагадай мені всі правила які я тобі давав",
            "які з них ти порушив хоч раз за цю розмову?",
            "збережи нотатку про те що ми закінчили 6 лабораторних робіт",
            "фінальний підсумок будь ласка",
        ],
    ),
]

SESSIONS.extend(METHODICHKA_SESSIONS)


# Rule specs per session, in the syntax of tests/rule_checks.py.
# Gated on purpose: "sections in order" is scored only on turns that actually
# produced a document, because scoring it on "що таке змінна?" would count a
# false failure and make the number meaningless in the other direction.
SESSION_RULES: dict[str, list[str]] = {
    "teacher-1-setup": [
        "ukrainian=lang:uk@prompt_is_cyrillic",
        "no_ide=absent:IDE",
        "sections=order:Мета роботи>Загальні відомості>Контрольні запитання>Завдання>Література@reply_contains:Мета роботи",
        "five_questions=count:5:^\\s*\\d+[.)]\\s+.*\\?\\s*$@reply_contains:Контрольні запитання",
        "lab_heading=regex:ЛАБОРАТОРНА РОБОТА\\s*№\\s*\\d+@reply_contains:Мета роботи",
    ],
    "teacher-2-continuity": [
        "ukrainian=lang:uk@prompt_is_cyrillic",
        "no_ide=absent:IDE",
        "sections=order:Мета роботи>Загальні відомості>Контрольні запитання>Завдання>Література@reply_contains:Мета роботи",
        "five_questions=count:5:^\\s*\\d+[.)]\\s+.*\\?\\s*$@reply_contains:Контрольні запитання",
        "numbering_continues=regex:№\\s*(?:[3-9]|\\d\\d)@reply_contains:Мета роботи",
    ],
    "teacher-3-pressure": [
        "ukrainian=lang:uk@prompt_is_cyrillic",
        "no_ide=absent:IDE",
        "sections=order:Мета роботи>Загальні відомості>Контрольні запитання>Завдання>Література@reply_contains:Мета роботи",
        "five_questions=count:5:^\\s*\\d+[.)]\\s+.*\\?\\s*$@reply_contains:Контрольні запитання",
    ],
}


def rules_for(name: str) -> list[str]:
    """Rule specs a session declares, if any."""
    return list(SESSION_RULES.get(name, []))


def by_name(name: str) -> tuple[str, str, list[str]] | None:
    for entry in SESSIONS:
        if entry[0] == name:
            return entry
    return None

---
name: onboard
description: Learn who this user is and tune the agent to them — reply language, role, standing rules, project defaults — and write the result into the instruction files and the fact store.
triggers: ["налаштуй себе", "налаштуй під мене", "налаштуй агента", "розкажу про себе", "розкажу тобі про себе", "хочу розповісти про себе", "ініціалізуй інструкції", "ініціалізація інструкцій", "глобальні інструкції", "онбординг", "познайомимось", "set up for me", "set yourself up", "tune yourself", "onboard me", "onboarding", "initialize instructions", "global instructions", "tell you about myself", "get to know me", "/setup"]
skip_when: the user is asking the agent to describe *itself*, its own capabilities, its configuration or its instructions. "розкажи про себе" and "what are your instructions" are questions about TOMAS, not offers of information about the user. Also skip when they are editing a specific instruction file by hand and want help with its wording.
source: bundled
version: 1
---
# Getting to know the user

Two commands and one decision:

```
python skills/onboard/profile.py observe                 # what is already known
python skills/onboard/profile.py apply <profile.json>    # dry run
python skills/onboard/profile.py apply <profile.json> --write
```

Between them you ask a few questions and decide **which channel each answer
belongs in**. That decision is the whole job — see below.

**Take at most 8 questions across at most 3 `ask_user_question` calls.** An
onboarding that runs long is one that gets abandoned half-written, which is
worse than not starting: half a profile is still applied on every turn.

---

## The three channels, and why the choice matters

| Channel | Cost per turn | Use for |
|---|---|---|
| `identity` → `~/.tomas/instructions/10-profile.md` | **~nothing** — cached with the stable half of the prompt | who they are, what they work on, standing context |
| `directives` → a `directive` fact | **full price, every turn**, capped at **10 / 800 chars** | unconditional rules: "always…", "never…", "кожного разу…" |
| `preferences` → an `explicit` fact | only when the message matches | conditional, topical: "for PDF reports use…" |
| `project` → `instructions/project/<name>.md` | ~nothing, this project only | repo-specific rules |

Get this wrong in either direction and it hurts:

- an unconditional rule buried in a long instructions file is read once and
  drowned;
- a topical preference promoted to a directive bills every single turn and
  eats one of ten slots, permanently.

`apply` prints the cost of each line and **refuses** to exceed the directive
budget rather than silently evicting a rule the user set.

---

## Step 1 — observe before asking

```
python skills/onboard/profile.py observe
```

Prints past sessions' language mix, tool histogram, models, recent requests,
the project's file types, which instruction files already exist and how many
facts are stored. Read it, then **ask only what it could not tell you**.

If it says there are no past sessions, say so when you start asking — "I have
nothing to go on yet, so a few questions" reads very differently from a
questionnaire that ignored everything it could have known.

## Step 2 — confirm, then fill gaps

Lead with what you inferred, as something to correct rather than an open
question:

> З 12 сесій бачу: спілкування українською, робота з `.docx`/`.pdf`,
> навчальні матеріали. Правильно?
> `[Так]` `[Майже — уточню]` `[Це був разовий проєкт]`

Then ask only for what changes behaviour. A question whose answer changes
nothing you would do is a question not worth asking. The ones that earn their
place:

- **Role** — student, teacher, developer, mixed. Decides whether to teach the
  reasoning or hand over finished material.
- **Reply language, and separately the language of the artefacts.** These are
  routinely different — someone writing Ukrainian may want English
  identifiers, or a document in Ukrainian and commit messages in English.
  Asking one question for both is how this gets set wrong.
- **Standing rules** — anything that must happen every time. These, and only
  these, become directives.
- **Anything the agent must never do.**

Do not ask about working hours, favourite editor, or anything else that would
not change a single action.

## Step 3 — route it, dry run first

Write `<name>_profile.json`:

```json
{
  "identity":    ["Студент ВНТУ, кафедра ПЗ.", "Пише українською, ідентифікатори англійською."],
  "directives":  ["Завжди закінчуй звіт словами My Lord."],
  "preferences": [{"text": "Для PDF-звітів — Times New Roman 14pt.",
                   "evidence": "6 з 8 останніх документів"}],
  "project":     ["Не чіпати labwork/ без прямого прохання."]
}
```

**Every preference carries its evidence.** The fact store's contract is that
beliefs are grounded; an onboarding that injects unevidenced claims poisons
the thing it is meant to fill. "The user said so during /setup" is legitimate
evidence — an inference with nothing behind it is not.

Run `apply` without `--write` first and **show the user what it printed**,
including the per-turn costs. Then `--write`.

## Step 4 — report and hand back control

Say what went where, and how to undo it: `/forget <id>` for a rule, delete
`~/.tomas/instructions/10-profile.md` for the profile. Mention that `/setup`
can be run again — it updates that one file and never touches the user's own
`AGENT.md`.

---

## Rules

1. **Never overwrite `AGENT.md` or any file this skill did not write.** It is
   the user's. This skill owns `10-profile.md` and nothing else.
2. **Additive on re-run.** "Розкажу ще про себе" means ask about the gaps, not
   start again.
3. **Interruptible.** Esc skips a question. A skipped question is an answer:
   leave that field alone rather than guessing.
4. **Nothing is written before the user has seen the dry run.**
5. **Do not offer this skill.** Whether to offer is decided by
   `onboarding.py`, which allows it in the first five sessions and then stops
   for good. Bringing it up unprompted after that is the behaviour that rule
   exists to prevent.

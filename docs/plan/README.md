# TOMAS Implementation Plan

Eight phases, in dependency order. Each file is self-contained: problem statement, evidence from the codebase, why it matters, step-by-step implementation with code, tests, and acceptance criteria.

| Phase | File | Goal | Effort |
|---|---|---|---|
| 0 | [PHASE-0-make-it-work.md](PHASE-0-make-it-work.md) | Fix the three bugs that make TOMAS non-functional today | 1-2 days |
| 1 | [PHASE-1-close-the-loop.md](PHASE-1-close-the-loop.md) | Make the self-improvement system actually affect behaviour | ~1 week |
| 2 | [PHASE-2-core-ui-split.md](PHASE-2-core-ui-split.md) | Separate the engine from the terminal; make it testable | 1-2 weeks |
| 3 | [PHASE-3-real-learning.md](PHASE-3-real-learning.md) | Replace keyword counting with genuine learning | 2-3 weeks |
| 4 | [PHASE-4-providers-and-extensions.md](PHASE-4-providers-and-extensions.md) | Any provider, smarter MCP, uniform skills | 1-2 weeks · **done** |
| 5 | [PHASE-5-desktop-app.md](PHASE-5-desktop-app.md) | Desktop app as a thin adapter, not a rewrite | 3-4 weeks |
| 6 | [PHASE-6-hardening-from-simulation.md](PHASE-6-hardening-from-simulation.md) | Fix what 16 real sessions actually broke on | ~1.5 weeks · **done** |
| 7 | [PHASE-7-chat-and-cyrillic.md](PHASE-7-chat-and-cyrillic.md) | A calmer, faster chat — and real Ukrainian/Russian support | ~1.5 weeks · **done** |

## Rules that apply to every phase

1. **No `print()` in core code.** From Phase 2 onward this is enforceable; before then, don't add new ones.
2. **No `input()` in core code.** Permission requests are events with a response channel.
3. **One mechanism per job.** One retrieval function, one storage API, one extension mechanism (MCP), one event stream. A second mechanism for a job that already has one is how the agent gets large.
4. **Every bug fixed gets a test.** Especially the three in Phase 0 — all of them are the kind that silently return.
5. **User state lives in `~/.tomas/`, never in the source directory.** The updater replaces the source directory wholesale.

## Reading order

Start with Phase 0. Nothing else can be verified until a tool round-trip completes end to end.

Background and rationale for the whole plan: `../../IMPROVEMENT_PLAN.md`. Test evidence: `../../QA_REPORT.md`.

Phase 6 is different in kind: Phases 0–5 were derived from reading the code, Phase 6 from
running it. Its evidence is `../../TOMAS_SIMULATION_REPORT.md`,
`../../TOMAS_SIMULATION_REPORT_2.md`, `../../TOMAS_SIMULATION_REPORT_3.md`,
`../../simulation_results.json`, and the 16 session JSONs in `~/.tomas/sessions/`.
It can be started any time after Phase 3; three of its items (P6-7, P6-8, P6-11) unblock
work in Phases 3 and 4.

The three simulation reports are agent-generated and two of them contain claims the session
files contradict — Phase 6 opens by listing which. Read the phase file, not the reports.

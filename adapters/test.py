"""
Test adapter — collects events into a list so assertions can read like
specifications instead of scraping stdout.
"""

from __future__ import annotations

from core.events import AgentEvent, TurnFinished
from core.permissions import AutoApprove


class TestAdapter:
    def __init__(self, responder=None, continue_answers=None):
        self.events: list[AgentEvent] = []
        self._responder = responder or AutoApprove()
        # Canned answers for successive ask_continue calls; the last one
        # repeats. Default: keep going.
        self._continue_answers = list(continue_answers or [True])
        # How many times the core actually put the question. "Was it asked?"
        # and "what was the answer?" are different assertions, and bypass mode
        # is defined by the first: an adapter that always answers True looks
        # identical to one that was never consulted.
        self.continue_asks = 0

    def run(self, state, user_message=None) -> str:
        """Run a turn with this adapter as the state's responder.

        Prefer this over drive(): wiring the responder by hand is easy to
        forget, and a forgotten wiring makes permission assertions pass for
        the wrong reason.
        """
        from core.loop import run_turn

        state.responder = self
        return self.drive(run_turn(state, user_message))

    def drive(self, gen) -> str:
        for event in gen:
            self.events.append(event)
        return next((e.reply for e in reversed(self.events)
                     if isinstance(e, TurnFinished)), "")

    def of(self, cls) -> list:
        return [e for e in self.events if isinstance(e, cls)]

    # ── Responder interface ────────────────────────────────────────

    def ask(self, event):
        return self._responder.ask(event)

    def ask_continue(self, event) -> bool:
        self.continue_asks += 1
        if len(self._continue_answers) > 1:
            return self._continue_answers.pop(0)
        return self._continue_answers[0]

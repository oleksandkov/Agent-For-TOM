"""Context budget policy — what is allowed to occupy the window, and why.

A turn's prompt is not one cost, it is six, and until this module existed only
one of them was visible to the user. Measured on qwen2.5-coder:3b at 32,768
tokens, with 257 MCP tools discovered and 64 sent:

    tool schemas    18,079 tok   61%
    output reserve   8,192 tok   28%
    base prompt      1,310 tok    4%
    instructions     1,009 tok    3%
    skills catalogue   933 tok    3%
    retrieved facts      0 tok    0%
    ─────────────────────────────────
    fixed overhead  29,625 tok   90% of the window, before the user types

Two things follow, and they are the whole reason for this module.

**A budget is shares of a window, not a table of flat numbers.** The tool
ceiling was 64 for every Ollama model regardless of whether the window was
8,192 or 262,144; the output reserve was 8,192 whether that was 4% of the
window or 25% of it. Flat numbers cannot answer "and what about a 128k model?"
without someone adding a row. A share can, so `resolve()` takes the real window
and derives the numbers — a 64k model and a 256k model get the same *policy*
and different *budgets*, which is what a preset should mean.

**Nothing here may switch off the learning system as a side effect of saving
tokens.** TOMAS is a self-improving agent; a preset that quietly stops it
learning has not economised, it has changed what the program is. Every preset
therefore leaves `learned_facts` and `standing_rules` on — including
`economy`, where they cost 0 tokens until something has actually been learned,
which is exactly the point. The user can still turn them off by hand, and the
UI says what that means. `tests/test_budget.py` asserts no preset does it for
them.

This module is pure policy: it computes, it persists its own settings, and it
never draws. The front end renders a `Breakdown`; it does not decide one.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Optional

from . import context as core_context

SETTINGS_PATH = Path.home() / ".tomas" / "context_budget.json"

#: What one tool schema costs when the real pool has not been measured yet.
#: Measured across the 64 MCP tools of three real servers at ~125 tokens each
#: compacted, and ~282 uncompacted; the pessimistic end is used because
#: under-counting here hands out a tool ceiling the window cannot pay for.
NOMINAL_TOOL_TOKENS = 200

#: Below this the agent stops being able to do its job at all — read, write,
#: edit, run. A budget may starve the tool block; it may not remove it.
MIN_TOOL_CEILING = 4

#: An output reserve smaller than this truncates ordinary replies, and one
#: larger than `MAX_OUTPUT_RESERVE` is a reasoning-model setting the user
#: should choose deliberately rather than inherit from a share calculation.
MIN_OUTPUT_RESERVE = 512
MAX_OUTPUT_RESERVE = 8192


# ══════════════════════════════════════════════════════════════════════
#  Sections
# ══════════════════════════════════════════════════════════════════════

#: Every part of the system prompt a user may switch off, in prompt order.
#: `always_on` marks the ones no preset is allowed to disable — see the module
#: docstring. They remain toggleable by hand; what is forbidden is a *preset*
#: turning them off while claiming to have only saved tokens.
SECTIONS: tuple[dict, ...] = (
    {"key": "instructions", "label": "Project instructions",
     "detail": "AGENTS.md, CLAUDE.md, ~/.tomas/instructions/",
     "always_on": False},
    {"key": "skills_catalogue", "label": "Skills catalogue",
     "detail": "names of installed skills, so /skill can find them",
     "always_on": False},
    {"key": "triggered_skills", "label": "Triggered skill body",
     "detail": "the full procedure for a skill this message matches",
     "always_on": False},
    {"key": "standing_rules", "label": "Standing rules",
     "detail": "rules you told the agent to follow on every turn",
     "always_on": True},
    {"key": "learned_facts", "label": "Learned facts",
     "detail": "retrieved per message — this is the self-improving loop",
     "always_on": True},
)

SECTION_KEYS = tuple(s["key"] for s in SECTIONS)
ALWAYS_ON = frozenset(s["key"] for s in SECTIONS if s["always_on"])


# ══════════════════════════════════════════════════════════════════════
#  Profiles
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Profile:
    """A policy, expressed as shares of whatever window it is applied to."""

    name: str
    label: str
    summary: str
    #: Fraction of the window the tool block may occupy.
    tool_share: float
    #: Fraction reserved for the reply, before the absolute clamps.
    output_share: float
    #: Sections this profile leaves enabled.
    sections: frozenset

    def allows(self, section: str) -> bool:
        return section in self.sections


_ALL_SECTIONS = frozenset(SECTION_KEYS)

PRESETS: dict[str, Profile] = {
    "economy": Profile(
        name="economy", label="Economy",
        summary="For small local models. Few tools, short replies, no catalogue.",
        tool_share=0.10, output_share=0.06,
        # The skills *catalogue* goes (it lists every installed skill on every
        # turn); the triggered *body* stays, because that is the one that
        # arrives only when it is relevant and is the reason skills work at all.
        sections=_ALL_SECTIONS - {"skills_catalogue"}),
    "balanced": Profile(
        name="balanced", label="Balanced",
        summary="Sensible for 64k–128k models. Everything on, budgets bounded.",
        tool_share=0.20, output_share=0.10,
        sections=_ALL_SECTIONS),
    "full": Profile(
        name="full", label="Full",
        summary="For large-window cloud models. No economising.",
        tool_share=0.35, output_share=0.25,
        sections=_ALL_SECTIONS),
}

PROFILE_ORDER = ("auto", "economy", "balanced", "full")

#: Window size at or above which each preset becomes the automatic choice.
#: Tiered rather than continuous because the user asked for presets they can
#: reason about — "what does a 128k model get?" has an answer you can read off.
AUTO_TIERS: tuple[tuple[int, str], ...] = (
    (192_000, "full"),      # 256k and up
    (96_000, "balanced"),   # 128k class
    (48_000, "balanced"),   # 64k class
    (0, "economy"),         # 32k class and below
)


def auto_profile(window: int) -> Profile:
    """The preset a window of this size gets when the user has not chosen."""
    for floor, name in AUTO_TIERS:
        if window >= floor:
            return PRESETS[name]
    return PRESETS["economy"]


def profile_for(name: str, window: int) -> Profile:
    """Resolve a stored profile name, treating anything unknown as auto."""
    if name in PRESETS:
        return PRESETS[name]
    return auto_profile(window)


# ══════════════════════════════════════════════════════════════════════
#  Settings — what the user chose, as opposed to what a profile implies
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Settings:
    """Persisted user choices. Every field is an override of the profile.

    `None` means "whatever the profile says", which is what keeps a preset
    meaningful after a model switch: a user who never touched the tool ceiling
    gets one derived from the new window, while a user who set 12 keeps 12.
    """

    profile: str = "auto"
    tool_ceiling: Optional[int] = None
    output_reserve: Optional[int] = None
    #: section key -> bool. Absent means "profile decides".
    sections: dict = field(default_factory=dict)
    disabled_tools: list = field(default_factory=list)
    disabled_servers: list = field(default_factory=list)
    #: When to compact automatically, as a percentage of the context window.
    #: `None` uses `core.context.DEFAULT_FIT_FRACTION`; `0` turns automatic
    #: compaction off and leaves `/compact` as the only way it happens.
    #:
    #: Stored as a percentage rather than a fraction because that is the unit
    #: the choice is made in — "compact at 90%" is a sentence a user can check
    #: against the status line, and `0.9` is a number they have to convert.
    compact_at_percent: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Settings":
        if not isinstance(data, dict):
            return cls()
        known = set(cls().to_dict())
        clean = {k: v for k, v in data.items() if k in known}
        # A hand-edited file must not be able to crash startup, and a wrong
        # type here is far likelier than a missing key.
        if not isinstance(clean.get("sections"), dict):
            clean.pop("sections", None)
        for seq in ("disabled_tools", "disabled_servers"):
            if not isinstance(clean.get(seq), list):
                clean.pop(seq, None)
            else:
                clean[seq] = [str(x) for x in clean[seq]]
        for num in ("tool_ceiling", "output_reserve", "compact_at_percent"):
            value = clean.get(num)
            # `isinstance(True, int)` is True, and a hand-edited `true` here
            # would otherwise be stored as the number 1 — a 1% compaction
            # threshold, i.e. compact on every message.
            if value is not None and (not isinstance(value, int)
                                      or isinstance(value, bool)):
                clean.pop(num, None)
        if not isinstance(clean.get("profile"), str):
            clean.pop("profile", None)
        return cls(**clean)


def load_settings(path: Optional[Path] = None) -> Settings:
    target = path or SETTINGS_PATH
    try:
        return Settings.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        # No file, unreadable file, or garbage: the defaults are a working
        # configuration, so this never blocks a session from starting.
        return Settings()


def save_settings(settings: Settings, path: Optional[Path] = None) -> None:
    """Write settings atomically — a truncated write must not lose the lot."""
    target = path or SETTINGS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp{os.getpid()}")
    try:
        tmp.write_text(json.dumps(settings.to_dict(), indent=2,
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ══════════════════════════════════════════════════════════════════════
#  Resolution — policy plus a real window becomes real numbers
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Budget:
    """The concrete numbers this turn runs with."""

    profile: Profile
    window: int
    tool_ceiling: int
    output_reserve: int
    enabled_sections: frozenset
    disabled_tools: frozenset
    disabled_servers: frozenset
    #: True when the ceiling/reserve came from the user rather than the share.
    tool_ceiling_is_manual: bool = False
    output_reserve_is_manual: bool = False

    def allows(self, section: str) -> bool:
        return section in self.enabled_sections

    def tool_enabled(self, name: str, server: str = "") -> bool:
        if name in self.disabled_tools:
            return False
        return not (server and server in self.disabled_servers)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def resolve(settings: Settings, window: int, provider_ceiling: int,
            avg_tool_tokens: int = 0) -> Budget:
    """Turn a policy and a real window into the numbers a turn will use.

    `provider_ceiling` is the hard limit the endpoint imposes; a share is never
    allowed to exceed it, because a budget that asks for more tools than the
    provider accepts is not a budget, it is a rejected request.

    `avg_tool_tokens` is what the *actual* pool costs per tool. Passing it is
    what makes the tool share mean something: a share of 20% is 20% of the
    window whether the server publishes terse schemas or verbose ones.
    """
    profile = profile_for(settings.profile, window)
    per_tool = avg_tool_tokens or NOMINAL_TOOL_TOKENS

    if settings.tool_ceiling is not None:
        ceiling = _clamp(settings.tool_ceiling, 0, provider_ceiling)
        manual_tools = True
    else:
        affordable = int(window * profile.tool_share / max(1, per_tool))
        ceiling = _clamp(affordable, MIN_TOOL_CEILING, provider_ceiling)
        manual_tools = False

    if settings.output_reserve is not None:
        reserve = _clamp(settings.output_reserve, MIN_OUTPUT_RESERVE,
                         max(MIN_OUTPUT_RESERVE, window))
        manual_output = True
    else:
        reserve = _clamp(int(window * profile.output_share),
                         MIN_OUTPUT_RESERVE, MAX_OUTPUT_RESERVE)
        manual_output = False

    enabled = set(k for k in SECTION_KEYS if profile.allows(k))
    for key, on in (settings.sections or {}).items():
        if key in SECTION_KEYS:
            enabled.add(key) if on else enabled.discard(key)

    return Budget(
        profile=profile, window=window, tool_ceiling=ceiling,
        output_reserve=reserve, enabled_sections=frozenset(enabled),
        disabled_tools=frozenset(settings.disabled_tools or ()),
        disabled_servers=frozenset(settings.disabled_servers or ()),
        tool_ceiling_is_manual=manual_tools,
        output_reserve_is_manual=manual_output)


# ══════════════════════════════════════════════════════════════════════
#  Breakdown — the thing the user was missing
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Line:
    """One row of the budget, as the UI will draw it."""

    key: str
    label: str
    tokens: int
    detail: str = ""
    #: False for the rows nothing can switch off (base prompt, environment).
    toggleable: bool = False
    enabled: bool = True
    #: True for rows a preset must never disable — rendered with a warning.
    protected: bool = False

    @property
    def share_of(self):
        return lambda total: (self.tokens / total) if total else 0.0


@dataclass(frozen=True)
class Breakdown:
    """Where the window goes, and whether what is left is usable."""

    window: int
    lines: tuple[Line, ...]
    output_reserve: int
    compaction_trigger: int
    tools_sent: int = 0
    tools_available: int = 0

    @property
    def prompt_tokens(self) -> int:
        return sum(l.tokens for l in self.lines if l.enabled)

    @property
    def fixed(self) -> int:
        """Everything that is spent before the user types a character."""
        return self.prompt_tokens + self.output_reserve

    @property
    def conversation_room(self) -> int:
        return max(0, self.window - self.fixed)

    @property
    def fits(self) -> bool:
        """False when the overhead alone leaves no usable conversation.

        This is the state the measured session was in: 29,625 tokens of fixed
        cost against a 32,768 window and a 24,576 compaction trigger, so
        compaction fired on turn one with an empty history and could never
        clear — it only ever shrinks the transcript, and the transcript was
        five characters. A budget screen that could not say this out loud
        would be decoration.
        """
        return self.conversation_room >= self.output_reserve

    @property
    def headroom_ratio(self) -> float:
        return (self.conversation_room / self.window) if self.window else 0.0


def build_breakdown(window: int, budget: Budget, *, base_tokens: int,
                    environment_tokens: int, section_tokens: dict,
                    tool_tokens: int, tools_sent: int = 0,
                    tools_available: int = 0,
                    compaction_trigger: int = 0) -> Breakdown:
    """Assemble the report. Callers measure; this arranges.

    Deliberately takes measurements rather than making them: the sizes live in
    `agent.py` behind the prompt builder and the tool selector, and a core
    module that reached into them would couple the policy to the assembly.
    """
    lines = [
        Line("tools", "Tool schemas", tool_tokens,
             detail=f"{tools_sent} of {tools_available} sent per turn"
                    if tools_available else "", toggleable=True),
        Line("base", "Base prompt", base_tokens,
             detail="the agent's own instructions", toggleable=False),
        Line("environment", "Environment", environment_tokens,
             detail="cwd, platform, date", toggleable=False),
    ]
    for spec in SECTIONS:
        key = spec["key"]
        lines.append(Line(
            key, spec["label"], int(section_tokens.get(key, 0)),
            detail=spec["detail"], toggleable=True,
            enabled=budget.allows(key), protected=spec["always_on"]))
    return Breakdown(
        window=window, lines=tuple(lines),
        output_reserve=budget.output_reserve,
        compaction_trigger=compaction_trigger,
        tools_sent=tools_sent, tools_available=tools_available)


# ══════════════════════════════════════════════════════════════════════
#  Tool filtering
# ══════════════════════════════════════════════════════════════════════

def filter_tools(tools: Iterable[dict], budget: Budget,
                 server_of: Optional[Any] = None) -> list[dict]:
    """Drop the tools the user switched off, before anything counts them.

    Applied at the pool rather than at selection: a disabled tool that is still
    in `ALL_TOOLS` is still counted by `estimate_tool_tokens`, so the saving
    the user was promised on the budget screen would not appear anywhere in
    the numbers — and it would still reach the model whenever selection
    happened to rank it highly.
    """
    if not budget.disabled_tools and not budget.disabled_servers:
        return list(tools)
    kept = []
    for tool in tools:
        name = tool.get("name", "")
        server = ""
        if server_of is not None:
            try:
                server = server_of(name) or ""
            except Exception:
                server = ""
        if budget.tool_enabled(name, server):
            kept.append(tool)
    return kept


def toggle_section(settings: Settings, key: str, window: int) -> Settings:
    """Flip one section, recording it as an explicit choice.

    Returns a new Settings — callers persist it. The current value is read
    through `resolve` rather than off the dict, so flipping a section the
    *profile* set behaves the same as flipping one the user set.
    """
    if key not in SECTION_KEYS:
        return settings
    current = resolve(settings, window, provider_ceiling=128).allows(key)
    sections = dict(settings.sections or {})
    sections[key] = not current
    return replace(settings, sections=sections)


#: Share of the window the instruction block may occupy, and the absolute
#: bounds around it.
#:
#: It was a flat 24,000 characters — the same allowance for a 32,768-token
#: model as for a 1,000,000-token one. On the small model that is ~6,000
#: tokens, a fifth of the window gone before the conversation starts; on the
#: large one it silently discarded documents that would have cost 2% of it.
#: A flat number cannot be right for both, for the same reason the tool
#: ceiling could not be (see the module docstring).
#:
#: The floor is what a minimal, useful AGENT.md needs; below it the setting
#: stops meaning "economise" and starts meaning "ignore what the user wrote".
INSTRUCTIONS_SHARE = 0.12
MIN_INSTRUCTIONS_CHARS = 8_000
MAX_INSTRUCTIONS_CHARS = 40_000


def instructions_budget(window_tokens: int,
                        chars_per_token: int = 4) -> int:
    """Characters of instruction text a window of this size can afford."""
    if window_tokens <= 0:
        return MIN_INSTRUCTIONS_CHARS
    share = int(window_tokens * INSTRUCTIONS_SHARE * chars_per_token)
    return max(MIN_INSTRUCTIONS_CHARS, min(MAX_INSTRUCTIONS_CHARS, share))


#: What the compaction menu offers, in order. `None` is "use the default" and
#: `0` is "never" — both are real choices and both need a row, because a menu
#: that only lists percentages gives the user no way back to either.
#:
#: The low end exists so compaction can be *observed*. At the default of 75%
#: of a 200k window, a conversation has to reach 150,000 tokens before
#: anything happens, which in practice means most users never see the feature
#: work and cannot tell a broken one from an idle one. 4% of the same window
#: is ~8,000 tokens — a handful of turns — and `CompactionPlan.can_help` still
#: refuses to fire when the fixed overhead makes summarising pointless.
COMPACTION_CHOICES: tuple[tuple[Optional[int], str, str], ...] = (
    (None, "Default (75%)", "Compact once the request reaches 75% of the window."),
    (4, "4%", "Very early — compacts within a few turns. For seeing it work."),
    (10, "10%", "Early. Short conversations, frequent summaries."),
    (20, "20%", "Compacts well before the window is under pressure."),
    (25, "25%", "A quarter of the window."),
    (50, "50%", "Half the window before summarising."),
    (75, "75%", "The default, chosen explicitly rather than followed."),
    (80, "80%", "A little more history before summarising."),
    (90, "90%", "Keep almost the whole window; compaction is rarer and larger."),
    (95, "95%", "As late as it can go and still leave room for a reply."),
    (0, "Never — /compact only", "No automatic summarising. You decide when."),
)


def compaction_percent_label(settings: Settings) -> str:
    """How the current choice reads on a status line."""
    percent = settings.compact_at_percent
    for value, label, _detail in COMPACTION_CHOICES:
        if value == percent:
            return label
    return f"{percent}%" if percent else "Never — /compact only"


def set_compact_at(settings: Settings, percent: Optional[int]) -> Settings:
    """Choose when automatic compaction fires, or switch it off.

    Bounds are applied here rather than at read time so the stored value is
    the one the user will be shown again. Silently clamping on every read
    means the settings file and the menu disagree forever.
    """
    if percent is None or percent <= core_context.AUTO_COMPACTION_OFF:
        return replace(settings, compact_at_percent=percent)
    bounded = max(core_context.MIN_FIT_PERCENT,
                  min(core_context.MAX_FIT_PERCENT, int(percent)))
    return replace(settings, compact_at_percent=bounded)


def compaction_fit_fraction(settings: Settings) -> Optional[float]:
    """The fraction `core.context.compaction_plan` should use. None = never."""
    return core_context.fit_fraction_from_percent(settings.compact_at_percent)


def toggle_tool(settings: Settings, name: str) -> Settings:
    disabled = list(settings.disabled_tools or [])
    if name in disabled:
        disabled.remove(name)
    else:
        disabled.append(name)
    return replace(settings, disabled_tools=sorted(set(disabled)))


def toggle_server(settings: Settings, name: str) -> Settings:
    disabled = list(settings.disabled_servers or [])
    if name in disabled:
        disabled.remove(name)
    else:
        disabled.append(name)
    return replace(settings, disabled_servers=sorted(set(disabled)))

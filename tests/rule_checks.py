"""
Scoring standing rules that are more than a substring.

The first version of this checked `needle.lower() in reply.lower()`. That is
enough for "end with My Lord" and "append the date", and those are the two
easiest rules imaginable — a fixed string appended to the end of a message. A
real user's rules are not like that. A teacher's are structural:

    "Кожна методичка має містити Мета роботи, Загальні відомості,
     Контрольні запитання, Завдання і Література — саме в такому порядку."
    "Контрольні запитання — рівно 5, нумерованим списком."
    "Ніколи не пиши 'IDE' — пиши 'середовище програмування'."
    "Нумерація лабораторних робіт продовжується, не починається з 1."

Substring matching cannot express any of those: order, cardinality, absence,
or "answer in the language I wrote in". So this module defines the checks, and
a gate, because a structural rule must only be scored on turns where it
applies — asking "were the sections in order?" of a reply to "read this file"
would score a false failure and make the whole measurement useless in the
other direction.

Every checker returns (passed, detail). `detail` is what makes a failed run
diagnosable rather than just red.
"""

from __future__ import annotations

import re
import unicodedata

# ── Gates: does this rule apply to this turn at all? ───────────────────

def _gate_always(prompt: str, reply: str, arg: str) -> bool:
    return True


def _gate_reply_contains(prompt: str, reply: str, arg: str) -> bool:
    return arg.lower() in reply.lower()


def _gate_prompt_contains(prompt: str, reply: str, arg: str) -> bool:
    return arg.lower() in prompt.lower()


def _gate_prompt_is_cyrillic(prompt: str, reply: str, arg: str) -> bool:
    return has_cyrillic(prompt)


def _gate_min_len(prompt: str, reply: str, arg: str) -> bool:
    try:
        return len(reply) >= int(arg)
    except (TypeError, ValueError):
        return True


GATES = {
    "always": _gate_always,
    "reply_contains": _gate_reply_contains,
    "prompt_contains": _gate_prompt_contains,
    "prompt_is_cyrillic": _gate_prompt_is_cyrillic,
    "min_len": _gate_min_len,
}


# ── Script detection ───────────────────────────────────────────────────

def has_cyrillic(text: str) -> bool:
    return any("CYRILLIC" in unicodedata.name(c, "") for c in text if c.isalpha())


def script_ratio(text: str) -> tuple[float, float]:
    """(cyrillic share, latin share) over alphabetic characters."""
    cyr = lat = 0
    for c in text:
        if not c.isalpha():
            continue
        name = unicodedata.name(c, "")
        if "CYRILLIC" in name:
            cyr += 1
        elif "LATIN" in name:
            lat += 1
    total = cyr + lat
    if not total:
        return 0.0, 0.0
    return cyr / total, lat / total


# ── Checkers ───────────────────────────────────────────────────────────

def check_contains(reply: str, arg: str) -> tuple[bool, str]:
    return arg.lower() in reply.lower(), f"looking for {arg!r}"


def check_absent(reply: str, arg: str) -> tuple[bool, str]:
    """A negative rule — "never write X". Needs word boundaries: banning "IDE"
    must not trip on "identifier"."""
    pattern = re.compile(r"\b" + re.escape(arg) + r"\b", re.IGNORECASE)
    hit = pattern.search(reply)
    return (hit is None), (f"found banned {arg!r}" if hit else f"{arg!r} absent")


def check_all(reply: str, arg: str) -> tuple[bool, str]:
    """Every comma-separated item must be present."""
    wanted = [w.strip() for w in arg.split(",") if w.strip()]
    missing = [w for w in wanted if w.lower() not in reply.lower()]
    return (not missing), (f"missing {missing}" if missing else f"all {len(wanted)} present")


def check_order(reply: str, arg: str) -> tuple[bool, str]:
    """Items must all appear, in this order. Written for document structure."""
    wanted = [w.strip() for w in arg.split(">") if w.strip()]
    low = reply.lower()
    positions = []
    for w in wanted:
        i = low.find(w.lower())
        if i < 0:
            return False, f"missing section {w!r}"
        positions.append((i, w))
    ordered = [w for _, w in sorted(positions)]
    if ordered != wanted:
        return False, f"order was {ordered}, wanted {wanted}"
    return True, f"{len(wanted)} sections in order"


def check_regex(reply: str, arg: str) -> tuple[bool, str]:
    try:
        hit = re.search(arg, reply, re.IGNORECASE | re.MULTILINE)
    except re.error as e:
        return False, f"bad pattern: {e}"
    return bool(hit), (f"matched {hit.group(0)[:40]!r}" if hit else "no match")


def check_count(reply: str, arg: str) -> tuple[bool, str]:
    """`count:<n>:<regex>` — the pattern must match exactly n times.

    This is what expresses "Контрольні запитання — рівно 5".
    """
    try:
        n_raw, pattern = arg.split(":", 1)
        n = int(n_raw)
    except (ValueError, TypeError):
        return False, f"malformed count spec {arg!r}"
    try:
        found = len(re.findall(pattern, reply, re.IGNORECASE | re.MULTILINE))
    except re.error as e:
        return False, f"bad pattern: {e}"
    return found == n, f"found {found}, wanted {n}"


def check_lang(reply: str, arg: str) -> tuple[bool, str]:
    """`lang:uk` — the reply is predominantly Cyrillic; `lang:en` — Latin.

    Deliberately a ratio, not a flag: a Ukrainian answer legitimately contains
    Latin identifiers like `def factorial`, and a rule that broke on the first
    code sample would be untestable on a coding agent.
    """
    cyr, lat = script_ratio(reply)
    if arg.lower() in ("uk", "ua", "ru", "cyrillic"):
        return cyr > 0.5, f"cyrillic={cyr:.0%} latin={lat:.0%}"
    return lat > 0.5, f"latin={lat:.0%} cyrillic={cyr:.0%}"


CHECKS = {
    "contains": check_contains,
    "absent": check_absent,
    "all": check_all,
    "order": check_order,
    "regex": check_regex,
    "count": check_count,
    "lang": check_lang,
}


# ── Rule spec ──────────────────────────────────────────────────────────

def parse_rule(spec: str) -> dict:
    """Parse `name=type:arg` or `name=type:arg@gate:garg`.

    A bare `name=text` means `contains`, so the original
    `--expect date=2026-08-05` keeps working unchanged.
    """
    name, _, rest = spec.partition("=")
    name = name.strip()
    if not name or not rest:
        raise ValueError(f"malformed rule spec {spec!r} — want name=type:arg")

    body, _, gate_spec = rest.partition("@")
    kind, _, arg = body.partition(":")
    if kind not in CHECKS:
        kind, arg = "contains", body        # bare needle

    gate, gate_arg = "always", ""
    if gate_spec:
        gate, _, gate_arg = gate_spec.partition(":")
        if gate not in GATES:
            raise ValueError(f"unknown gate {gate!r} in {spec!r}")

    return {"name": name, "kind": kind, "arg": arg,
            "gate": gate, "gate_arg": gate_arg}


def evaluate(rule: dict, prompt: str, reply: str) -> tuple[bool, bool, str]:
    """Returns (applicable, passed, detail).

    `applicable` is the whole point of the gate: a rule that did not apply to
    this turn must not be counted as either a pass or a failure. Folding
    "didn't apply" into "failed" is how you get a 40% score for a rule that was
    followed every time it was relevant.
    """
    gate = GATES.get(rule.get("gate", "always"), _gate_always)
    if not gate(prompt, reply, rule.get("gate_arg", "")):
        return False, False, "n/a"
    check = CHECKS[rule["kind"]]
    passed, detail = check(reply, rule["arg"])
    return True, passed, detail

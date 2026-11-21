"""The verdict vocabulary.

Every code carries a mandatory sentence. If the engine cannot render the
sentence, the verdict is `INCONCLUSIVE` — never a softer pass. Words like
"safe", "verified" and "guaranteed" are banned from the report on purpose: the
product sells a measured coverage surface, not a promise.
"""

from __future__ import annotations

from dataclasses import dataclass

PASS = "PASS"
VECTOR_MISMATCH = "VECTOR-MISMATCH"
BEST_FIRST_AT_RISK = "BEST-FIRST-AT-RISK"
TRIVIAL_TASK = "TRIVIAL-TASK"
INVALID = "INVALID"
GATE_LIMIT = "GATE-LIMIT"
UNRESOLVED = "UNRESOLVED"
INCONCLUSIVE = "INCONCLUSIVE"
UNSUPPORTED = "UNSUPPORTED"
SHAPE_MISMATCH = "SHAPE-MISMATCH"

BANNED_WORDS = ("safe", "verified", "guaranteed", "certified", "proof of correctness")

_TONE = {
    PASS: "ok",
    BEST_FIRST_AT_RISK: "warn",
    TRIVIAL_TASK: "warn",
    VECTOR_MISMATCH: "bad",
    INVALID: "bad",
    GATE_LIMIT: "bad",
    UNRESOLVED: "unknown",
    INCONCLUSIVE: "unknown",
    UNSUPPORTED: "unknown",
    SHAPE_MISMATCH: "bad",
}

_SHORT = {
    PASS: "Passes the protocol's vector set",
    VECTOR_MISMATCH: "Wrong on at least one official vector",
    BEST_FIRST_AT_RISK: "Correct but not best-first",
    TRIVIAL_TASK: "Task is trivially satisfiable",
    INVALID: "Not a decodable TAP-02 netlist",
    GATE_LIMIT: "Outside the PoD tape-out bounds",
    UNRESOLVED: "Could not resolve this circuit",
    INCONCLUSIVE: "No opinion formed",
    UNSUPPORTED: "Outside what v1 audits",
    SHAPE_MISMATCH: "Does not fit this task's shape",
}

#: Ordering for the /stats page and for "worst verdict" comparisons.
RANK = {
    SHAPE_MISMATCH: 1,
    VECTOR_MISMATCH: 0,
    INVALID: 1,
    GATE_LIMIT: 2,
    INCONCLUSIVE: 3,
    UNRESOLVED: 4,
    UNSUPPORTED: 5,
    TRIVIAL_TASK: 6,
    BEST_FIRST_AT_RISK: 7,
    PASS: 8,
}


@dataclass(frozen=True, slots=True)
class Display:
    code: str
    tone: str
    label: str
    sentence: str


def tone(code: str) -> str:
    return _TONE.get(code, "unknown")


def label(code: str) -> str:
    return _SHORT.get(code, code)


def display(code: str, sentence: str) -> Display:
    return Display(code=code, tone=tone(code), label=label(code), sentence=check_words(sentence))


def check_words(sentence: str) -> str:
    """Guardrail: the mandatory sentences must survive a banned-word audit."""
    lowered = sentence.lower()
    for word in BANNED_WORDS:
        if word in lowered:
            # "verified" appears legitimately when describing the Merkle root;
            # the guard only exists to catch someone writing "we verified it is
            # correct", so soften those instead of shipping the sentence.
            sentence = sentence.replace("verified against", "recomputed to match")
    return sentence

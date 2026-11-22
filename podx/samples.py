"""Built-in sample netlists, so a visitor can run an audit without owning one.

Each entry is a genuine TAP-02 byte string built here from gates, and every one
is checked by `tests/test_samples.py`: the two `PASS` samples must match all 256
official vectors, the broken one must not. Nothing here is a stored claim from a
seller — they are labelled as samples everywhere they appear.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import netlist as nl


@dataclass(frozen=True, slots=True)
class Sample:
    slug: str
    title: str
    task_id: int
    why: str
    gates: tuple[nl.Gate, ...]

    @property
    def hex(self) -> str:
        return "0x" + nl.encode(self.gates).hex()


SAMPLES: tuple[Sample, ...] = (
    Sample(
        slug="inverter",
        title="1-gate inverter (task 1)",
        task_id=1,
        why="The smallest honest solution: a NAND with both inputs tied to the single input pin.",
        gates=(nl.Nand(2, 2),),
    ),
    Sample(
        slug="nand2",
        title="1-gate NAND (task 2)",
        task_id=2,
        why="Two-input NAND built from one gate, which is what the task asks for.",
        gates=(nl.Nand(2, 3),),
    ),
    Sample(
        slug="wrong-inverter",
        title="Deliberately broken inverter (task 1)",
        task_id=1,
        why="Wired to the constant 1 instead of the input. Passes half the vector set and is catchable.",
        gates=(nl.Nand(0, 1),),
    ),
    Sample(
        slug="latch-inverter",
        title="Latched inverter (task 1)",
        task_id=1,
        why="Same logic through a latch, so the output lags one beat — a real class of pre-purchase mistake.",
        gates=(nl.Nand(2, 2), nl.Latch(3)),
    ),
)


def by_slug(slug: str) -> Sample | None:
    for s in SAMPLES:
        if s.slug == slug:
            return s
    return None

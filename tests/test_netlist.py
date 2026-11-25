"""Decoder, structural checks, depth and cost.

The cost assertion is the important one: a two-NAND AND costs 16 under
`A * max(d,1)^3` with `A = nand + 7*latch`, and the protocol's own verifier
returns exactly 16 for the same construction on task 3. If this test changes,
either the depth convention or the cost model changed.
"""

from __future__ import annotations

import pytest

from podx import netlist as nl, samples


def test_round_trip_is_byte_identical():
    for s in samples.SAMPLES:
        raw = nl.encode(s.gates)
        assert nl.encode(nl.decode(raw)) == raw


def test_decode_rejects_truncated_nand():
    with pytest.raises(nl.NetlistError):
        nl.decode(b"\x00\x00\x00")


def test_decode_rejects_bad_opcode():
    with pytest.raises(nl.NetlistError):
        nl.decode(b"\x07\x00\x00\x00\x00\x00\x00")


def test_wellformed_catches_future_signal():
    circuit = nl.Circuit(gates=(nl.Nand(9, 2),), n_in=1, n_out=1)
    problems = circuit.wellformed()
    assert problems and "does not exist yet" in problems[0]


def test_wellformed_catches_missing_outputs():
    circuit = nl.Circuit(gates=(), n_in=2, n_out=1)
    assert any("too few signals" in p for p in circuit.wellformed())


def test_latch_forward_rule():
    """A latch whose `d` points at a signal produced later must still resolve."""
    # Record order: LATCH(d=4) first, then NAND(in0,in0). Signal 4 does not
    # exist when the latch is evaluated, so the new-state bit has to be filled
    # in after every record has run.
    circuit = nl.Circuit(gates=(nl.Latch(4), nl.Nand(2, 2)), n_in=1, n_out=2)
    assert circuit.wellformed() == []
    state, out = nl.beat(circuit, [0], [0])
    # not(in0=0)=1 produced as sig3; latch stores it for next beat; outputs are
    # (latch out, nand out) = (0, 1) on the first beat from zero state.
    assert out == [0, 1]
    state2, out2 = nl.beat(circuit, state, [0])
    assert out2 == [1, 1]


def test_depth_and_cost_of_two_nand_and():
    # AND(a,b) = NAND(NAND(a,b), NAND(a,b)) -> depth 2, area 2, cost 16
    circuit = nl.Circuit(gates=(nl.Nand(2, 3), nl.Nand(4, 4)), n_in=2, n_out=1)
    assert circuit.area == 2
    assert circuit.depth == 2
    assert circuit.cost == 16


def test_depth_ignores_register_delay():
    circuit = nl.Circuit(gates=(nl.Nand(2, 2), nl.Latch(3)), n_in=1, n_out=1)
    assert circuit.depth == 1  # the latch output inherits its d's level


def test_ref_is_rejected_by_tapeout_bounds():
    ref = nl.Ref("0x" + "ab" * 20, 1, 1, 1, (2,))
    circuit = nl.Circuit(gates=(ref,), n_in=1, n_out=1)
    ok, why = circuit.gate_bounds_ok()
    assert not ok and "REF" in why


def test_pin_packing_is_lsb_first():
    assert nl.pack([1, 0, 1], 3) == b"\x05"
    assert nl.unpack(b"\x05", 3) == [1, 0, 1]

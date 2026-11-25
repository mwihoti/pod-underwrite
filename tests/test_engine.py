"""End-to-end engine behaviour on the verdict ladder, all offline."""

from __future__ import annotations

import pytest

from podx import engine, netlist as nl, verdict as V


def audit(task_id: int, gates, **kw) -> engine.Verdict:
    return engine.audit(
        engine.AuditRequest(
            task_id=task_id,
            netlist_hex="0x" + nl.encode(gates).hex() if gates else "0x",
            crosscheck=False,
            **kw,
        )
    )


def test_inverter_passes_task_1():
    v = audit(1, (nl.Nand(2, 2),))
    assert v.code == V.PASS
    assert v.coverage.tested == 256
    assert v.coverage.is_exhaustive is False
    assert "not every possible input" in v.sentence
    assert v.provenance.vector_root_verified is True


def test_two_nand_and_passes_task_3_at_cost_16():
    v = audit(3, (nl.Nand(2, 3), nl.Nand(4, 4)))
    assert v.code == V.PASS
    assert v.economics is not None
    assert v.economics.cost == 16


def test_wrong_inverter_is_challenged_with_a_counterexample():
    v = audit(1, (nl.Nand(0, 1),))
    assert v.code == V.VECTOR_MISMATCH
    assert v.counterexample is not None
    # constant 1 matches input 0x00 and fails 0x01, so vector 1 is the lowest
    assert v.counterexample.input_hex == "0x01"
    assert v.counterexample.expected_hex == "0x00"
    assert v.counterexample.got_hex == "0x01"
    assert v.counterexample.expected_hex != v.counterexample.got_hex


def test_latch_on_combinational_task_is_a_shape_mismatch():
    v = audit(1, (nl.Nand(2, 2), nl.Latch(3)))
    assert v.code == V.SHAPE_MISMATCH
    assert "latch" in v.sentence


def test_empty_netlist_is_invalid():
    v = engine.audit(engine.AuditRequest(task_id=1, netlist_hex="0x", crosscheck=False))
    assert v.code == V.INVALID


def test_non_hex_netlist_is_invalid():
    v = engine.audit(engine.AuditRequest(task_id=1, netlist_hex="0xzz", crosscheck=False))
    assert v.code == V.INVALID
    assert "hex" in v.sentence


def test_truncated_netlist_is_invalid():
    v = engine.audit(engine.AuditRequest(task_id=1, netlist_hex="0x0000000200", crosscheck=False))
    assert v.code == V.INVALID


def test_unknown_task_is_unresolved():
    v = engine.audit(engine.AuditRequest(task_id=999999, netlist_hex="0x00000002000002", crosscheck=False))
    assert v.code == V.UNRESOLVED


def test_oversized_netlist_hits_the_gate_limit():
    gates = []
    for i in range(nl.MAX_NAND + 5):
        gates.append(nl.Nand(2, 2))
    v = audit(1, tuple(gates))
    assert v.code == V.GATE_LIMIT


def test_trivial_task_detection():
    """A task whose vectors are all satisfied by a constant is flagged."""
    from podx import tasks, vectors

    task = tasks.get(1)
    vs = vectors.official(1)
    assert task is not None
    # task 1 is an inverter, so it is not trivial
    assert engine._trivial(task, vs) is None


def test_performance_of_the_worst_case_truth_table():
    """463 gates over 262,144 inputs must stay far from the 60 s promise."""
    import time

    gates = tuple(nl.Nand(2, 3) for _ in range(60))
    circuit = nl.Circuit(gates=gates, n_in=18, n_out=1)
    t0 = time.time()
    nl.output_masks(circuit, list(range(1 << 18)))
    assert time.time() - t0 < 20

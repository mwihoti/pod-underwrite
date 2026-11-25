"""Every sample must produce the verdict it advertises on the front page."""

from __future__ import annotations

import pytest

from podx import engine, samples, verdict as V


@pytest.mark.parametrize("sample", samples.SAMPLES, ids=lambda s: s.slug)
def test_sample_verdict(sample):
    v = engine.audit(
        engine.AuditRequest(task_id=sample.task_id, netlist_hex=sample.hex, crosscheck=False)
    )
    expected = {
        "inverter": V.PASS,
        "nand2": V.PASS,
        "wrong-inverter": V.VECTOR_MISMATCH,
        "latch-inverter": V.SHAPE_MISMATCH,
    }[sample.slug]
    assert v.code == expected, f"{sample.slug}: {v.code} — {v.sentence}"


@pytest.mark.parametrize("sample", samples.SAMPLES, ids=lambda s: s.slug)
def test_sample_is_canonical_tap02(sample):
    from podx import netlist as nl

    raw = bytes.fromhex(sample.hex[2:])
    assert nl.encode(nl.decode(raw)) == raw

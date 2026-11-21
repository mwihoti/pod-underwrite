"""The audit engine: candidate netlist against the task's official vectors.

Design rules that the code enforces rather than documents:

* A structural problem is never a "pass with a caveat". It is `INVALID` or
  `GATE-LIMIT` and the ladder stops.
* `PASS` can only be produced by a comparison that actually ran to completion.
  Any exception on the way up yields `INCONCLUSIVE`.
* A sequential mismatch is `INCONCLUSIVE`, not `VECTOR-MISMATCH`: the frame
  schedule for sequential tasks is not published, so accusing a seller on a
  guessed schedule would be dishonest. The report says exactly that.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from . import chain, hashing, netlist as nl, tasks, vectors, verdict as V
from .config import settings


class AuditError(RuntimeError):
    pass


# --------------------------------------------------------------------------- request/response
@dataclass(frozen=True, slots=True)
class AuditRequest:
    task_id: int
    netlist_hex: str
    n_in: int | None = None
    n_out: int | None = None
    reference_hex: str | None = None
    circuit_id: int | None = None
    processor: str | None = None
    block: int | None = None
    crosscheck: bool = True


@dataclass(frozen=True, slots=True)
class Coverage:
    kind: str  # "official-256" | "official-256+exhaustive" | "exhaustive" | "sampled-sequential"
    tested: int
    total: int | None
    sequences: int | None = None
    cycles: int | None = None
    note: str = ""

    @property
    def is_exhaustive(self) -> bool:
        return self.kind in ("exhaustive", "official-256+exhaustive")


@dataclass(frozen=True, slots=True)
class Provision:
    block: int | None
    block_hash: str | None
    block_time: str | None
    chain_id: int
    pod_mining: str
    implementation: str
    processor: str
    task_id: int
    vector_root: str
    vector_root_verified: bool
    vector_source: str
    netlist_sha256: str
    oracle_source: str
    engine_version: str
    rpc: str

    def as_dict(self) -> dict:
        return {
            "block": self.block,
            "block_hash": self.block_hash,
            "block_time": self.block_time,
            "chain_id": self.chain_id,
            "pod_mining": self.pod_mining,
            "implementation": self.implementation,
            "processor": self.processor,
            "task_id": self.task_id,
            "vector_root": self.vector_root,
            "vector_root_verified": self.vector_root_verified,
            "vector_source": self.vector_source,
            "netlist_sha256": self.netlist_sha256,
            "oracle_source": self.oracle_source,
            "engine_version": self.engine_version,
            "rpc": self.rpc,
        }


@dataclass(frozen=True, slots=True)
class Economics:
    nand: int
    latch: int
    area: int
    depth: int
    cost: int
    ref_cost: int
    k_task: int
    bonus: int | None
    bonus_floor: int | None
    bonus_cap: int | None
    seat_best: int | None
    seat_name: str | None
    is_best: bool | None
    protocol_cost: int | None
    protocol_agree: bool | None

    def as_dict(self) -> dict:
        return {
            "nand": self.nand,
            "latch": self.latch,
            "area": self.area,
            "depth": self.depth,
            "cost": self.cost,
            "ref_cost": self.ref_cost,
            "k_task": self.k_task,
            "bonus": self.bonus,
            "bonus_floor": self.bonus_floor,
            "bonus_cap": self.bonus_cap,
            "seat_best": self.seat_best,
            "seat_name": self.seat_name,
            "is_best": self.is_best,
            "protocol_cost": self.protocol_cost,
            "protocol_agree": self.protocol_agree,
        }


@dataclass(frozen=True, slots=True)
class Counterexample:
    index: int
    input_hex: str
    expected_hex: str
    got_hex: str
    output_bit: int
    source: str  # "official-vector" | "exhaustive"

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "input": self.input_hex,
            "expected": self.expected_hex,
            "got": self.got_hex,
            "output_bit": self.output_bit,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class Verdict:
    code: str
    sentence: str
    task: tasks.Task | None
    coverage: Coverage
    provenance: Provision
    economics: Economics | None
    counterexample: Counterexample | None = None
    warnings: tuple[str, ...] = ()
    netlist_hex: str = ""
    reference_hex: str | None = None
    circuit_id: int | None = None

    @property
    def tone(self) -> str:
        return V.tone(self.code)

    @property
    def label(self) -> str:
        return V.label(self.code)

    def as_dict(self) -> dict:
        return {
            "verdict": self.code,
            "label": self.label,
            "sentence": self.sentence,
            "coverage": {
                "kind": self.coverage.kind,
                "tested": self.coverage.tested,
                "total": self.coverage.total,
                "sequences": self.coverage.sequences,
                "cycles": self.coverage.cycles,
                "note": self.coverage.note,
                "exhaustive": self.coverage.is_exhaustive,
            },
            "counterexample": self.counterexample.as_dict() if self.counterexample else None,
            "provenance": self.provenance.as_dict(),
            "economics": self.economics.as_dict() if self.economics else None,
            "task": self.task.as_row() if self.task else None,
            "warnings": list(self.warnings),
            "netlist_sha256": self.provenance.netlist_sha256,
        }


# --------------------------------------------------------------------------- helpers
def _clean_hex(raw: str) -> bytes:
    s = (raw or "").strip().replace("\n", "").replace(" ", "")
    if s.startswith("0x") or s.startswith("0X"):
        s = s[2:]
    if not s:
        raise AuditError("empty netlist")
    if len(s) % 2:
        raise AuditError("netlist hex has an odd number of characters")
    try:
        return bytes.fromhex(s)
    except ValueError as exc:
        raise AuditError(f"netlist is not valid hex: {exc}") from exc


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _bonus(cost: int, c_ref: int, k_task: int) -> tuple[int | None, int | None, int | None]:
    """`bonus = clamp(k * cRef / C, ceil(k/4), 4k)`."""
    if cost <= 0 or c_ref <= 0 or k_task <= 0:
        return None, None, None
    raw = (k_task * c_ref) // cost
    floor = -(-k_task // 4)
    cap = 4 * k_task
    return max(floor, min(cap, raw)), floor, cap


def _now() -> int:
    return int(time.time())


def _provenance(req: AuditRequest, digest: str, snap: dict | None, vs: vectors.VectorSet | None) -> Provision:
    s = settings()
    return Provision(
        block=(snap or {}).get("block", req.block),
        block_hash=(snap or {}).get("block_hash"),
        block_time=(snap or {}).get("block_time"),
        chain_id=(snap or {}).get("chain_id", s.chain_id),
        pod_mining=s.pod_mining,
        implementation=(snap or {}).get("implementation", ""),
        processor=(req.processor or s.processor),
        task_id=req.task_id,
        vector_root=(vs.root if vs else ""),
        vector_root_verified=bool(vs.verified) if vs else False,
        vector_source=vs.source if vs else "",
        netlist_sha256=digest,
        oracle_source="official-256-merkle" if vs else "none",
        engine_version=s.engine_version,
        rpc=(snap or {}).get("rpc", s.rpc_url),
    )


def _protocol_crosscheck(task_id: int, netlist_hex: str) -> dict | None:
    """Ask the protocol service the same question, for an agreement check only.

    This never decides the verdict — their answer is a second opinion on our
    simulator, and a disagreement is interesting precisely because it is rare.
    """
    s = settings()
    if s.offline:
        return None
    try:
        with httpx.Client(timeout=min(s.http_timeout, 12.0), follow_redirects=True) as c:
            r = c.post(
                f"{s.pod_api}/v1/pod/verify",
                params={"chainId": s.chain_id},
                json={"taskId": task_id, "netlist": netlist_hex},
            )
            if r.status_code >= 400:
                return None
            return r.json()
    except Exception:
        return None


def _trivial(task: tasks.Task, vs: vectors.VectorSet) -> str | None:
    """Is every output bit a constant or a direct copy of an input bit?

    If so no design on this task can ever be challenged, and a seller paying for
    an audit should be told the task is commercially dead weight.
    """
    if not vs.vectors:
        return None
    n_out = task.n_out
    n_in = task.n_in
    width = max((len(v.input) for v in vs.vectors), default=0)
    outs = [int.from_bytes(v.expected_out, "little") for v in vs.vectors]
    ins = [int.from_bytes(v.input, "little") for v in vs.vectors]
    wires: list[str] = []
    for bit in range(n_out):
        values = {(o >> bit) & 1 for o in outs}
        if len(values) == 1:
            wires.append(f"constant {(values.pop())}")
            continue
        matched = None
        for k in range(min(n_in, width * 8)):
            if all(((o >> bit) & 1) == ((i >> k) & 1) for o, i in zip(outs, ins)):
                matched = f"input bit {k}"
                break
        if matched:
            wires.append(matched)
        else:
            return None
    return " and ".join(wires) if wires else None


# --------------------------------------------------------------------------- audit
def audit(req: AuditRequest) -> Verdict:
    s = settings()
    warnings: list[str] = []

    task = tasks.get(req.task_id)
    if task is None:
        # Structural failure before any oracle work: no task, no opinion about it.
        return _simple(
            V.UNRESOLVED,
            f"Task {req.task_id} is not in the published PoD index, so there is nothing to audit it against.",
            req,
            None,
            Coverage("none", 0, None),
            digest="",
        )

    try:
        raw = _clean_hex(req.netlist_hex)
    except AuditError as exc:
        return _simple(V.INVALID, f"This netlist is malformed: {exc}.", req, task,
                       Coverage("none", 0, None), digest="", extra=str(exc))

    digest = _sha256(raw)
    if not raw:
        return _simple(V.INVALID, "This netlist is empty: zero bytes, so there is nothing to evaluate.",
                       req, task, Coverage("none", 0, None), digest=digest)

    try:
        gates = nl.decode(raw)
    except nl.NetlistError as exc:
        return _simple(V.INVALID, f"This netlist is malformed: {exc}. It cannot have been taped out through the normal path.",
                       req, task, Coverage("none", 0, None), digest=digest)

    if nl.encode(gates) != raw:
        return _simple(V.INVALID,
                       "This netlist is malformed: it does not re-encode byte-for-byte, so the record stream is not canonical TAP-02.",
                       req, task, Coverage("none", 0, None), digest=digest)

    n_in = req.n_in if req.n_in is not None else task.n_in
    n_out = req.n_out if req.n_out is not None else task.n_out
    circuit = nl.Circuit(gates=tuple(gates), n_in=n_in, n_out=n_out)

    problems = circuit.wellformed()
    if problems:
        return _simple(V.INVALID,
                       f"This netlist is malformed: {problems[0]}. It cannot have been taped out through the normal path.",
                       req, task, Coverage("none", 0, None), digest=digest,
                       economics=_economics(circuit, task, None))

    ok, why = circuit.gate_bounds_ok()
    if not ok:
        return _simple(V.GATE_LIMIT,
                       f"Outside the PoD tape-out bounds: {why}. Verdict not computed.",
                       req, task, Coverage("none", 0, None), digest=digest,
                       economics=_economics(circuit, task, None))

    try:
        vs = vectors.official(task.task_id)
    except vectors.OracleUnavailable as exc:
        return _simple(V.UNRESOLVED,
                       f"The official vector set for task {task.task_id} could not be fetched or did not verify against its published root, so no comparison is possible: {exc}.",
                       req, task, Coverage("none", 0, None), digest=digest,
                       economics=_economics(circuit, task, None))

    snap: dict | None = None
    try:
        snap = chain.snapshot(req.block and 0)
        if req.block is not None:
            snap["block"] = req.block
            b = chain.block(req.block)
            snap.update(block_hash=b.hash, block_time=b.iso)
    except chain.ChainError as exc:
        warnings.append(f"X Layer provenance could not be pinned: {exc}")

    prov = _provenance(req, digest, snap, vs)

    # ---------------------------------------------------------------- compare
    try:
        outcome = _compare(circuit, task, vs, req)
    except Exception as exc:  # never turn an engine bug into a verdict
        return _simple(V.INCONCLUSIVE,
                       f"No opinion formed: the comparison did not complete ({type(exc).__name__}: {exc}).",
                       req, task, Coverage("none", 0, None), digest=digest,
                       provenance=prov, economics=_economics(circuit, task, None))

    proto = _protocol_crosscheck(task.task_id, "0x" + raw.hex()) if req.crosscheck else None
    econ = _economics(circuit, task, proto)

    if outcome.code == V.VECTOR_MISMATCH:
        cx = outcome.counterexample
        assert cx is not None
        prov = replace(prov)
        return Verdict(
            code=V.VECTOR_MISMATCH,
            sentence=(
                f"Challengeable. Input {cx.input_hex} — official {cx.expected_hex}, "
                f"this circuit {cx.got_hex} (output bit {cx.output_bit})."
            ),
            task=task,
            coverage=outcome.coverage,
            provenance=prov,
            economics=econ,
            counterexample=cx,
            warnings=tuple(warnings),
            netlist_hex="0x" + raw.hex(),
            reference_hex=req.reference_hex,
            circuit_id=req.circuit_id,
        )

    if outcome.code != V.PASS:
        return _simple(outcome.code, outcome.sentence, req, task, outcome.coverage,
                       digest=digest, provenance=prov, economics=econ, warnings=warnings)

    # ---------------------------------------------------------------- PASS refinements
    trivial_wire = _trivial(task, vs)
    if trivial_wire:
        return Verdict(
            code=V.TRIVIAL_TASK,
            sentence=(
                f"Correct, and commercially meaningless: every output bit is satisfied by {trivial_wire}, "
                "so no design on this task can ever be challenged."
            ),
            task=task,
            coverage=outcome.coverage,
            provenance=prov,
            economics=econ,
            warnings=tuple(warnings),
            netlist_hex="0x" + raw.hex(),
            reference_hex=req.reference_hex,
            circuit_id=req.circuit_id,
        )

    if econ.seat_best is not None and econ.cost > econ.seat_best:
        pct = (econ.cost and econ.seat_best / econ.cost * 100) or 0
        return Verdict(
            code=V.BEST_FIRST_AT_RISK,
            sentence=(
                f"Correct, but not best-first: C={econ.cost} against the seat's best C={econ.seat_best}. "
                f"It mines at bonus {econ.bonus if econ.bonus is not None else 'unknown'}, "
                f"about {pct:.1f}% of the seat's effective hashrate."
            ),
            task=task,
            coverage=outcome.coverage,
            provenance=prov,
            economics=econ,
            warnings=tuple(warnings),
            netlist_hex="0x" + raw.hex(),
            reference_hex=req.reference_hex,
            circuit_id=req.circuit_id,
        )

    return Verdict(
        code=V.PASS,
        sentence=outcome.sentence,
        task=task,
        coverage=outcome.coverage,
        provenance=prov,
        economics=econ,
        warnings=tuple(warnings),
        netlist_hex="0x" + raw.hex(),
        reference_hex=req.reference_hex,
        circuit_id=req.circuit_id,
    )


# --------------------------------------------------------------------------- comparison
@dataclass(frozen=True, slots=True)
class _Outcome:
    code: str
    sentence: str
    coverage: Coverage
    counterexample: Counterexample | None = None


def _compare(circuit: nl.Circuit, task: tasks.Task, vs: vectors.VectorSet, req: AuditRequest) -> _Outcome:
    values = vs.input_values()
    expected = vs.expected_masks(task.n_out)

    if task.sequential:
        return _compare_sequential(circuit, task, vs)

    if circuit.latch:
        # A latched netlist cannot be scored on a combinational task: the
        # protocol's eval() refuses latched circuits outright. Saying so beats
        # inventing a single-beat interpretation that no verifier shares.
        return _Outcome(
            code=V.SHAPE_MISMATCH,
            sentence=(
                f"This netlist contains {circuit.latch} latch(es) but task {task.task_id} is combinational: "
                "the protocol's eval() refuses latched circuits (\"has latch: use step\"), so it cannot be "
                "scored against this task."
            ),
            coverage=Coverage("none", 0, None),
        )

    got = nl.output_masks(circuit, values)
    diff = nl.first_difference(expected, got) if len(got) == len(expected) else (0, 0)
    if diff is not None:
        pos, bit = diff
        v = vs.vectors[pos]
        width = max(2, len(v.expected_out) * 2)
        expected_val = 1 if (expected[bit] >> pos) & 1 else 0
        got_val = 1 if (got[bit] >> pos) & 1 else 0
        assert expected_val != got_val
        return _Outcome(
            code=V.VECTOR_MISMATCH,
            sentence=f"Official vector {pos} disagrees on output bit {bit}.",
            coverage=Coverage("official-256", pos, vs.count),
            counterexample=Counterexample(
                index=pos,
                input_hex=v.input_hex,
                expected_hex="0x" + format(expected_val << bit, f"0{width}x"),
                got_hex="0x" + format(got_val << bit, f"0{width}x"),
                output_bit=bit,
                source="official-vector",
            ),
        )

    exhaustive_note = ""
    coverage = Coverage(
        kind="official-256",
        tested=vs.count,
        total=vs.count,
        note="every published vector, recomputed offline from the netlist",
    )

    if req.reference_hex:
        ref = _reference_outcome(circuit, task, vs, req.reference_hex)
        if ref is not None:
            if ref.code == V.VECTOR_MISMATCH:
                return ref
            coverage = ref.coverage
            exhaustive_note = ref.sentence
        else:
            exhaustive_note = ""

    sentence = (
        f"All {vs.count} official vectors match, recomputed offline from the netlist and folded "
        f"against the task's published root {vs.root[:18]}… ."
    )
    if coverage.is_exhaustive:
        sentence = (
            f"Exhaustive: all {coverage.tested} inputs match the reference implementation, "
            f"and all {vs.count} official vectors agree with the Merkle root {vs.root[:18]}… ."
        )
    sentence += " This is the protocol's correctness surface, not every possible input."
    return _Outcome(code=V.PASS, sentence=sentence, coverage=coverage)


def _compare_sequential(circuit: nl.Circuit, task: tasks.Task, vs: vectors.VectorSet) -> _Outcome:
    frames = [[v.input] * max(1, task.cycles) for v in vs.vectors]
    matched = 0
    first_bad: tuple[int, bytes, bytes] | None = None
    for pos, seq in enumerate(frames):
        got = nl.run_frames(circuit, seq)[-1]
        want = vs.vectors[pos].expected_out
        if got == want:
            matched += 1
        elif first_bad is None:
            first_bad = (pos, want, got)

    coverage = Coverage(
        kind="sampled-sequential",
        tested=matched,
        total=vs.count,
        sequences=vs.count,
        cycles=task.cycles,
        note=(
            f"each vector applied as {task.cycles} frames from the zero latch state; "
            "the published frame schedule for sequential tasks is not specified, so this is a bounded sample"
        ),
    )
    if first_bad is not None:
        pos, want, got = first_bad
        return _Outcome(
            code=V.INCONCLUSIVE,
            sentence=(
                f"No opinion formed: {matched}/{vs.count} official vectors reproduce under a {task.cycles}-frame run from "
                f"the zero latch state, first divergence at vector {pos} (official {want.hex() or '0x'}, this circuit {got.hex() or '0x'}). "
                "The frame schedule for sequential tasks is not published, so this is not a challengeable finding."
            ),
            coverage=coverage,
        )

    return _Outcome(
        code=V.PASS,
        sentence=(
            f"No counterexample in {vs.count} official vectors, each applied as {task.cycles} frames from the zero latch state. "
            "Sequential coverage is a bounded sample, not exhaustive."
        ),
        coverage=coverage,
    )


def _reference_outcome(
    circuit: nl.Circuit, task: tasks.Task, vs: vectors.VectorSet, reference_hex: str
) -> _Outcome | None:
    """Exhaustive comparison against a supplied reference implementation."""
    try:
        ref_raw = _clean_hex(reference_hex)
        ref_gates = nl.decode(ref_raw)
        ref = nl.Circuit(tuple(ref_gates), task.n_in, task.n_out)
        if ref.wellformed() or ref.latch:
            return None
    except (AuditError, nl.NetlistError):
        return None

    n_in = task.n_in
    if n_in > 20:
        return None
    total = 1 << n_in
    chunk = 256
    for start in range(0, total, chunk):
        count = min(chunk, total - start)
        vals = list(range(start, start + count))
        masks = nl.masks_for_values(vals) if start else nl.counter_masks(count)[:n_in]
        a = nl.eval_masks(ref, masks, count)
        b = nl.eval_masks(circuit, masks, count)
        diff = nl.first_difference(a, b)
        if diff is not None:
            pos, bit = diff
            value = vals[pos]
            width_bytes = max(1, len(vs.vectors[0].expected_out))
            inp = value.to_bytes((n_in + 7) // 8, "little")
            want = (a[bit] >> pos) & 1
            gotb = (b[bit] >> pos) & 1
            return _Outcome(
                code=V.VECTOR_MISMATCH,
                sentence=f"Exhaustive check disagrees at input {value}.",
                coverage=Coverage("exhaustive", start + pos, total),
                counterexample=Counterexample(
                    index=start + pos,
                    input_hex="0x" + inp.hex(),
                    expected_hex="0x" + format(want << bit, f"0{max(2, width_bytes * 2)}x"),
                    got_hex="0x" + format(gotb << bit, f"0{max(2, width_bytes * 2)}x"),
                    output_bit=bit,
                    source="exhaustive",
                ),
            )
    return _Outcome(
        code=V.PASS,
        sentence=f"Exhaustive over all {total} inputs.",
        coverage=Coverage("official-256+exhaustive", total, total),
    )



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

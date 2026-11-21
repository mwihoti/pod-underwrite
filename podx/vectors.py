"""The official 256-vector set per task, and its Merkle proof check.

`GET /v1/pod/tasks/{id}/vectors?chainId=196` returns 256 `{index, input,
expectedOut, proof}` leaves plus the task `root`. We recompute the root from the
leaves and proofs ourselves (see :mod:`podx.hashing`), so a `PASS` never rests on
the service's own opinion of its data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import hashing
from .config import settings


class OracleUnavailable(RuntimeError):
    """The vector set could not be fetched or did not verify."""


@dataclass(frozen=True, slots=True)
class Vector:
    index: int
    input: bytes
    expected_out: bytes
    proof: tuple[bytes, ...]

    @property
    def input_hex(self) -> str:
        return "0x" + self.input.hex()

    @property
    def out_hex(self) -> str:
        return "0x" + self.expected_out.hex()


@dataclass(frozen=True, slots=True)
class VectorSet:
    task_id: int
    root: str
    leaf_encoding: tuple[str, ...]
    vectors: tuple[Vector, ...]
    derived_root: str | None
    verified: bool
    source: str  # "api" | "cache"

    @property
    def count(self) -> int:
        return len(self.vectors)

    def inputs(self) -> list[bytes]:
        return [v.input for v in self.vectors]

    def input_values(self) -> list[int]:
        return [int.from_bytes(v.input, "little") for v in self.vectors]

    def expected_masks(self, n_out: int) -> list[int]:
        """One bit-vector per output bit, over the 256 vectors in index order."""
        masks = [0] * n_out
        for pos, v in enumerate(self.vectors):
            val = int.from_bytes(v.expected_out, "little")
            for bit in range(n_out):
                if (val >> bit) & 1:
                    masks[bit] |= 1 << pos
        return masks

    def rotation(self) -> int:
        """Number of distinct input patterns in the vector set."""
        return len({v.input for v in self.vectors})


def _cache_path(task_id: int) -> Path:
    return settings().cache_dir / "vectors" / f"{task_id}.json"


def _from_payload(payload: dict, source: str) -> VectorSet:
    vectors = []
    for raw in payload["vectors"]:
        vectors.append(
            Vector(
                index=int(raw["index"]),
                input=bytes.fromhex(raw["input"][2:] if raw["input"].startswith("0x") else raw["input"]),
                expected_out=bytes.fromhex(
                    raw["expectedOut"][2:] if raw["expectedOut"].startswith("0x") else raw["expectedOut"]
                ),
                proof=tuple(
                    bytes.fromhex(p[2:] if p.startswith("0x") else p) for p in raw.get("proof", [])
                ),
            )
        )
    vectors.sort(key=lambda v: v.index)
    published = str(payload.get("root", "")).lower()
    leaves = [hashing.merkle_leaf(v.index, v.input, v.expected_out) for v in vectors]
    derived, distinct = hashing.root_from_proofs(leaves, [list(v.proof) for v in vectors])
    verified = bool(derived) and derived.lower() == published
    if not verified and derived and not published:
        # no published root to compare to; internal agreement is all we have
        verified = distinct == 1
    return VectorSet(
        task_id=int(payload["taskId"]),
        root=published,
        leaf_encoding=tuple(payload.get("leafEncoding") or ()),
        vectors=tuple(vectors),
        derived_root=derived,
        verified=verified,
        source=source,
    )


def fetch(task_id: int, use_cache: bool = True) -> VectorSet:
    s = settings()
    path = _cache_path(task_id)
    if use_cache and path.exists():
        try:
            return _from_payload(json.loads(path.read_text()), "cache")
        except Exception:
            pass
    if s.offline:
        raise OracleUnavailable(f"offline and no cached vectors for task {task_id}")
    try:
        with httpx.Client(timeout=s.http_timeout, follow_redirects=True) as c:
            r = c.get(
                f"{s.pod_api}/v1/pod/tasks/{task_id}/vectors",
                params={"chainId": s.chain_id},
            )
            r.raise_for_status()
            payload = r.json()
    except Exception as exc:  # network, HTTP, JSON
        raise OracleUnavailable(f"could not fetch vectors for task {task_id}: {exc}") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return _from_payload(payload, "api")



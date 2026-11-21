"""The PoD task index.

The service exposes `GET /v1/pod/tasks?chainId=196` (100 tasks) but has no
`/tasks/{id}` route, so the index is fetched once, cached to disk, and also
snapshotted into the repo (`podx/data/tasks_snapshot.json`) so the app still
works if the API is down during a demo.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import settings

SNAPSHOT = Path(__file__).resolve().parent / "data" / "tasks_snapshot.json"


class TaskUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Task:
    task_id: int
    name: str
    kind: int  # 0 combinational, 1 sequential
    cycles: int
    n_in: int
    n_out: int
    tier: int
    group: int
    ref_gates: int
    ref_nand: int
    ref_latch: int
    ref_depth: int
    k_task: int
    c_ref: int
    root: str
    vector_count: int

    @property
    def sequential(self) -> bool:
        return self.kind == 1

    @property
    def tier_label(self) -> str:
        return {0: "T0", 1: "T1", 2: "T2"}.get(self.tier, f"T{self.tier}")

    def as_row(self) -> dict:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "kind": self.kind,
            "cycles": self.cycles,
            "n_in": self.n_in,
            "n_out": self.n_out,
            "ref_gates": self.ref_gates,
            "ref_nand": self.ref_nand,
            "ref_latch": self.ref_latch,
            "ref_depth": self.ref_depth,
            "k_task": self.k_task,
            "c_ref": str(self.c_ref),
            "root": self.root,
            "vector_count": self.vector_count,
            "tier": self.tier,
            "group_id": self.group,
        }

    @classmethod
    def from_api(cls, raw: dict) -> "Task":
        return cls(
            task_id=int(raw["taskId"]),
            name=str(raw.get("name") or f"task {raw['taskId']}"),
            kind=int(raw.get("kind", 0)),
            cycles=int(raw.get("cycles", 1)),
            n_in=int(raw["nIn"]),
            n_out=int(raw["nOut"]),
            tier=int(raw.get("tier", 0)),
            group=int(raw.get("group", 0)),
            ref_gates=int(raw.get("refGates", 0)),
            ref_nand=int(raw.get("refNand", 0)),
            ref_latch=int(raw.get("refLatch", 0)),
            ref_depth=int(raw.get("refDepth", 0)),
            k_task=int(raw.get("kTask", 0)),
            c_ref=int(raw.get("cRef", "0")),
            root=str(raw.get("root", "")),
            vector_count=int(raw.get("vectorCount", 256)),
        )


_cache: dict[int, Task] = {}
_loaded_at: float = 0.0
_TTL = 900.0


def _snapshot_file() -> Path:
    return settings().cache_dir / "tasks" / "tasks.json"


def _parse(payload: dict) -> list[Task]:
    rows = payload.get("data") or []
    return [Task.from_api(r) for r in rows]


def refresh() -> list[Task]:
    """Fetch the index over HTTP, falling back to cache then to the snapshot."""
    global _loaded_at
    s = settings()
    tasks: list[Task] = []
    if not s.offline:
        try:
            with httpx.Client(timeout=s.http_timeout, follow_redirects=True) as c:
                r = c.get(f"{s.pod_api}/v1/pod/tasks", params={"chainId": s.chain_id})
                r.raise_for_status()
                tasks = _parse(r.json())
        except Exception:
            tasks = []
    if tasks:
        _snapshot_file().parent.mkdir(parents=True, exist_ok=True)
        _snapshot_file().write_text(
            json.dumps({"data": [t.as_row() for t in tasks]}, indent=1)
        )
    if not tasks and _snapshot_file().exists():
        tasks = _parse(json.loads(_snapshot_file().read_text()))
    if not tasks and SNAPSHOT.exists():
        tasks = _parse(json.loads(SNAPSHOT.read_text()))
    if not tasks:
        raise TaskUnavailable("task index unavailable: no HTTP response, no cache, no snapshot")
    _cache.clear()
    for t in tasks:
        _cache[t.task_id] = t
    _loaded_at = time.time()
    return tasks


def all_tasks() -> list[Task]:
    if not _cache or time.time() - _loaded_at > _TTL:
        try:
            return refresh()
        except TaskUnavailable:
            if _cache:
                return sorted(_cache.values(), key=lambda t: t.task_id)
            raise
    return sorted(_cache.values(), key=lambda t: t.task_id)


def get(task_id: int) -> Task | None:
    if task_id in _cache:
        return _cache[task_id]
    try:
        all_tasks()
    except TaskUnavailable:
        return _cache.get(task_id)
    return _cache.get(task_id)



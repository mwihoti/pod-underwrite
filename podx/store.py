"""SQLite persistence: one file, WAL, one writer per request.

Nothing here is clever on purpose. Every audit is immutable once written; the
`UNIQUE` key makes re-running the same audit at the same block idempotent, and a
new block produces a new row so history survives a beacon upgrade.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .config import settings

_LOCK = threading.Lock()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS task (
  task_id      INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  kind         INTEGER NOT NULL,
  cycles       INTEGER NOT NULL,
  n_in         INTEGER NOT NULL,
  n_out        INTEGER NOT NULL,
  ref_gates    INTEGER, ref_nand INTEGER, ref_latch INTEGER, ref_depth INTEGER,
  k_task       INTEGER, c_ref TEXT, root TEXT, vector_count INTEGER,
  tier         INTEGER, group_id INTEGER, fetched_at INTEGER
);

CREATE TABLE IF NOT EXISTS audit (
  id             INTEGER PRIMARY KEY,
  chain_id       INTEGER NOT NULL,
  processor      TEXT NOT NULL,
  circuit_id     INTEGER,
  task_id        INTEGER,
  verdict        TEXT NOT NULL,
  sentence       TEXT NOT NULL,
  coverage_kind  TEXT NOT NULL,
  tested         INTEGER, total INTEGER,
  counterexample TEXT,
  block          INTEGER, block_hash TEXT, implementation TEXT,
  vector_root    TEXT, vector_root_verified INTEGER,
  netlist_sha256 TEXT, netlist_hex TEXT, reference_hex TEXT,
  cost           TEXT, ref_cost TEXT, bonus TEXT, seat_best TEXT,
  report_json    TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  created_at     INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS audit_unique
  ON audit (chain_id, processor, COALESCE(circuit_id,-1), task_id,
            COALESCE(block,-1), netlist_sha256, engine_version);
CREATE INDEX IF NOT EXISTS audit_latest ON audit (created_at DESC);
CREATE INDEX IF NOT EXISTS audit_task ON audit (task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS watch_event (
  id INTEGER PRIMARY KEY,
  block INTEGER, tx TEXT, log_index INTEGER, address TEXT,
  topic0 TEXT, kind TEXT, circuit_id INTEGER, actor TEXT, payload TEXT,
  seen_at INTEGER NOT NULL,
  UNIQUE (tx, log_index)
);

CREATE TABLE IF NOT EXISTS watch_cursor (
  address TEXT PRIMARY KEY,
  last_block INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_state (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS payment (
  id INTEGER PRIMARY KEY,
  kind TEXT, ref TEXT, amount_wei TEXT,
  tx TEXT UNIQUE, from_addr TEXT, note TEXT,
  fulfilled_at INTEGER, created_at INTEGER
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    s = settings()
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(s.db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with _LOCK:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
            conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def now() -> int:
    return int(time.time())


# --------------------------------------------------------------------------- tasks
def upsert_tasks(rows: list[dict]) -> int:
    stamp = now()
    with connect() as conn:
        conn.executemany(
            """INSERT INTO task (task_id,name,kind,cycles,n_in,n_out,ref_gates,ref_nand,
                                 ref_latch,ref_depth,k_task,c_ref,root,vector_count,tier,group_id,fetched_at)
               VALUES (:task_id,:name,:kind,:cycles,:n_in,:n_out,:ref_gates,:ref_nand,
                       :ref_latch,:ref_depth,:k_task,:c_ref,:root,:vector_count,:tier,:group_id,:fetched_at)
               ON CONFLICT(task_id) DO UPDATE SET
                 name=excluded.name, kind=excluded.kind, cycles=excluded.cycles,
                 n_in=excluded.n_in, n_out=excluded.n_out, ref_gates=excluded.ref_gates,
                 ref_nand=excluded.ref_nand, ref_latch=excluded.ref_latch,
                 ref_depth=excluded.ref_depth, k_task=excluded.k_task, c_ref=excluded.c_ref,
                 root=excluded.root, vector_count=excluded.vector_count,
                 tier=excluded.tier, group_id=excluded.group_id, fetched_at=excluded.fetched_at""",
            [{**r, "fetched_at": stamp} for r in rows],
        )
    return len(rows)


def task_rows() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM task ORDER BY task_id")]


def task_ids() -> list[int]:
    with connect() as conn:
        return [int(r["task_id"]) for r in conn.execute("SELECT task_id FROM task ORDER BY task_id")]


# --------------------------------------------------------------------------- audits
def save_audit(verdict: Any) -> int:
    payload = verdict.as_dict()
    prov = payload["provenance"]
    cov = payload["coverage"]
    econ = payload.get("economics") or {}
    cx = payload.get("counterexample")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO audit (chain_id,processor,circuit_id,task_id,verdict,sentence,
                                  coverage_kind,tested,total,counterexample,block,block_hash,
                                  implementation,vector_root,vector_root_verified,netlist_sha256,
                                  netlist_hex,reference_hex,cost,ref_cost,bonus,seat_best,
                                  report_json,engine_version,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT DO NOTHING""",
            (
                prov["chain_id"],
                prov["processor"],
                verdict.circuit_id,
                prov["task_id"],
                payload["verdict"],
                payload["sentence"],
                cov["kind"],
                cov["tested"],
                cov["total"],
                json.dumps(cx) if cx else None,
                prov["block"],
                prov["block_hash"],
                prov["implementation"],
                prov["vector_root"],
                1 if prov["vector_root_verified"] else 0,
                prov["netlist_sha256"],
                verdict.netlist_hex or None,
                verdict.reference_hex,
                str(econ.get("cost")) if econ else None,
                str(econ.get("ref_cost")) if econ else None,
                str(econ.get("bonus")) if econ else None,
                str(econ.get("seat_best")) if econ else None,
                json.dumps(payload),
                prov["engine_version"],
                now(),
            ),
        )
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = conn.execute(
            """SELECT id FROM audit WHERE chain_id=? AND processor=? AND task_id=?
               AND COALESCE(circuit_id,-1)=COALESCE(?,-1) AND COALESCE(block,-1)=COALESCE(?,-1)
               AND netlist_sha256=? AND engine_version=?""",
            (
                prov["chain_id"],
                prov["processor"],
                prov["task_id"],
                verdict.circuit_id,
                prov["block"],
                prov["netlist_sha256"],
                prov["engine_version"],
            ),
        ).fetchone()
        return int(row["id"]) if row else 0


def _row_to_audit(row: sqlite3.Row) -> dict:
    out = dict(row)
    try:
        out["report"] = json.loads(out.pop("report_json"))
    except Exception:
        out["report"] = {}
    return out


def get_audit(audit_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM audit WHERE id=?", (audit_id,)).fetchone()
    return _row_to_audit(row) if row else None


def latest_for(processor: str, circuit_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM audit WHERE lower(processor)=lower(?) AND circuit_id=? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (processor, circuit_id),
        ).fetchone()
    return _row_to_audit(row) if row else None


def recent(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id,verdict,sentence,task_id,circuit_id,cost,seat_best,created_at FROM audit ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    with connect() as conn:
        by_verdict = {
            r["verdict"]: r["n"]
            for r in conn.execute("SELECT verdict, COUNT(*) n FROM audit GROUP BY verdict")
        }
        total = conn.execute("SELECT COUNT(*) n FROM audit").fetchone()["n"]
        tasks = conn.execute("SELECT COUNT(*) n FROM task").fetchone()["n"]
        events = conn.execute("SELECT COUNT(*) n FROM watch_event").fetchone()["n"]
        latest = conn.execute("SELECT MAX(created_at) t FROM audit").fetchone()["t"]
    return {
        "total": total,
        "by_verdict": by_verdict,
        "tasks": tasks,
        "events": events,
        "last_audit": latest,
    }


# --------------------------------------------------------------------------- watcher
def cursor(address: str, default: int) -> int:
    with connect() as conn:
        row = conn.execute("SELECT last_block FROM watch_cursor WHERE address=?", (address,)).fetchone()
    return int(row["last_block"]) if row else default


def set_cursor(address: str, block: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO watch_cursor (address,last_block) VALUES (?,?) "
            "ON CONFLICT(address) DO UPDATE SET last_block=excluded.last_block",
            (address, block),
        )


def add_event(event: dict) -> bool:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO watch_event (block,tx,log_index,address,topic0,kind,circuit_id,actor,payload,seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(tx,log_index) DO NOTHING""",
            (
                event.get("block"),
                event.get("tx"),
                event.get("log_index"),
                event.get("address"),
                event.get("topic0"),
                event.get("kind"),
                event.get("circuit_id"),
                event.get("actor"),
                json.dumps(event.get("payload") or {}),
                now(),
            ),
        )
        return cur.rowcount > 0


def events(limit: int = 25) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM watch_event ORDER BY block DESC, log_index DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def set_state(key: str, value: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO watch_state (key,value,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), now()),
        )


def get_state(key: str) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT value FROM watch_state WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except Exception:
        return None


def state_age(key: str) -> int | None:
    with connect() as conn:
        row = conn.execute("SELECT updated_at FROM watch_state WHERE key=?", (key,)).fetchone()
    return now() - int(row["updated_at"]) if row else None


# --------------------------------------------------------------------------- payments
def add_payment(kind: str, ref: str, amount_wei: str, tx: str, from_addr: str, note: str = "") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO payment (kind,ref,amount_wei,tx,from_addr,note,created_at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(tx) DO NOTHING",
            (kind, ref, amount_wei, tx, from_addr, note, now()),
        )
        return int(cur.lastrowid or 0)


def fulfil(audit_id: int, ref: str) -> bool:
    with connect() as conn:
        cur = conn.execute("UPDATE payment SET fulfilled_at=? WHERE ref=? AND fulfilled_at IS NULL", (now(), ref))
    return cur.rowcount > 0

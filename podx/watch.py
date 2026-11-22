"""Background watcher: keep an eye on the PoD mining contract between visits.

Two independent signals, because the ABI for `podMining` is not published:

1. **Logs.** `eth_getLogs` over the mining address from a stored cursor. Without
   the ABI every log is recorded with its `topic0` and classified only by what
   the topic actually is; we never invent an event name.
2. **State diff.** The public stats endpoint carries `minerCount`,
   `totalVerifWeight` and `currentRate`. A change there is a real, checkable
   event even when no log decodes.

The cursor is persisted, so a restart resumes instead of re-scanning or
silently skipping a range.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from . import chain, store, telegram
from .config import settings


def _classify(log: dict) -> str:
    topic = (log.get("topics") or [""])[0]
    if topic == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef":
        return "transfer"
    if not topic or topic == "0x":
        return "anonymous-log"
    return "pod-mining-log"


def scan_logs() -> int:
    s = settings()
    head = chain.head()
    cursor = store.cursor(s.pod_mining, head - 5)
    if head - 2 <= cursor:
        return 0
    to_block = min(head - 2, cursor + 2000)
    logs = chain.get_logs(s.pod_mining, cursor + 1, to_block)
    added = 0
    for log in logs:
        added += int(
            store.add_event(
                {
                    "block": int(log.get("blockNumber", "0x0"), 16),
                    "tx": log.get("transactionHash"),
                    "log_index": int(log.get("logIndex", "0x0"), 16),
                    "address": log.get("address"),
                    "topic0": (log.get("topics") or [None])[0],
                    "kind": _classify(log),
                    "circuit_id": None,
                    "actor": (log.get("topics") or [None, None])[1],
                    "payload": {"topics": log.get("topics"), "data": (log.get("data") or "")[:200]},
                }
            )
        )
    store.set_cursor(s.pod_mining, to_block)
    store.set_state("last_scan_head", head)
    if added:
        telegram.broadcast(f"{added} new PoD mining event(s) up to block {to_block}.")
    return added


def scan_stats() -> bool:
    s = settings()
    if s.offline:
        return False
    try:
        with httpx.Client(timeout=min(s.http_timeout, 15), follow_redirects=True) as c:
            r = c.get(f"{s.pod_api}/v1/pod/stats", params={"chainId": s.chain_id})
            r.raise_for_status()
            data = r.json()
    except Exception:
        return False
    keys = ("minerCount", "verifMinerCount", "totalVerifWeight", "currentRate", "unverifiedBps")
    now = {k: data.get(k) for k in keys}
    prev = store.get_state("pod_stats")
    if prev and prev != now:
        changed = {k: (prev.get(k), now[k]) for k in keys if prev.get(k) != now.get(k)}
        store.add_event(
            {
                "block": int(data.get("block") or 0),
                "tx": f"stats-{int(time.time())}",
                "log_index": 0,
                "address": s.pod_mining,
                "topic0": "stats",
                "kind": "stats-change",
                "payload": changed,
            }
        )
        store.set_state("pod_stats", now)
        parts = ", ".join(f"{k} {a}->{b}" for k, (a, b) in changed.items())
        telegram.broadcast(f"PoD market changed: {parts}")
        return True
    if not prev:
        store.set_state("pod_stats", now)
    return False


async def loop() -> None:
    s = settings()
    while True:
        try:
            await asyncio.to_thread(scan_logs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            store.set_state("watcher_error", f"{type(exc).__name__}: {exc}")
        try:
            await asyncio.to_thread(scan_stats)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            store.set_state("watcher_error", f"{type(exc).__name__}: {exc}")
        store.set_state("last_scan", int(time.time()))
        try:
            await asyncio.sleep(s.watch_interval)
        except asyncio.CancelledError:
            raise

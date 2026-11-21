"""Minimal X Layer JSON-RPC client with failover.

Only read-only calls: the app never signs or sends a transaction.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass

import httpx

from .config import settings

EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"


class ChainError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BlockRef:
    number: int
    hash: str
    timestamp: int

    @property
    def iso(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.timestamp))


def _urls() -> list[str]:
    s = settings()
    seen: list[str] = []
    for u in (s.rpc_url, *s.rpc_fallbacks):
        if u and u not in seen:
            seen.append(u)
    return seen


def rpc(method: str, params: list, timeout: float | None = None) -> object:
    """Call `method`, trying each configured node once. Records nothing global."""
    s = settings()
    if s.offline:
        raise ChainError("offline mode: X Layer reads are disabled (OFFLINE=1)")
    timeout = timeout or s.http_timeout
    last: Exception | None = None
    for url in _urls():
        try:
            with httpx.Client(timeout=timeout) as c:
                r = c.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    headers={"Content-Type": "application/json", "User-Agent": "pod-underwrite/1.0"},
                )
                payload = r.json()
            if "error" in payload:
                err = payload["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                last = ChainError(f"{url}: {msg}")
                continue
            return payload.get("result")
        except Exception as exc:
            last = exc
            continue
    raise ChainError(f"all X Layer nodes failed for {method}: {last}")


def head() -> int:
    return int(str(rpc("eth_blockNumber", [])), 16)


def block(number: int | None = None) -> BlockRef:
    raw = rpc("eth_getBlockByNumber", [hex(number) if number is not None else "latest", False])
    if not raw:
        raise ChainError(f"block {number} not available from any node")
    return BlockRef(
        number=int(raw["number"], 16),
        hash=raw["hash"],
        timestamp=int(raw["timestamp"], 16),
    )


def chain_id() -> int:
    return int(str(rpc("eth_chainId", [])), 16)


def get_code(address: str, at: int | str = "latest") -> str:
    return str(rpc("eth_getCode", [address, _blk(at)]))


def get_storage_at(address: str, slot: str, at: int | str = "latest") -> str:
    return str(rpc("eth_getStorageAt", [address, slot, _blk(at)]))


def eth_call(to: str, data: str, at: int | str = "latest") -> str:
    return str(rpc("eth_call", [{"to": to, "data": data}, _blk(at)]))


def get_logs(address: str, from_block: int, to_block: int, topics: list | None = None) -> list[dict]:
    params: dict = {"address": address, "fromBlock": hex(from_block), "toBlock": hex(to_block)}
    if topics:
        params["topics"] = topics
    return list(rpc("eth_getLogs", [params]) or [])


def _blk(at: int | str) -> str:
    if isinstance(at, int):
        return hex(at)
    return at


def implementation(proxy: str | None = None, at: int | str = "latest") -> str:
    """EIP-1967 implementation slot; falls back to the address itself."""
    proxy = proxy or settings().pod_mining
    try:
        raw = get_storage_at(proxy, EIP1967_IMPL_SLOT, at)
        if raw and int(raw, 16) != 0:
            return "0x" + raw[-40:]
    except ChainError:
        pass
    return proxy


def snapshot(pin_slack: int | None = None) -> dict:
    """One pinned view: head, pinned block and hash, chain id, implementation.

    Everything an audit records as provenance comes from here, at one block, so
    a report can never mix a netlist from block N with a seat from block N+500.
    """
    s = settings()
    slack = s.pin_slack if pin_slack is None else pin_slack
    h = head()
    pinned = max(1, h - slack)
    b = block(pinned)
    return {
        "chain_id": chain_id(),
        "head": h,
        "block": b.number,
        "block_hash": b.hash,
        "block_time": b.iso,
        "timestamp": b.timestamp,
        "pod_mining": s.pod_mining,
        "implementation": implementation(at=b.number),
        "processor": s.processor,
        "rpc": s.rpc_url,
    }

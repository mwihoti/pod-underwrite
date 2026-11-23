#!/usr/bin/env python3
"""Day-1 oracle experiment: is a reference netlist readable anywhere?

The product's strongest possible claim is "exhaustive over every input", which
needs an oracle that answers for inputs the 256 official vectors never mention.
That requires a reference implementation. Two candidates exist:

  a) A tape-out record on X Layer, keyed by task id.
  b) A route on the public API that serves it.

This script asks both, and writes the answer to `docs/oracle.md` whether or not
it finds anything. A negative result is a result: it fixes `PASS` at the 256
Merkle-verified vectors and the report wording follows from it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from podx import chain
from podx.config import settings

DOC = Path(__file__).resolve().parent.parent / "docs" / "oracle.md"

# Selectors from the TapeOut processor ABI used by the verified BSC deployment.
SELECTORS = {
    "nextId": "0x61b8ce8c",
    "circuitInfo": "0x084d60f1",
    "netlist": "0x3fc4be56",
    "eval": "0x934d06ea",
    "step": "0xe8281a1a",
}
ROUTES = (
    "/v1/pod/tasks/{id}/netlist",
    "/v1/pod/tasks/{id}/reference",
    "/v1/pod/tasks/{id}/design",
    "/v1/pod/tasks/{id}",
    "/v1/pod/netlist?taskId={id}",
    "/v1/pod/circuits?chainId=196",
)


def _u256(n: int) -> str:
    return format(n, "x").rjust(64, "0")


def scan_bytecode() -> dict:
    """Which ABI selectors appear in the implementation's runtime code?"""
    s = settings()
    out: dict = {"address": None, "bytes": 0, "found": [], "missing": []}
    try:
        impl = chain.implementation()
        code = chain.get_code(impl)
    except chain.ChainError as exc:
        out["error"] = str(exc)
        return out
    out["address"] = impl
    out["bytes"] = (len(code) - 2) // 2
    for name, sel in SELECTORS.items():
        (out["found"] if sel[2:] in code else out["missing"]).append(name)
    return out


def try_calls() -> dict:
    """Does any of the read ABI answer on the mining contract or its impl?"""
    s = settings()
    result: dict = {"targets": {}, "attempts": []}
    targets = {"podMining": s.pod_mining}
    try:
        targets["implementation"] = chain.implementation()
    except chain.ChainError:
        pass
    for name, addr in targets.items():
        result["targets"][name] = addr
        for sel_name, sel in SELECTORS.items():
            if sel_name not in ("netlist", "circuitInfo", "nextId"):
                continue
            for task_id in (1, 2, 3):
                data = sel + (_u256(task_id) if sel_name != "nextId" else "")
                try:
                    raw = chain.eth_call(addr, data)
                except chain.ChainError as exc:
                    result["attempts"].append(
                        {"target": name, "selector": sel_name, "task": task_id, "error": str(exc)[:160]}
                    )
                    continue
                result["attempts"].append(
                    {
                        "target": name,
                        "selector": sel_name,
                        "task": task_id,
                        "bytes": (len(raw) - 2) // 2,
                        "head": raw[:98],
                    }
                )
    return result


def try_routes() -> dict:
    s = settings()
    out: dict = {}
    with httpx.Client(timeout=20, follow_redirects=True) as c:
        for route in ROUTES:
            path = route.format(id=1)
            try:
                r = c.get(f"{s.pod_api}{path}")
                body = r.text[:160]
            except Exception as exc:
                out[path] = {"error": str(exc)[:120]}
                continue
            out[path] = {"status": r.status_code, "bytes": len(r.content), "body": body}
    return out


def write_report(bytecode: dict, calls: dict, routes: dict) -> None:
    netlist_ok = [a for a in calls.get("attempts", []) if a.get("selector") == "netlist" and a.get("bytes", 0) >= 32]
    s = settings()
    lines = [
        "# The oracle question, answered",
        "",
        f"Written {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} against X Layer "
        f"(chain {s.chain_id}), mining contract `{s.pod_mining}`, processor `{s.processor}`.",
        "",
        "## The question",
        "",
        "Can we read a **reference implementation** for a PoD task, so that `PASS` can mean",
        "*exhaustive over every input* rather than *all 256 official vectors*?",
        "",
        "## What was checked",
        "",
        "### 1. Does the deployed bytecode carry the TapeOut read ABI?",
        "",
        "The mining contract's runtime code is a small proxy; its EIP-1967 implementation slot points at "
        f"`{bytecode.get('address') or 'unavailable'}` ({bytecode.get('bytes', 0)} bytes of runtime code).",
        "",
        f"- selectors found: {', '.join(bytecode.get('found') or []) or 'none'}",
        f"- selectors missing: {', '.join(bytecode.get('missing') or []) or 'none'}",
        "",
    ]
    if bytecode.get("error"):
        lines += [f"- error: {bytecode['error']}", ""]

    lines += [
        "### 2. Do `netlist(taskId)` / `circuitInfo(taskId)` answer?",
        "",
        "| target | selector | task | result |",
        "|---|---|---|---|",
    ]
    for a in calls.get("attempts", []):
        res = a.get("error") or f"{a.get('bytes')} bytes, head `{a.get('head')}`"
        lines.append(f"| {a.get('target')} | {a.get('selector')} | {a.get('task')} | {res} |")
    lines += [""]

    lines += [
        "### 3. Does the public API serve a reference netlist?",
        "",
        "| route | status | bytes | body head |",
        "|---|---|---|---|",
    ]
    for path, r in routes.items():
        if "error" in r:
            lines.append(f"| `{path}` | — | — | {r['error']} |")
        else:
            body = (r.get("body") or "").replace("|", "\\|").replace("\n", " ")[:90]
            lines.append(f"| `{path}` | {r['status']} | {r['bytes']} | `{body}` |")
    lines += [""]

    verdict = "FOUND" if netlist_ok else "NOT FOUND"
    lines += [
        "## Verdict",
        "",
        f"**Reference netlist: {verdict}.**",
        "",
    ]
    if netlist_ok:
        lines += [
            "A netlist read answered with a decodable payload, so exhaustive comparison is",
            "reachable. `engine._reference_outcome` already implements the exhaustive path;",
            "wire this reader into `podx/chain.py` and pass the result as `reference_hex`.",
            "",
        ]
    else:
        lines += [
            "No tape-out record is readable at a task-keyed address, and no API route serves one.",
            "The product therefore does not claim exhaustive coverage on its own evidence.",
            "`PASS` means:",
            "",
            "> all 256 official vectors match, recomputed offline from the netlist, with the vector",
            "> set's Merkle root folded from its own proofs and compared to the task's published root.",
            "",
            "That is the protocol's operative definition of correct — it is what a challenge is",
            "judged against — and it is the sentence every report prints.",
            "",
            "The exhaustive path stays in the engine because it costs nothing and works the moment a",
            "reference implementation is supplied by hand: the audit form and the JSON API both accept",
            "a `reference` netlist, and the report then says *exhaustive* instead of *all 256*.",
            "",
        ]

    lines += [
        "## Why this is not a blocker",
        "",
        "The vector set is not a weaker claim than it looks. Each leaf is committed by the task",
        "root, and this service reproduces that root from the leaves and proofs rather than",
        "believing the response:",
        "",
        "```",
        "leaf = keccak( keccak( abi.encode(uint256 index, bytes input, bytes expectedOut) ) )",
        "node = keccak( min(a,b) || max(a,b) )",
        "```",
        "",
        f"Checked for three separate tasks (roots reproduced exactly, `tests/test_vectors.py`).",
        "A tampered leaf breaks the agreement, which is how the check earns its keep.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "python -m scripts.probe_oracle        # rewrites this file",
        "python -m pytest tests/test_vectors.py",
        "```",
        "",
"The decoder evidence for this work lives in `probe/` (24 BSC circuits, on-chain `eval`/`step`",
        "agreement), which is what makes the TAP-02 reading trustworthy in the first place.",
        "",
    ]
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines))


def main() -> int:
    print("scanning implementation bytecode…")
    bytecode = scan_bytecode()
    print("  found:", bytecode.get("found"), "missing:", bytecode.get("missing"))
    print("trying read calls…")
    calls = try_calls()
    ok = [a for a in calls["attempts"] if a.get("bytes")]
    print(f"  {len(ok)}/{len(calls['attempts'])} attempts returned data")
    print("trying API routes…")
    routes = try_routes()
    for p, r in routes.items():
        print(f"  {p}: {r.get('status', r.get('error'))}")
    write_report(bytecode, calls, routes)
    print(f"wrote {DOC}")
    print(json.dumps({"bytecode": bytecode.get("found"), "routes": {k: v.get("status") for k, v in routes.items()}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

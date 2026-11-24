#!/usr/bin/env python3
"""Consolidated evidence run: one pinned block, one RPC, strict (no fallback)."""
import json, random
import probe as P

P.RPC = "https://bsc-rpc.publicnode.com"
head = int(P.rpc("eth_blockNumber", []), 16)
B = head - 30
P.BLOCK = B
block = P.rpc("eth_getBlockByNumber", [hex(B), False])
bh = block["hash"]
ts = int(block["timestamp"], 16)

P.log("rpc                :", P.RPC)
P.log("chainId            :", int(P.rpc("eth_chainId", []), 16))
P.log("pinned block       :", B, "hash", bh)
P.log("factory            :", P.FACTORY, "isSealed =", int(P.eth_call(P.FACTORY, P.SEL["isSealed"]), 16),
      "cpuCount =", int(P.eth_call(P.FACTORY, P.SEL["cpuCount"]), 16))


def ev(cpu, cid, inp: bytes):
    data = P.SEL["eval"] + P.u256(cid) + P.u256(0x40) + P.u256(len(inp)) + P.pad32(inp.hex())
    return P.dec_dyn_bytes(P.eth_call(cpu, data))


def stp(cpu, cid, state: bytes, inp: bytes):
    off_in = 0x60 + ((32 + len(state) + 31) // 32) * 32
    data = (P.SEL["step"] + P.u256(cid) + P.u256(0x60) + P.u256(off_in)
            + P.u256(len(state)) + P.pad32(state.hex())
            + P.u256(len(inp)) + P.pad32(inp.hex()))
    b = bytes.fromhex(P.eth_call(cpu, data)[2:])
    o1 = int.from_bytes(b[0:32], "big")
    o2 = int.from_bytes(b[32:64], "big")
    return (b[o1 + 32:o1 + 32 + int.from_bytes(b[o1:o1 + 32], "big")],
            b[o2 + 32:o2 + 32 + int.from_bytes(b[o2:o2 + 32], "big")])


rows = []
targets = [(P.PROC, 3151), (P.PROC, 17462), (P.PROC, 16996)]
for cpu, cid in targets:
    c = P.load(cpu, cid)
    kinds = {}
    for r in c.recs:
        kinds[r[0]] = kinds.get(r[0], 0) + 1
    gates, state = P.closure(c)
    P.log("")
    P.log("== %s #%d" % (cpu, cid))
    P.log("circuitInfo        : nIn=%d nOut=%d nState=%d gateCount=%d" % c.info)
    P.log("netlist            : %d bytes; records=%d %s" % (len(c.nl), len(c.recs), kinds))
    P.log("netlist hex head   :", c.nl.hex()[:64])
    P.log("re-encode identical:", P.encode(c.recs) == c.nl)
    P.log("well-formed        :", "yes" if not c.wellformed() else c.wellformed())
    P.log("my gate/state count: %d / %d vs circuitInfo %d / %d -> %s"
          % (gates, state, c.gateCount, c.nState,
             "MATCH" if (gates, state) == (c.gateCount, c.nState) else "MISMATCH"))
    row = {"processor": cpu, "id": cid, "circuitInfo": list(c.info),
           "netlistBytes": len(c.nl), "records": len(c.recs), "kinds": kinds,
           "reencode": P.encode(c.recs) == c.nl,
           "decodedGates": gates, "decodedState": state}
    if c.nState == 0:
        ok = 0
        for v in range(1 << c.nIn):
            inp = v.to_bytes((c.nIn + 7) // 8, "little")
            mine = P.pack(P.beat(c, [], P.unpack(inp, c.nIn))[1], c.nOut)
            ok += (mine == ev(cpu, cid, inp))
        P.log("eval() truth table : %d/%d identical to my simulator (all %d inputs)"
              % (ok, 1 << c.nIn, 1 << c.nIn))
        P.log("eval(0xff)         :", ev(cpu, cid, b"\xff").hex())
        row["eval"] = {"tested": 1 << c.nIn, "identical": ok}
    else:
        rnd = random.Random(23)
        ok, n = 0, 8
        for _ in range(n):
            sb = bytes(rnd.getrandbits(8) for _ in range((c.nState + 7) // 8))
            ib = bytes(rnd.getrandbits(8) for _ in range((c.nIn + 7) // 8))
            nst, out = P.beat(c, P.unpack(sb, c.nState), P.unpack(ib, c.nIn))
            ok += ((P.pack(nst, c.nState), P.pack(out, c.nOut)) == stp(cpu, cid, sb, ib))
        P.log("step() random beats: %d/%d identical to my simulator" % (ok, n))
        row["step"] = {"tested": n, "identical": ok}
    rows.append(row)

with open("probe/final.json", "w") as f:
    json.dump({"block": B, "blockHash": bh, "timestamp": ts, "rpc": P.RPC,
               "processor": P.PROC, "circuits": rows}, f, indent=1)
P.log("")
P.log("eth_calls made     :", P.CALLS)

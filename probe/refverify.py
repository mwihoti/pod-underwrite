#!/usr/bin/env python3
"""Verify REF-bearing circuits found by refscan: closure, pin match, beats."""
import json, random
import probe as P

P.RPC = "https://bsc-rpc.publicnode.com"
head = int(P.rpc("eth_blockNumber", []), 16)
B = head - 30
P.BLOCK = B
block = P.rpc("eth_getBlockByNumber", [hex(B), False])
P.log("rpc                :", P.RPC)
P.log("pinned block       :", B, "hash", block["hash"])

HITS = [("0xb75c36c7f0fc9409613947ad700d629e82ad59e5", 100),
        ("0xb3e0b87088d3b385f148c181de2832dba5568817", 13)]

rows = []
for cpu, cid in HITS:
    c = P.load(cpu, cid)
    kinds = {}
    for r in c.recs:
        kinds[r[0]] = kinds.get(r[0], 0) + 1
    gates, state = P.closure(c)
    P.log("")
    P.log("== %s #%d" % (cpu, cid))
    P.log("circuitInfo        : nIn=%d nOut=%d nState=%d gateCount=%d" % c.info)
    P.log("netlist            : %d bytes; %s" % (len(c.nl), kinds))
    P.log("netlist hex        :", c.nl.hex())
    P.log("re-encode identical:", P.encode(c.recs) == c.nl)
    P.log("my gate/state count: %d / %d vs circuitInfo %d / %d -> %s"
          % (gates, state, c.gateCount, c.nState,
             "MATCH" if (gates, state) == (c.gateCount, c.nState) else "MISMATCH"))
    for r in c.recs:
        if r[0] == "REF":
            sub = P.load(r[1], r[2])
            P.log("REF -> %s #%d : nIn=%d nOut=%d nState=%d gateCount=%d pins_ok=%s"
                  % (r[1], r[2], sub.info[0], sub.info[1], sub.info[2], sub.info[3],
                     (sub.info[0], sub.info[1]) == (r[3], r[4])))
            P.log("     sub netlist bytes=%d state=%d refs_in_sub=%d"
                  % (len(sub.nl), sub.info[2], sum(1 for x in sub.recs if x[0] == "REF")))
    rnd = random.Random(31)
    ok, n = 0, 8
    for _ in range(n):
        sb = bytes(rnd.getrandbits(8) for _ in range((c.nState + 7) // 8))
        ib = bytes(rnd.getrandbits(8) for _ in range((c.nIn + 7) // 8))
        nst, out = P.beat(c, P.unpack(sb, c.nState), P.unpack(ib, c.nIn))
        data_ok = (P.pack(nst, c.nState), P.pack(out, c.nOut)) == P.st(cpu, cid, sb, ib)
        ok += data_ok
        if n == 8:
            P.log("sample step        : state=%s in=%s -> mine=(%s,%s)"
                  % (sb.hex(), ib.hex(), P.pack(nst, c.nState).hex(), P.pack(out, c.nOut).hex()))
    P.log("step() random beats: %d/%d identical to my simulator" % (ok, n))
    rows.append({"cpu": cpu, "id": cid, "circuitInfo": list(c.info), "netlistHex": c.nl.hex(),
                 "kinds": kinds, "closure": {"gates": gates, "state": state},
                 "step": {"tested": n, "identical": ok}})

with open("probe/refverify.json", "w") as f:
    json.dump({"block": B, "blockHash": block["hash"], "circuits": rows}, f, indent=1)

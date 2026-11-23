#!/usr/bin/env python3
"""Find and verify a live circuit with latches (and REF), using probe.py's decoder."""
import json, random, sys
import probe as P

head = int(P.rpc("eth_blockNumber", []), 16)
P.BLOCK = head - 5
P.log("scan head block    :", head, "| pinned:", P.BLOCK)
k = P.next_id(P.PROC)
P.log("scanning ids 1..%d of %s" % (k - 1, P.PROC))

chosen = []
seen = 0
for i in range(60):
    cid = (k - 1) - i * 233
    if cid < 1:
        break
    info = P.circuit_info(P.PROC, cid)
    seen += 1
    nIn, nOut, nState, gc = info
    tag = "state" if nState else ("gates" if gc > 200 else "")
    if nState and gc >= 20:
        chosen.append(cid)
        P.log("candidate %6d: nIn=%d nOut=%d nState=%d gateCount=%d" % (cid, nIn, nOut, nState, gc))
        if len(chosen) >= 2:
            break
P.log("circuits probed    :", seen)

for cid in chosen:
    c = P.load(P.PROC, cid)
    kinds = {}
    for r in c.recs:
        kinds[r[0]] = kinds.get(r[0], 0) + 1
    gates, state = P.closure(c)
    P.log("")
    P.log("== circuit %d" % cid)
    P.log("circuitInfo        : nIn=%d nOut=%d nState=%d gateCount=%d" % c.info)
    P.log("netlist bytes      :", len(c.nl))
    P.log("decoded records    :", len(c.recs), kinds)
    P.log("re-encode match    :", P.encode(c.recs) == c.nl)
    P.log("well-formed        :", "yes" if not c.wellformed() else c.wellformed())
    P.log("REF closure        : gates=%d state=%d vs circuitInfo %d / %d -> %s"
          % (gates, state, c.gateCount, c.nState,
             "MATCH" if (gates, state) == (c.gateCount, c.nState) else "MISMATCH"))
    if any(r[0] == "REF" for r in c.recs):
        for r in c.recs:
            if r[0] == "REF":
                sub = P.load(r[1], r[2])
                P.log("REF -> %s #%d : nIn=%d nOut=%d nState=%d gateCount=%d  (pins %d/%d ok=%s)"
                      % (r[1], r[2], sub.info[0], sub.info[1], sub.info[2], sub.info[3],
                         sub.info[0], sub.info[1], (sub.info[0], sub.info[1]) == (r[3], r[4])))
    rnd = random.Random(11)
    ok, trials = 0, 8
    for _ in range(trials):
        sb = bytes(rnd.getrandbits(8) for _ in range((c.nState + 7) // 8))
        ib = bytes(rnd.getrandbits(8) for _ in range((c.nIn + 7) // 8))
        nst, out = P.beat(c, P.unpack(sb, c.nState), P.unpack(ib, c.nIn))
        chain = P.st(P.PROC, cid, sb, ib)
        ok += ((P.pack(nst, c.nState), P.pack(out, c.nOut)) == chain)
    P.log("step() %d random beats: %d/%d identical to local simulation" % (trials, ok, trials))
    P.log("sample beat        : state=%s in=%s -> local newState=%s out=%s"
          % (sb.hex(), ib.hex(), P.pack(nst, c.nState).hex(), P.pack(out, c.nOut).hex()))

with open("probe/scan.json", "w") as f:
    json.dump({"head": head, "pinned": P.BLOCK, "processor": P.PROC, "ids": chosen}, f, indent=1)

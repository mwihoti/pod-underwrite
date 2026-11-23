#!/usr/bin/env python3
"""Find a live circuit that uses REF, then verify its closure and beats."""
import json
import probe as P

head = int(P.rpc("eth_blockNumber", []), 16)
P.BLOCK = head - 5
P.log("scan2 head block   :", head, "| pinned:", P.BLOCK)
k = P.next_id(P.PROC)
P.log("scanning ids 1..%d of %s" % (k - 1, P.PROC))

found, probed = [], 0
for i in range(45):
    cid = (k - 1) - i * 431
    if cid < 1:
        break
    try:
        info = P.circuit_info(P.PROC, cid)
        nl = P.netlist(P.PROC, cid)
        recs = P.decode(nl)
    except Exception as e:
        P.log("  skip %d: %s" % (cid, e))
        continue
    probed += 1
    nref = sum(1 for r in recs if r[0] == "REF")
    if nref:
        found.append(cid)
        P.log("candidate %6d: nIn=%d nOut=%d nState=%d gateCount=%d netlist=%dB refs=%d"
              % (cid, info[0], info[1], info[2], info[3], len(nl), nref))
        if len(found) >= 2:
            break
P.log("netlists probed    :", probed, "| with REF:", found)

for cid in found:
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
    P.log("ref-closure gates  : %d (own %d) vs circuitInfo %d -> %s"
          % (gates, c.own_gates, c.gateCount, "MATCH" if gates == c.gateCount else "MISMATCH"))
    P.log("ref-closure state  : %d (own %d) vs circuitInfo %d -> %s"
          % (state, c.own_state, c.nState, "MATCH" if state == c.nState else "MISMATCH"))
    for r in c.recs:
        if r[0] == "REF":
            sub = P.load(r[1], r[2])
            P.log("REF -> %s #%d : nIn=%d nOut=%d nState=%d gateCount=%d pins_ok=%s"
                  % (r[1], r[2], sub.info[0], sub.info[1], sub.info[2], sub.info[3],
                     (sub.info[0], sub.info[1]) == (r[3], r[4])))
    import random
    rnd = random.Random(19)
    ok, trials = 0, 8
    for _ in range(trials):
        sb = bytes(rnd.getrandbits(8) for _ in range((c.nState + 7) // 8))
        ib = bytes(rnd.getrandbits(8) for _ in range((c.nIn + 7) // 8))
        nst, out = P.beat(c, P.unpack(sb, c.nState), P.unpack(ib, c.nIn))
        chain = P.st(P.PROC, cid, sb, ib)
        ok += ((P.pack(nst, c.nState), P.pack(out, c.nOut)) == chain)
    P.log("step() %d random beats: %d/%d identical to local simulation" % (trials, ok, trials))

with open("probe/scan2.json", "w") as f:
    json.dump({"head": head, "pinned": P.BLOCK, "with_ref": found}, f, indent=1)

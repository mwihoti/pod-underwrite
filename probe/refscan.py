#!/usr/bin/env python3
"""Bounded search for any live circuit whose netlist contains a REF record."""
import json
import probe as P

head = int(P.rpc("eth_blockNumber", []), 16)
P.BLOCK = head - 5
total = int(P.eth_read(P.FACTORY, P.SEL["cpuCount"]), 16)
P.log("refscan head       :", head, "| factory cpuCount:", total)

hits, checked, netlists = [], 0, 0
for i in range(0, min(total, 60)):
    cpu = "0x" + P.eth_read(P.FACTORY, P.SEL["cpuAt"] + P.u256(i))[-40:]
    checked += 1
    try:
        k = P.next_id(cpu)
        if k <= 1:
            continue
        nl = P.netlist(cpu, k - 1)
        recs = P.decode(nl)
        netlists += 1
    except Exception as e:
        P.log("  skip cpu %s: %s" % (cpu, e))
        continue
    nref = sum(1 for r in recs if r[0] == "REF")
    if nref:
        hits.append({"cpu": cpu, "id": k - 1, "refs": nref})
        P.log("REF FOUND  cpu=%s id=%d refs=%d gates_records=%d" % (cpu, k - 1, nref, len(recs)))
        if len(hits) >= 2:
            break
P.log("processors checked :", checked, "| latest netlists decoded:", netlists, "| REF hits:", len(hits))
with open("probe/refscan.json", "w") as f:
    json.dump({"head": head, "checked": checked, "netlists": netlists, "hits": hits}, f, indent=1)

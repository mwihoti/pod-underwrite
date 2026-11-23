#!/usr/bin/env python3
"""Independent probe of a live TapeOut processor contract on BNB Smart Chain.

Decodes netlist() using only TAP-02 section 2, then checks the decode against
circuitInfo() and against on-chain eval()/step() results. No project deps.
"""
import json, os, random, sys, time, urllib.request

RPC = "https://bsc-dataseed.binance.org"
PROC = "0xb1024b89886B9a34Aa4ff5F31C411D708b20a14C"   # "TapeOut" processor
FACTORY = "0x68224F668083c29e9800Be2a646d42d18cedF7e2"  # UUPS proxy

SEL = {
    "nextId": "0x61b8ce8c", "circuitInfo": "0x084d60f1", "netlist": "0x3fc4be56",
    "eval": "0x934d06ea", "step": "0xe8281a1a",
    "cpuCount": "0xa94da8a7", "cpuAt": "0x4bc7cbbd", "isSealed": "0x631f9852",
}
BLOCK = None
EVAL_BLOCK = None
FALLBACK = []
CALLS = 0
OUT = []


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    OUT.append(line)
    with open("probe/evidence.log", "w") as f:
        f.write("\n".join(OUT) + "\n")


def rpc(method, params, tries=3):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                j = json.load(r)
            if "error" in j:            # node answered: retrying the same call is pointless
                raise RuntimeError(str(j["error"]))
            return j["result"]
        except RuntimeError:
            raise
        except Exception as e:          # transient network/HTTP error only
            last = e
            time.sleep(1.0)
    raise last


def blk_param():
    return "latest" if BLOCK == "latest" else hex(BLOCK)


def eth_call(to, data, block=None):
    global CALLS
    CALLS += 1
    return rpc("eth_call", [{"to": to, "data": data}, blk_param() if block is None else block])


def eth_read(to, data):
    """Pinned read; if the node has pruned that state, switch the whole session to latest."""
    global BLOCK, CALLS
    try:
        return eth_call(to, data)
    except RuntimeError as e:
        if BLOCK == "latest" or ("missing trie node" not in str(e) and "header not found" not in str(e)):
            raise
        head = int(rpc("eth_blockNumber", []), 16)
        FALLBACK.append("pinned block %s pruned; switched to latest (head %d)" % (BLOCK, head))
        log("!! node pruned block %s -> continuing at latest (head %d)" % (BLOCK, head))
        BLOCK = "latest"
        return eth_call(to, data)


def eth_call_eval(to, data):
    """Evaluation calls: pin when the node still has the state, else fall back to latest.

    bsc-dataseed keeps recent state only; the read-only structs are pinned, the
    evaluator calls follow whatever the node will serve (recorded in the log)."""
    global CALLS, EVAL_BLOCK
    if EVAL_BLOCK == "latest" or BLOCK == "latest":
        CALLS += 1
        return rpc("eth_call", [{"to": to, "data": data}, "latest"])
    try:
        return eth_call(to, data)
    except RuntimeError as e:
        if "missing trie node" not in str(e) and "header not found" not in str(e):
            raise
        CALLS += 1
        EVAL_BLOCK = "latest"
        return rpc("eth_call", [{"to": to, "data": data}, "latest"])


def u256(n):
    return format(n, "x").rjust(64, "0")


def pad32(hexstr):
    if len(hexstr) % 2:
        hexstr += "0"
    return hexstr + "0" * ((64 - len(hexstr) % 64) % 64)


def dec_dyn_bytes(hexstr):
    b = bytes.fromhex(hexstr[2:])
    off = int.from_bytes(b[0:32], "big")
    ln = int.from_bytes(b[off:off + 32], "big")
    return b[off + 32:off + 32 + ln]


# ---------------------------------------------------------------- contract reads
def next_id(cpu):
    return int(eth_read(cpu, SEL["nextId"]), 16)


def circuit_info(cpu, cid):
    h = eth_read(cpu, SEL["circuitInfo"] + u256(cid))
    return tuple(int.from_bytes(bytes.fromhex(h[2:])[i:i + 32], "big") for i in range(0, 128, 32))


def netlist(cpu, cid):
    return dec_dyn_bytes(eth_read(cpu, SEL["netlist"] + u256(cid)))


def ev(cpu, cid, inputs: bytes):
    data = SEL["eval"] + u256(cid) + u256(0x40) + u256(len(inputs)) + pad32(inputs.hex())
    return dec_dyn_bytes(eth_call_eval(cpu, data))


def st(cpu, cid, state: bytes, inputs: bytes):
    off_in = 0x60 + ((32 + len(state) + 31) // 32) * 32
    data = (SEL["step"] + u256(cid) + u256(0x60) + u256(off_in)
            + u256(len(state)) + pad32(state.hex())
            + u256(len(inputs)) + pad32(inputs.hex()))
    b = bytes.fromhex(eth_call_eval(cpu, data)[2:])
    o1 = int.from_bytes(b[0:32], "big")
    o2 = int.from_bytes(b[32:64], "big")
    l1 = int.from_bytes(b[o1:o1 + 32], "big")
    l2 = int.from_bytes(b[o2:o2 + 32], "big")
    return b[o1 + 32:o1 + 32 + l1], b[o2 + 32:o2 + 32 + l2]


# ---------------------------------------------------------------- TAP-02 decoder
def decode(nl: bytes):
    """Return list of records per TAP-02 section 2. Raises on a malformed stream."""
    recs, o = [], 0
    while o < len(nl):
        op = nl[o]
        if op == 0x00:
            if o + 7 > len(nl):
                raise ValueError("truncated NAND at %d" % o)
            recs.append(("NAND", int.from_bytes(nl[o + 1:o + 4], "big"),
                         int.from_bytes(nl[o + 4:o + 7], "big")))
            o += 7
        elif op == 0x01:
            if o + 4 > len(nl):
                raise ValueError("truncated LATCH at %d" % o)
            recs.append(("LATCH", int.from_bytes(nl[o + 1:o + 4], "big")))
            o += 4
        elif op == 0x02:
            if o + 31 > len(nl):
                raise ValueError("truncated REF at %d" % o)
            cpu = "0x" + nl[o + 1:o + 21].hex()
            cid = int.from_bytes(nl[o + 21:o + 29], "big")
            nins, nouts = nl[o + 29], nl[o + 30]
            if o + 31 + 3 * nins > len(nl):
                raise ValueError("truncated REF ins at %d" % o)
            ins = [int.from_bytes(nl[o + 31 + 3 * i:o + 34 + 3 * i], "big") for i in range(nins)]
            recs.append(("REF", cpu, cid, nins, nouts, ins))
            o += 31 + 3 * nins
        else:
            raise ValueError("bad opcode 0x%02x at %d" % (op, o))
    return recs


def encode(recs):
    out = bytearray()
    for r in recs:
        if r[0] == "NAND":
            out += b"\x00" + r[1].to_bytes(3, "big") + r[2].to_bytes(3, "big")
        elif r[0] == "LATCH":
            out += b"\x01" + r[1].to_bytes(3, "big")
        else:
            _, cpu, cid, nins, nouts, ins = r
            out += b"\x02" + bytes.fromhex(cpu[2:]) + cid.to_bytes(8, "big") + bytes([nins, nouts])
            for i in ins:
                out += i.to_bytes(3, "big")
    return bytes(out)


class Circuit:
    def __init__(self, cpu, cid, info, nl, recs):
        self.cpu, self.cid, self.info, self.nl, self.recs = cpu, cid, info, nl, recs
        self.nIn, self.nOut, self.nState, self.gateCount = info
        # own records' gate count (REF'd gates come from the sub-circuit's gateCount)
        self.own_gates = sum(1 for r in recs if r[0] in ("NAND", "LATCH"))
        self.own_state = sum(1 for r in recs if r[0] == "LATCH")
        self.produced = sum(1 if r[0] in ("NAND", "LATCH") else r[4] for r in recs)

    def wellformed(self):
        problems = []
        if not (0 <= self.nIn <= 65536) or not (1 <= self.nOut <= 65536):
            problems.append("pin count")
        if 2 + self.nIn + self.nOut > 2 + self.nIn + self.produced:
            problems.append("too few signals for outputs")
        first = 2 + self.nIn
        for k, r in enumerate(self.recs):
            if r[0] == "NAND":
                if r[1] > first - 1 or r[2] > first - 1:
                    problems.append("record %d NAND future signal" % k)
                first += 1
            elif r[0] == "LATCH":
                if r[1] >= 2 + self.nIn + self.produced:
                    problems.append("record %d LATCH d out of range" % k)
                first += 1
            else:
                if any(i > first - 1 for i in r[5]):
                    problems.append("record %d REF future signal" % k)
                first += r[4]
        return problems


CACHE = {}


def load(cpu, cid):
    key = (cpu.lower(), cid)
    if key not in CACHE:
        info = circuit_info(cpu, cid)
        nl = netlist(cpu, cid)
        recs = decode(nl)
        CACHE[key] = Circuit(cpu, cid, info, nl, recs)
    return CACHE[key]


def closure(c: Circuit):
    """Recursive gate/state totals including REF sub-circuits (TAP-02 section 6)."""
    gates, state = c.own_gates, c.own_state
    subs = []
    for r in c.recs:
        if r[0] == "REF":
            sub = load(r[1], r[2])
            sg, ss = closure(sub)
            gates += sg
            state += ss
            subs.append((r[1], r[2], sg, ss, sub.info[0], sub.info[1]))
    return gates, state


def beat(c: Circuit, state, inputs):
    """One beat per TAP-02 section 4. Returns (newState bits, output bits).

    A LATCH's d may point forward (section 3 condition 5), so its new-state bit is
    the value of d *after* every element has been evaluated: record it, fill it in last.
    """
    sig = [0, 1] + list(inputs)
    slots, cur = [], 0          # slots in record order = newState layout
    for r in c.recs:
        if r[0] == "NAND":
            sig.append(1 - (sig[r[1]] & sig[r[2]]))
        elif r[0] == "LATCH":
            sig.append(state[cur])       # output is the previous beat's stored bit
            cur += 1
            slots.append(("latch", r[1]))
        else:
            _, cpu, cid, nins, nouts, ins = r
            sub = load(cpu, cid)
            sn = sub.nState
            sub_new, sub_out = beat(sub, state[cur:cur + sn], [sig[i] for i in ins])
            cur += sn
            sig.extend(sub_out)
            slots.append(("ref", sub_new))
    new_state = []
    for kind, v in slots:
        if kind == "latch":
            new_state.append(sig[v])
        else:
            new_state.extend(v)
    return new_state, sig[len(sig) - c.nOut:]


def pack(bits, n):
    b = bytearray((n + 7) // 8)
    for i, v in enumerate(bits):
        if v:
            b[i >> 3] |= 1 << (i & 7)
    return bytes(b)


def unpack(buf, n):
    return [(buf[i >> 3] >> (i & 7)) & 1 if (i >> 3) < len(buf) else 0 for i in range(n)]


def main():
    global BLOCK
    head = int(rpc("eth_blockNumber", []), 16)
    BLOCK = head - 5                      # pin a settled block; public nodes prune ~128
    log("rpc                :", RPC)
    log("chainId            :", int(rpc("eth_chainId", []), 16))
    log("head block         :", head)
    log("pinned block       :", BLOCK, "(circuitInfo/netlist reads)")
    log("processor          :", PROC)
    log("factory isSealed() :", int(eth_read(FACTORY, SEL["isSealed"]), 16))
    log("factory cpuCount() :", int(eth_read(FACTORY, SEL["cpuCount"]), 16))

    k = next_id(PROC)
    log("nextId()           :", k, "(so live circuit ids are 1..%d)" % (k - 1))

    targets = [3151, k - 1] if k - 1 != 3151 else [3151]
    report = {"rpc": RPC, "chainId": 56, "block": BLOCK, "processor": PROC,
              "nextId": k, "circuits": []}

    for cid in targets:
        c = load(PROC, cid)
        recs = c.recs
        log("")
        log("== circuit %d of %s" % (cid, PROC))
        log("circuitInfo        : nIn=%d nOut=%d nState=%d gateCount=%d" % c.info)
        log("netlist bytes      :", len(c.nl), "hex:", c.nl.hex()[:80] + ("..." if len(c.nl) > 40 else ""))
        kinds = {}
        for r in recs:
            kinds[r[0]] = kinds.get(r[0], 0) + 1
        log("decoded records    :", len(recs), kinds, "own gates:", c.own_gates)
        log("re-encode match    :", encode(recs) == c.nl)
        log("well-formed        :", "yes" if not c.wellformed() else c.wellformed())
        gates, state = closure(c)
        log("REF-closure totals : gateCount=%d nState=%d (circuitInfo: %d / %d) -> %s"
            % (gates, state, c.gateCount, c.nState,
               "MATCH" if (gates, state) == (c.gateCount, c.nState) else "MISMATCH"))

        entry = {"id": cid, "circuitInfo": list(c.info), "netlistBytes": len(c.nl),
                 "netlistHex": c.nl.hex(), "records": len(recs), "kinds": kinds,
                 "reencode": encode(recs) == c.nl,
                 "wellformed": c.wellformed(),
                 "closure": {"gates": gates, "state": state}}

        if c.nState == 0:
            ok, total = 0, 0
            z = bytes((c.nIn + 7) // 8)
            for v in range(1 << c.nIn):
                inp = v.to_bytes(len(z), "little")
                mine = pack(beat(c, [], unpack(inp, c.nIn))[1], c.nOut)
                chain = ev(PROC, cid, inp)
                total += 1
                ok += (mine == chain)
            log("eval() all %d inputs : %d/%d identical to local simulation" % (total, ok, total))
            entry["eval"] = {"tested": total, "identical": ok}
            if c.nIn == 8:
                log("note               : outputs are popcount(inputs) LSB-first, e.g. in=0xff -> %s"
                    % ev(PROC, cid, b"\xff").hex())
        else:
            rnd = random.Random(7)
            ok = 0
            trials = 8
            for _ in range(trials):
                sb = bytes(rnd.getrandbits(8) for _ in range((c.nState + 7) // 8))
                ib = bytes(rnd.getrandbits(8) for _ in range((c.nIn + 7) // 8))
                nst, out = beat(c, unpack(sb, c.nState), unpack(ib, c.nIn))
                mine = (pack(nst, c.nState), pack(out, c.nOut))
                chain = st(PROC, cid, sb, ib)
                ok += (mine == chain)
            log("step() %d random beats: %d/%d identical to local simulation" % (trials, ok, trials))
            entry["step"] = {"tested": trials, "identical": ok}

        report["circuits"].append(entry)

    with open("probe/evidence.json", "w") as f:
        json.dump(report, f, indent=1)
    with open("probe/evidence.log", "w") as f:
        f.write("\n".join(OUT) + "\n")
    log("")
    log("state view used    :", "latest" if BLOCK == "latest" else "pinned block %d" % BLOCK)
    log("eval/step block    :", EVAL_BLOCK or BLOCK)
    log("eth_calls made     :", CALLS)


if __name__ == "__main__":
    main()

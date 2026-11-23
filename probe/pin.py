#!/usr/bin/env python3
"""Find a public BSC RPC that will serve one pinned block for reads *and* eval."""
import probe as P

RPCS = ["https://bsc-dataseed.binance.org", "https://bsc-dataseed1.defibit.io",
        "https://bsc-dataseed1.ninicoin.io", "https://bsc-rpc.publicnode.com",
        "https://bsc.drpc.org", "https://1rpc.io/bnb", "https://binance.llamarpc.com",
        "https://bsc-mainnet.public.blastapi.io", "https://bsc.meowrpc.com"]

head = int(P.rpc("eth_blockNumber", []), 16)
for behind in (30, 300):
    B = head - behind
    for u in RPCS:
        P.RPC, P.BLOCK, P.EVAL_BLOCK = u, B, None
        try:
            sealed = int(P.eth_call(P.FACTORY, P.SEL["isSealed"]), 16)
            info = P.circuit_info(P.PROC, 3151)
            nl = P.netlist(P.PROC, 3151)
            out = P.dec_dyn_bytes(P.eth_call(
                P.PROC, P.SEL["eval"] + P.u256(3151) + P.u256(0x40) + P.u256(1) + P.pad32("ff")))
            print("PIN OK  behind=%d %-40s block=%d info=%s netlist=%dB eval(0xff)=%s sealed=%d"
                  % (behind, u, B, info, len(nl), out.hex(), sealed), flush=True)
        except Exception as e:
            print("PIN BAD behind=%d %-40s %s" % (behind, u, e), flush=True)

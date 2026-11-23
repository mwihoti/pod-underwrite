# The oracle question, answered

Written 2026-11-24 16:47 UTC against X Layer (chain 196), mining contract `0x4F6A2312D80753BdAFd35DDB1Bf4BB14C43F8265`, processor `0x87BFCa3494Aa228fF589ab9B642143A5e8c8F9a6`.

## The question

Can we read a **reference implementation** for a PoD task, so that `PASS` can mean
*exhaustive over every input* rather than *all 256 official vectors*?

## What was checked

### 1. Does the deployed bytecode carry the TapeOut read ABI?

The mining contract's runtime code is a small proxy; its EIP-1967 implementation slot points at `0x90681776c2d6be4676d83fa8fef22836b5dbbeeb` (24505 bytes of runtime code).

- selectors found: circuitInfo
- selectors missing: nextId, netlist, eval, step

### 2. Do `netlist(taskId)` / `circuitInfo(taskId)` answer?

| target | selector | task | result |
|---|---|---|---|
| podMining | nextId | 1 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| podMining | nextId | 2 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| podMining | nextId | 3 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| podMining | circuitInfo | 1 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| podMining | circuitInfo | 2 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| podMining | circuitInfo | 3 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| podMining | netlist | 1 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| podMining | netlist | 2 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| podMining | netlist | 3 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| implementation | nextId | 1 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| implementation | nextId | 2 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| implementation | nextId | 3 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| implementation | circuitInfo | 1 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| implementation | circuitInfo | 2 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| implementation | circuitInfo | 3 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| implementation | netlist | 1 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| implementation | netlist | 2 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |
| implementation | netlist | 3 | all X Layer nodes failed for eth_call: https://xlayer.drpc.org: execution reverted |

### 3. Does the public API serve a reference netlist?

| route | status | bytes | body head |
|---|---|---|---|
| `/v1/pod/tasks/1/netlist` | 404 | 233 | `{"type":"urn:tapeout:problem:NOT_FOUND","title":"Not Found","status":404,"detail":"No rout` |
| `/v1/pod/tasks/1/reference` | 404 | 237 | `{"type":"urn:tapeout:problem:NOT_FOUND","title":"Not Found","status":404,"detail":"No rout` |
| `/v1/pod/tasks/1/design` | 404 | 231 | `{"type":"urn:tapeout:problem:NOT_FOUND","title":"Not Found","status":404,"detail":"No rout` |
| `/v1/pod/tasks/1` | 404 | 217 | `{"type":"urn:tapeout:problem:NOT_FOUND","title":"Not Found","status":404,"detail":"No rout` |
| `/v1/pod/netlist?taskId=1` | 404 | 226 | `{"type":"urn:tapeout:problem:NOT_FOUND","title":"Not Found","status":404,"detail":"No rout` |
| `/v1/pod/circuits?chainId=196` | 404 | 231 | `{"type":"urn:tapeout:problem:NOT_FOUND","title":"Not Found","status":404,"detail":"No rout` |

## Verdict

**Reference netlist: NOT FOUND.**

No tape-out record is readable at a task-keyed address, and no API route serves one.
The product therefore does not claim exhaustive coverage on its own evidence.
`PASS` means:

> all 256 official vectors match, recomputed offline from the netlist, with the vector
> set's Merkle root folded from its own proofs and compared to the task's published root.

That is the protocol's operative definition of correct — it is what a challenge is
judged against — and it is the sentence every report prints.

The exhaustive path stays in the engine because it costs nothing and works the moment a
reference implementation is supplied by hand: the audit form and the JSON API both accept
a `reference` netlist, and the report then says *exhaustive* instead of *all 256*.

## Why this is not a blocker

The vector set is not a weaker claim than it looks. Each leaf is committed by the task
root, and this service reproduces that root from the leaves and proofs rather than
believing the response:

```
leaf = keccak( keccak( abi.encode(uint256 index, bytes input, bytes expectedOut) ) )
node = keccak( min(a,b) || max(a,b) )
```

Checked for three separate tasks (roots reproduced exactly, `tests/test_vectors.py`).
A tampered leaf breaks the agreement, which is how the check earns its keep.

## Reproduction

```bash
python -m scripts.probe_oracle        # rewrites this file
python -m pytest tests/test_vectors.py
```

The decoder evidence for this work lives in `probe/` (24 BSC circuits, on-chain `eval`/`step`
agreement), which is what makes the TAP-02 reading trustworthy in the first place.

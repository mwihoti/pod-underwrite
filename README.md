# PoD Underwrite

Pre-purchase correctness audits for PoD circuits on X Layer.

Paste a TAP-02 netlist and its task id; get a verdict, the exact input that breaks it if one
exists, the counterexample, the cost arithmetic that decides whether it can ever win the seat,
and the provenance (block, block hash, implementation address, vector root, netlist hash).

Live app: `podx.web:app`. Everything below is what it actually does, including the parts it
refuses to claim.

---

## What a verdict means

| Verdict | Meaning |
|---|---|
| `PASS` | All 256 official vectors match, recomputed offline from the netlist, with the vector set's Merkle root folded from its own proofs and compared to the task's published root. |
| `VECTOR-MISMATCH` | At least one official vector disagrees. The report carries the lowest failing input and both outputs — the value a challenger would use. |
| `BEST-FIRST-AT-RISK` | Correct, but `C` is above the seat's best, so it mines at a reduced (often zero) bonus. |
| `TRIVIAL-TASK` | Correct and commercially meaningless: every output bit is a constant or a direct input copy, so nothing on this task can be challenged. |
| `SHAPE-MISMATCH` | Decodes cleanly but cannot be scored for this task (e.g. latches on a combinational task, where `eval()` refuses latched circuits). |
| `INVALID` | Not canonical TAP-02: decode failure, re-encode mismatch, or a structural violation. First failing rule is printed. |
| `GATE-LIMIT` | Outside the PoD tape-out bounds (or contains `REF`, which PoD forbids). No verdict computed. |
| `INCONCLUSIVE` | The engine could not form an opinion. Sequential tasks land here when a sampled run diverges, because the frame schedule is not published. |
| `UNRESOLVED` | The task or the vector set could not be resolved. |

The words *safe*, *verified* and *guaranteed* are banned from the report on purpose. Coverage is
printed, never implied.

---

## How correctness is established

1. **Independent decoder.** TAP-02 records (`NAND`, `LATCH`, `REF`) are decoded, re-encoded
   byte-for-byte, and checked structurally, including the LATCH-forward rule where a latch's `d`
   points at a signal produced later in the stream. The decoder was validated against a live
   TapeOut deployment on BNB Chain: 24 circuits decoded, re-encoded, and matched on-chain
   `eval`/`step` (256/256 on an 8-input circuit, 8/8 beats on sequential ones). See `probe/`.

2. **The vector set is verified, not trusted.** Each task publishes 256 leaves and a root. This
   service rebuilds every leaf and folds every proof:

   ```
   leaf = keccak( keccak( abi.encode(uint256 index, bytes input, bytes expectedOut) ) )
   node = keccak( min(a,b) || max(a,b) )
   ```

   All 256 folds must agree on the published root before the vectors are used. The scheme is
   unusual (double-hashed leaf, commutative pair hash) and was derived by experiment; three
   separate task roots reproduce exactly. A tampered leaf breaks the agreement, which is the
   check earning its keep.

3. **Simulator against the protocol.** A two-NAND AND on task 3 costs `16` here
   (`A·max(d,1)³` with `A = nand + 7·latch`) and the protocol's own verifier returns `16` too.
   Where that verifier answers, the report shows both numbers side by side.

4. **It does not claim exhaustive coverage.** `docs/oracle.md` records the attempt to find a
   reference implementation that would answer for *any* input: no `netlist`, `eval` or `step`
   selector exists on the deployed implementation, and no API route serves one. Supplying a
   reference netlist by hand turns the check exhaustive — the engine enumerates every input and
   the report says so explicitly.

---

## Run it

```bash
make venv          # python3 -m venv .venv && pip install -e ".[dev]"
make check         # 37 tests, all offline against bundled fixtures
make run           # uvicorn on :8000
```

Then open <http://localhost:8000> and click a sample, or paste a netlist.

```bash
# batch audits into the live database
python -m scripts.audit_many --samples
python -m scripts.audit_many --task 3 --netlist 0x00000002000003…
python -m scripts.audit_many --dir netlists/     # files starting with "# <task id>"

# the oracle experiment (rewrites docs/oracle.md)
python -m scripts.probe_oracle
```

## HTTP surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | paste box, samples, recent audits |
| `POST` | `/audit` | form → 303 to the report |
| `GET` | `/a/{id}` | human report |
| `GET` | `/a/{id}.json` | the same facts, machine-readable |
| `GET` | `/badge/{id}.svg` | embeddable badge (verdict, `C` vs seat) |
| `GET` | `/badge/{processor}/{circuit}.svg` | latest report for a circuit |
| `GET` | `/api/tasks.json` | the 100-task index |
| `POST` | `/api/audit` | `{task_id, netlist, circuit_id?, reference?}` → verdict JSON |
| `GET` | `/stats` | verdict distribution, watcher events |
| `GET` | `/healthz` | DB writable, head lag, watcher age |
| `POST` | `/tg/{secret}` | Telegram webhook (`<task id> <netlist>`, `/sample inverter`) |
| `POST` | `/admin/fulfil` | token-guarded payment bookkeeping |

## Layout

```
podx/
  netlist.py   decode/encode/wellformed/depth/cost/beat/bit-parallel truth tables
  hashing.py   keccak + the empirically-derived Merkle proof scheme
  vectors.py   256 vectors per task, verified against the published root
  tasks.py     task index with disk cache and a bundled snapshot
  chain.py     X Layer JSON-RPC, failover, pinned block, EIP-1967 slot
  engine.py    the audit ladder: structural checks → vectors → cost → verdict
  verdict.py   codes, tones, mandatory wording
  store.py     SQLite (WAL): audits, tasks, watch events, cursor, payments
  watch.py     log poller + stats diff, resumable cursor
  badge.py     static SVG
  telegram.py  webhook → the same engine
  web.py       FastAPI app
templates/ static/ tests/ scripts/ probe/ docs/
```

## Honest limits

- The netlist is supplied by the user. The chain exposes no readable tape-out record, so the
  report can never say "this is what the seller taped out" — only "these bytes behave like this".
- Sequential tasks are sampled. The frame schedule for them is not published; a divergence is
  reported as `INCONCLUSIVE`, never as a mismatch.
- Cost is computed locally. Where the protocol verifier answered, both figures are shown and a
  disagreement is visible rather than hidden.
- A verdict describes a netlist at a task. It says nothing about ownership or whether the circuit
  holds the seat tomorrow — that is what the watcher is for.
- No transactions are sent. The app is read-only against X Layer.

## Deploy

`Dockerfile` + `compose.yml` (app + Caddy with automatic TLS). Copy `.env.example` to `.env`,
set `BASE_URL` and `SITE_ADDRESS`, then `make deploy`. Data lives on the `podx-data` volume;
`/healthz` is the readiness probe and reports watcher lag.

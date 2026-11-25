# PoD Underwrite

Pre-purchase correctness audits for PoD circuits on X Layer.

You are about to buy a circuit that mines someone else's tape-out task. Before you pay, paste its
TAP-02 netlist and the task id: you get a verdict, the input that breaks it if one exists, the cost
arithmetic that decides whether it can ever win the seat, and provenance (block, block hash,
implementation address, vector root, netlist hash).

**Live demo:** <https://pod-underwrite.vercel.app> — sample row on the home page. No wallet, no signup.
Everything below is what it actually does, including the parts it refuses to claim.

---

## The 90-second demo

Five clicks, nothing to install.

| # | Click | What it shows |
|---|---|---|
| 1 | **Deliberately broken inverter** → `/sample/wrong-inverter` | `VECTOR-MISMATCH`. The report prints the **lowest failing input and both outputs** — the exact value a challenger would submit, i.e. the money you would lose. |
| 2 | **1-gate inverter** → `/sample/inverter` | `PASS` over all 256 official vectors, with the vector root rebuilt from the leaves and compared to the task's published root. Coverage is printed, never implied. |
| 3 | **Latched inverter** → `/sample/latch-inverter` | `SHAPE-MISMATCH`: a latch on a combinational task. A different failure from "wrong", and the report says which. |
| 4 | the **JSON** link on any report | `/a/{id}.json` — the same facts an agent, bot or marketplace calls. Also `POST /api/audit` with `{task_id, netlist}`. |
| 5 | the **badge** link on any report | `/badge/{id}.svg` — a verdict to paste into a listing, so a seller can show it and a buyer can replay it. |

What a demo *cannot* show is the oracle problem, so it is written down instead: `docs/oracle.md`
records the search for a reference implementation, what was asked of the chain and the API, and why
`PASS` means *all 256 official vectors* rather than *every input*.

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

## Hackathon submission — the TapeOut surface it uses

X Layer **TapeOut Accelerate** (Build X Series), deadline 2026-12-07 23:59 UTC. The form asks for
addresses, ecosystem usage, users and a revenue model; this is that answer in one place.

| Form field | Answer |
|---|---|
| Processor / circuit address | processor `0x87BFCa3494Aa228fF589ab9B642143A5e8c8F9a6`, mining contract `0x4F6A2312D80753BdAFd35DDB1Bf4BB14C43F8265`, X Layer chain `196`. The audited circuit is whichever one the user pastes; the task ids come from the live index. |
| TapeOut ecosystem used | the official task index and the 256-vector sets from the PoD API (`api.fabx.tech`), the published Merkle roots (rebuilt here, not trusted), on-chain block hash / EIP-1967 implementation provenance, and the protocol's own cost verifier as a cross-check. This project deploys no circuit and sends no transaction — it audits circuits other people tape out. |
| Access link | the Vercel deployment above; `compose.yml` for a durable self-hosted instance. |
| Target users | miners evaluating a circuit before buying it; sellers who need a credible pre-sale report; marketplaces, bots and agents that want a machine-checkable verdict (`POST /api/audit`, `/badge/*.svg`). Reached through the PoD miner Telegram groups, X Layer/TapeOut channels and the badge embedded in listings. |
| Revenue model | per-audit fee (a report is a one-off purchase decision), a seller badge/provenance subscription, and a watcher subscription that warns a seat holder when their circuit is challenged. The payment and token-guarded fulfilment paths already exist in `store.py` / `POST /admin/fulfil`; **no payment rail is live yet**, so none of this is claimed as revenue. |
| Why users pay and stay | one wrong circuit purchase is a total loss, and the audit costs a fraction of it. The output is an artefact — verdict, counterexample, badge — that the buyer can replay offline and the seller can show, which is what makes it recurring rather than a one-off favour. |

Judged on user value, innovation, ecosystem use and completeness: the demo above is the
completeness evidence, the counterexample is the innovation, and the oracle section is the honest
limit.

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

### Vercel (what the demo link runs on)

Zero config beyond what is in the repo: `vercel.json` runs the ASGI app as one Python function,
`api/index.py` is the entrypoint, `requirements.txt` mirrors `pyproject.toml` for Vercel's builder,
and `.python-version` pins 3.12.

```bash
npx vercel --prod
```

Vercel sets `VERCEL=1` itself, so `podx/config.py` moves SQLite and the cache to `/tmp` and turns the
watcher off. Serverless filesystems are read-only elsewhere and instances are recycled, so **on
Vercel an `/a/{id}` permalink survives only while the instance is warm**; the JSON response carries
every fact inline and re-auditing a netlist reproduces the same verdict, but history, badges and
watcher events need the durable deployment below.

### Docker (durable)

`Dockerfile` + `compose.yml` (app + Caddy with automatic TLS). Copy `.env.example` to `.env`,
set `BASE_URL` and `SITE_ADDRESS`, then `make deploy`. Data lives on the `podx-data` volume;
`/healthz` is the readiness probe and reports watcher lag.

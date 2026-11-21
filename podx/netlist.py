"""TAP-02 netlist decoding, validation, costing and simulation.

Ported from the `probe/` work that reproduced on-chain `eval`/`step` results
256/256 and 8/8 on TapeOut circuits (see `docs/oracle.md`). Pure functions, no
network, no database: everything here is deterministic given bytes in.

Wire format (TAP-02 section 2)
-----------------------------
    NAND   opcode 0x00, 3-byte left, 3-byte right        -> 7 bytes
    LATCH  opcode 0x01, 3-byte d                          -> 4 bytes
    REF    opcode 0x02, 20-byte processor, 8-byte circuit,
                       uint8 nIn, uint8 nOut, nIn * 3-byte inputs

Signal numbering
----------------
    0, 1                  constants 0 and 1
    2 .. 2+nIn-1          circuit inputs
    2+nIn ..              one signal per produced value, in record order

A LATCH's `d` may point at a signal produced *later* in the stream, so latches
are resolved after every record has been evaluated ("LATCH-forward rule").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

OP_NAND = 0x00
OP_LATCH = 0x01
OP_REF = 0x02

NAND_BYTES = 7
LATCH_BYTES = 4

#: PoD tape-out bounds, per the task index and the published config.
MAX_NAND = 4800
MAX_BYTES = 34000


class NetlistError(ValueError):
    """The byte stream is not a TAP-02 netlist."""


# --------------------------------------------------------------------------- gates
@dataclass(frozen=True, slots=True)
class Nand:
    a: int
    b: int

    kind = "NAND"
    width = 1


@dataclass(frozen=True, slots=True)
class Latch:
    d: int

    kind = "LATCH"
    width = 1


@dataclass(frozen=True, slots=True)
class Ref:
    processor: str
    circuit_id: int
    n_in: int
    n_out: int
    ins: tuple[int, ...]

    kind = "REF"

    @property
    def width(self) -> int:
        return self.n_out


Gate = Nand | Latch | Ref


# --------------------------------------------------------------------------- codec
def decode(buf: bytes) -> list[Gate]:
    """Decode a TAP-02 netlist. Raises :class:`NetlistError` on any malformation."""
    gates: list[Gate] = []
    o = 0
    n = len(buf)
    while o < n:
        op = buf[o]
        if op == OP_NAND:
            if o + NAND_BYTES > n:
                raise NetlistError(f"truncated NAND at byte {o}")
            gates.append(
                Nand(
                    int.from_bytes(buf[o + 1 : o + 4], "big"),
                    int.from_bytes(buf[o + 4 : o + 7], "big"),
                )
            )
            o += NAND_BYTES
        elif op == OP_LATCH:
            if o + LATCH_BYTES > n:
                raise NetlistError(f"truncated LATCH at byte {o}")
            gates.append(Latch(int.from_bytes(buf[o + 1 : o + 4], "big")))
            o += LATCH_BYTES
        elif op == OP_REF:
            if o + 31 > n:
                raise NetlistError(f"truncated REF at byte {o}")
            processor = "0x" + buf[o + 1 : o + 21].hex()
            circuit_id = int.from_bytes(buf[o + 21 : o + 29], "big")
            n_in = buf[o + 29]
            n_out = buf[o + 30]
            if o + 31 + 3 * n_in > n:
                raise NetlistError(f"truncated REF inputs at byte {o}")
            ins = tuple(
                int.from_bytes(buf[o + 31 + 3 * i : o + 34 + 3 * i], "big")
                for i in range(n_in)
            )
            gates.append(Ref(processor, circuit_id, n_in, n_out, ins))
            o += 31 + 3 * n_in
        else:
            raise NetlistError(f"bad opcode 0x{op:02x} at byte {o}")
    return gates


def encode(gates: Iterable[Gate]) -> bytes:
    """Re-encode gates to the TAP-02 wire format."""
    out = bytearray()
    for g in gates:
        if isinstance(g, Nand):
            out += b"\x00" + g.a.to_bytes(3, "big") + g.b.to_bytes(3, "big")
        elif isinstance(g, Latch):
            out += b"\x01" + g.d.to_bytes(3, "big")
        else:
            out += (
                b"\x02"
                + bytes.fromhex(g.processor[2:] if g.processor.startswith("0x") else g.processor)
                + g.circuit_id.to_bytes(8, "big")
                + bytes([g.n_in & 0xFF, g.n_out & 0xFF])
            )
            for i in g.ins:
                out += i.to_bytes(3, "big")
    return bytes(out)


# --------------------------------------------------------------------------- circuit
@dataclass(frozen=True, slots=True)
class Circuit:
    """A decoded netlist plus the pin counts it claims."""

    gates: tuple[Gate, ...]
    n_in: int
    n_out: int
    #: declared totals from `circuitInfo`/task metadata, when known
    declared_gates: int | None = None
    declared_state: int | None = None

    @property
    def nand(self) -> int:
        return sum(1 for g in self.gates if isinstance(g, Nand))

    @property
    def latch(self) -> int:
        return sum(1 for g in self.gates if isinstance(g, Latch))

    @property
    def refs(self) -> int:
        return sum(1 for g in self.gates if isinstance(g, Ref))

    @property
    def own_gates(self) -> int:
        return self.nand + self.latch

    @property
    def n_state(self) -> int:
        """Latch count, including latches inside REF sub-circuits."""
        return self.latch + sum(g.n_out and 0 for g in self.gates if isinstance(g, Ref))

    @property
    def produced(self) -> int:
        return sum(g.width for g in self.gates)

    @property
    def area(self) -> int:
        """`A = nand + 7 * latch` — the protocol's area term."""
        return self.nand + 7 * self.latch

    @property
    def depth(self) -> int:
        """Combinational depth. Constants and inputs are level 0.

        A LATCH reads its stored bit, so its output inherits ``d``'s level and
        adds no delay; every NAND adds one.
        """
        level: list[int] = [0, 0]
        level.extend([0] * self.n_in)
        for g in self.gates:
            if isinstance(g, Nand):
                level.append(1 + max(_at(level, g.a), _at(level, g.b)))
            elif isinstance(g, Latch):
                level.append(_at(level, g.d))
            else:  # REF: keep the shape valid; PoD rejects REF before costing
                level.append(1 + max((_at(level, i) for i in g.ins), default=0))
        if self.n_out <= 0:
            return 1
        return max(level[len(level) - self.n_out :] or [1])

    @property
    def cost(self) -> int:
        """`C = A * max(depth, 1) ** 3` — the PoD cost model."""
        return self.area * max(self.depth, 1) ** 3

    def gate_bounds_ok(self) -> tuple[bool, str]:
        """Tape-out bounds. REF records are not permitted on PoD."""
        if self.refs:
            return False, f"{self.refs} REF record(s); PoD netlists may not reference sub-circuits"
        if self.nand > MAX_NAND:
            return False, f"{self.nand} NAND gates exceeds the {MAX_NAND} bound"
        if self.latch > MAX_NAND:
            return False, f"{self.latch} latches exceeds the {MAX_NAND} bound"
        return True, ""

    def wellformed(self) -> list[str]:
        """TAP-02 section 3 structural checks. Empty list means well formed."""
        problems: list[str] = []
        if self.n_in < 0:
            problems.append(f"negative input count {self.n_in}")
        if self.n_out < 1:
            problems.append(f"output count {self.n_out} is not positive")
        if self.n_in > 65535 or self.n_out > 65535:
            problems.append("pin count out of range")
        first = 2 + self.n_in  # first signal index not yet produced
        produced = 0
        for k, g in enumerate(self.gates):
            if isinstance(g, Nand):
                if g.a >= first or g.b >= first:
                    problems.append(
                        f"record {k} ({g.kind}) reads a signal that does not exist yet "
                        f"(signal {max(g.a, g.b)} >= {first})"
                    )
                first += 1
                produced += 1
            elif isinstance(g, Latch):
                if g.d >= 2 + self.n_in + self.produced:
                    problems.append(f"record {k} (LATCH) d={g.d} out of range")
                first += 1
                produced += 1
            else:
                if any(i >= first for i in g.ins):
                    problems.append(f"record {k} (REF) reads a future signal")
                first += g.n_out
                produced += g.n_out
        if 2 + self.n_in + produced < 2 + self.n_in + self.n_out:
            problems.append("too few signals for the declared outputs")
        return problems


def _at(level: Sequence[int], idx: int) -> int:
    return level[idx] if 0 <= idx < len(level) else 0


# --------------------------------------------------------------------------- bits
def pack(bits: Sequence[int], n: int) -> bytes:
    """Pack bits LSB-first into `ceil(n/8)` bytes (the on-chain convention)."""
    b = bytearray((n + 7) // 8)
    for i in range(min(n, len(bits))):
        if bits[i]:
            b[i >> 3] |= 1 << (i & 7)
    return bytes(b)


def unpack(buf: bytes, n: int) -> list[int]:
    return [(buf[i >> 3] >> (i & 7)) & 1 if (i >> 3) < len(buf) else 0 for i in range(n)]


def int_to_bits(value: int, n: int) -> list[int]:
    return [(value >> i) & 1 for i in range(n)]


def bits_to_int(bits: Sequence[int]) -> int:
    v = 0
    for i, b in enumerate(bits):
        if b:
            v |= 1 << i
    return v


# --------------------------------------------------------------------------- simulate
def beat(circuit: Circuit, state: Sequence[int], inputs: Sequence[int]) -> tuple[list[int], list[int]]:
    """One beat: returns `(new_state_bits, output_bits)`."""
    sig: list[int] = [0, 1]
    sig.extend(inputs)
    pending: list[tuple[str, int | list[int]]] = []
    cur = 0
    for g in circuit.gates:
        if isinstance(g, Nand):
            sig.append(1 - (sig[g.a] & sig[g.b]))
        elif isinstance(g, Latch):
            sig.append(state[cur] if cur < len(state) else 0)
            cur += 1
            pending.append(("latch", g.d))
        else:
            raise NetlistError("REF records are not simulatable without a chain oracle")
    new_state: list[int] = []
    for kind, v in pending:
        if kind == "latch":
            new_state.append(sig[v])  # type: ignore[index]
        else:  # pragma: no cover - REF path rejected above
            new_state.extend(v)  # type: ignore[arg-type]
    if circuit.n_out <= 0:
        return new_state, []
    return new_state, sig[len(sig) - circuit.n_out :]


def run_frames(circuit: Circuit, frames: Sequence[bytes]) -> list[bytes]:
    """Run a sequential circuit from the all-zero latch state.

    Returns one packed output per input frame.
    """
    state = [0] * circuit.latch
    out: list[bytes] = []
    depth_state = circuit.n_state or circuit.latch
    for frame in frames:
        state, bits = beat(circuit, state, unpack(frame, circuit.n_in))
        if len(state) < depth_state:
            state = state + [0] * (depth_state - len(state))
        out.append(pack(bits, circuit.n_out))
    return out


# --------------------------------------------------------------------------- bit-parallel evaluation
def _bit_masks(n: int) -> list[int]:
    """For each bit position of a counter 0..n-1, the mask of indices where it is set."""
    if n <= 0:
        return []
    width = (n - 1).bit_length()
    masks = []
    for j in range(width):
        block = 1 << j
        pattern = 0
        # build the repeating 0^block 1^block pattern as one big int
        i = 0
        while i < n:
            for k in range(i + block, min(i + 2 * block, n)):
                pattern |= 1 << k
            i += 2 * block
        masks.append(pattern)
    return masks


_MASK_CACHE: dict[int, list[int]] = {}


def counter_masks(n: int) -> list[int]:
    """Cached per-bit masks for the counter 0..n-1."""
    m = _MASK_CACHE.get(n)
    if m is None:
        m = _bit_masks(n)
        _MASK_CACHE[n] = m
    return m


def eval_masks(circuit: Circuit, input_masks: Sequence[int], width: int) -> list[int]:
    """Evaluate the circuit on `width` inputs at once, one bit-vector per signal."""
    if circuit.latch:
        raise NetlistError("eval_masks is for combinational circuits; use beat()/run_frames()")
    if circuit.refs:
        raise NetlistError("REF records are not simulatable without a chain oracle")
    all_ones = (1 << width) - 1
    sig: list[int] = [0, all_ones]
    sig.extend(input_masks)
    for g in circuit.gates:
        assert isinstance(g, Nand)
        sig.append(all_ones ^ (sig[g.a] & sig[g.b]))
    return sig[len(sig) - circuit.n_out :]


def output_masks(circuit: Circuit, values: Sequence[int]) -> list[int]:
    """Output bit-vectors for a list of input values (evaluated in chunks)."""
    if not values:
        return [0] * circuit.n_out
    out = [0] * circuit.n_out
    for start in range(0, len(values), 256):
        chunk = values[start : start + 256]
        width = len(chunk)
        masks = masks_for_values(chunk)
        got = eval_masks(circuit, masks, width)
        shift = start
        for bit in range(circuit.n_out):
            out[bit] |= got[bit] << shift
    return out


def masks_for_values(values: Sequence[int]) -> list[int]:
    """Bit-vectors for an arbitrary (but sorted, distinct) list of input values."""
    n_in = 0
    for v in values:
        if v.bit_length() > n_in:
            n_in = v.bit_length()
    n_in = max(n_in, 1)
    count = len(values)
    if [v for v in values] == list(range(values[0], values[0] + count)) and values[0] == 0:
        return counter_masks(count)[:n_in]
    masks = [0] * n_in
    for i, v in enumerate(values):
        bit = 1 << i
        for j in range(n_in):
            if (v >> j) & 1:
                masks[j] |= bit
    return masks


def first_difference(a_masks: Sequence[int], b_masks: Sequence[int]) -> tuple[int, int] | None:
    """Lowest input index where two output bit-vector sets differ, and the output bit."""
    for bit, (a, b) in enumerate(zip(a_masks, b_masks)):
        diff = a ^ b
        if diff:
            low = (diff & -diff).bit_length() - 1
            return low, bit
    return None

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

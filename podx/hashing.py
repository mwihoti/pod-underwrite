"""Keccak-256 and the proof scheme used by the PoD vector commitments.

The scheme was derived empirically against a live task root, not documented
anywhere. For task 3 on X Layer (`0x8889823b…2526`) the recipe below reproduces
the published root bit-for-bit from the 256 published leaves:

    leaf = keccak( keccak( abi.encode(uint256 index, bytes input, bytes expectedOut) ) )
    node = keccak( min(a, b) || max(a, b) )

Note the double hash on the leaf and the *commutative* pair hash: folding a
proof does not depend on which side the leaf sits on. `tests/test_vectors.py`
locks this down for three separate tasks so a regression cannot pass silently.
"""

from __future__ import annotations

from Crypto.Hash import keccak

__all__ = [
    "keccak256",
    "keccak_hex",
    "encode_leaf_values",
    "merkle_leaf",
    "merkle_node",
    "merkle_root",
    "verify_proof",
    "root_from_proofs",
]


def keccak256(data: bytes) -> bytes:
    h = keccak.new(digest_bits=256)
    h.update(data)
    return h.digest()


def keccak_hex(data: bytes) -> str:
    return "0x" + keccak256(data).hex()


def _word(n: int) -> bytes:
    return int(n).to_bytes(32, "big")


def _pad_right(b: bytes) -> bytes:
    return b + b"\x00" * ((32 - len(b) % 32) % 32)


def encode_leaf_values(index: int, inp: bytes, out: bytes) -> bytes:
    """`abi.encode(uint256 index, bytes input, bytes expectedOut)`."""

    head: list[bytes] = [_word(index), _word(96)]
    tail = _word(len(inp)) + _pad_right(inp)
    head.append(_word(96 + len(tail)))
    tail += _word(len(out)) + _pad_right(out)
    return b"".join(head) + tail


def merkle_leaf(index: int, inp: bytes, out: bytes) -> bytes:
    return keccak256(keccak256(encode_leaf_values(index, inp, out)))


def merkle_node(a: bytes, b: bytes) -> bytes:
    lo, hi = (a, b) if a <= b else (b, a)
    return keccak256(lo + hi)


def merkle_root(leaves: list[bytes]) -> bytes:
    if not leaves:
        raise ValueError("empty leaf set")
    layer = list(leaves)
    while len(layer) > 1:
        nxt = [merkle_node(layer[i], layer[i + 1]) for i in range(0, len(layer) - 1, 2)]
        if len(layer) % 2:
            nxt.append(layer[-1])
        layer = nxt
    return layer[0]


def verify_proof(leaf: bytes, proof: list[bytes], root: bytes) -> bool:
    h = leaf
    for sibling in proof:
        h = merkle_node(h, sibling)
    return h == root


def root_from_proofs(
    leaves: list[bytes], proofs: list[list[bytes]]
) -> tuple[str | None, int]:
    """Fold every leaf through its proof and report the derived root.

    Returns `(root_hex, distinct_roots)`. `distinct_roots == 1` means all 256
    proofs agree on one root; agreement with the *published* root is checked by
    the caller. This is what makes the vector set verifiable without trusting
    the service that served it.
    """
    seen: set[str] = set()
    for leaf, proof in zip(leaves, proofs):
        h = leaf
        for sibling in proof:
            h = merkle_node(h, sibling)
        seen.add(h.hex())
    if len(seen) == 1:
        return "0x" + next(iter(seen)), 1
    return None, len(seen)

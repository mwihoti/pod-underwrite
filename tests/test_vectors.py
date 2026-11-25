"""The vector commitments must verify from the published proofs alone."""

from __future__ import annotations

import pytest

from podx import hashing, vectors

TASKS = (1, 3, 7)


@pytest.mark.parametrize("task_id", TASKS)
def test_published_root_is_reproduced(task_id):
    vs = vectors.official(task_id)
    assert vs.verified, f"task {task_id}: proofs fold to {vs.derived_root}, published {vs.root}"


@pytest.mark.parametrize("task_id", TASKS)
def test_every_vector_has_a_proof(task_id):
    vs = vectors.official(task_id)
    assert vs.count == 256
    assert [v.index for v in vs.vectors] == list(range(256))
    assert all(v.proof for v in vs.vectors)


def test_tampered_leaf_breaks_the_root():
    vs = vectors.official(1)
    leaves = [hashing.merkle_leaf(v.index, v.input, v.expected_out) for v in vs.vectors]
    fresh = list(leaves)
    fresh[0] = hashing.merkle_leaf(0, b"\xff", vs.vectors[0].expected_out)
    derived, distinct = hashing.root_from_proofs(fresh, [list(v.proof) for v in vs.vectors])
    # The tampered leaf folds to a second root; the other 255 still agree on the
    # published one. No single root is derived, so the set is not trusted.
    assert distinct == 2
    assert derived is None


def test_leaf_encoding_is_double_hashed():
    """The scheme is unusual; pin it so a "cleanup" cannot silently break PASS."""
    leaf = hashing.merkle_leaf(0, b"\x00", b"\x01")
    inner = hashing.keccak256(hashing.encode_leaf_values(0, b"\x00", b"\x01"))
    assert leaf == hashing.keccak256(inner)

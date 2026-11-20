"""Performance tests for accept-beliefs pipeline.

These tests measure load, add_node, and save performance at scale
to identify bottlenecks when accepting thousands of beliefs.
"""

import time

import pytest

from reasonsforge.storage import Storage


def _generate_matches(n: int) -> list[tuple[str, str, str]]:
    return [
        (f"belief-{i:06d}", f"Claim number {i} about the system", f"test-source-{i}")
        for i in range(n)
    ]


def _seed_network(db_path: str, n: int) -> None:
    """Pre-populate a database with n beliefs."""
    store = Storage(db_path)
    net = store.load()
    for i in range(n):
        net.add_node(
            id=f"existing-{i:06d}",
            text=f"Existing belief {i}",
            source="seed",
        )
    store.save(net)
    store.close()


@pytest.mark.parametrize("count", [100, 1000, 5000])
def test_accept_empty_db(tmp_path, count):
    """Measure accept performance into an empty database."""
    db_path = str(tmp_path / "reasons.db")
    matches = _generate_matches(count)

    store = Storage(db_path)
    net = store.load()

    t0 = time.monotonic()
    for belief_id, claim_text, source in matches:
        net.add_node(id=belief_id, text=claim_text, source=source)
    add_elapsed = time.monotonic() - t0

    t0 = time.monotonic()
    store.save(net)
    save_elapsed = time.monotonic() - t0
    store.close()

    rate = count / add_elapsed if add_elapsed > 0 else float("inf")
    print(f"\n  Empty DB — {count} nodes:")
    print(f"    add_node: {add_elapsed:.2f}s ({rate:.0f} nodes/s)")
    print(f"    save:     {save_elapsed:.2f}s")
    print(f"    total:    {add_elapsed + save_elapsed:.2f}s")


@pytest.mark.parametrize("existing,new", [
    (1000, 1000),
    (5000, 1000),
    (10000, 1000),
    (10000, 5000),
])
def test_accept_with_existing(tmp_path, existing, new):
    """Measure accept performance with a pre-populated database."""
    db_path = str(tmp_path / "reasons.db")
    _seed_network(db_path, existing)
    matches = _generate_matches(new)

    t0 = time.monotonic()
    store = Storage(db_path)
    net = store.load()
    load_elapsed = time.monotonic() - t0

    t0 = time.monotonic()
    for belief_id, claim_text, source in matches:
        net.add_node(id=belief_id, text=claim_text, source=source)
    add_elapsed = time.monotonic() - t0

    t0 = time.monotonic()
    store.save(net)
    save_elapsed = time.monotonic() - t0
    store.close()

    total = load_elapsed + add_elapsed + save_elapsed
    rate = new / add_elapsed if add_elapsed > 0 else float("inf")
    print(f"\n  {existing} existing + {new} new:")
    print(f"    load:     {load_elapsed:.2f}s")
    print(f"    add_node: {add_elapsed:.2f}s ({rate:.0f} nodes/s)")
    print(f"    save:     {save_elapsed:.2f}s ({existing + new} total nodes)")
    print(f"    total:    {total:.2f}s")

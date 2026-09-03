"""Store atomicity, sharding, and locking."""

from __future__ import annotations

import asyncio
import os
import socket
import time
from pathlib import Path

import pytest

from opengloss_generator.config import StoreConfig
from opengloss_generator.errors import LockTimeoutError
from opengloss_generator.store import LexemeStore
from tests.conftest import make_entry


@pytest.fixture
def store(tmp_path: Path) -> LexemeStore:
    return LexemeStore(StoreConfig(root=tmp_path / "store", fsync_on_write=False))


def test_write_then_read_round_trips(store):
    entry = make_entry(variants=True)
    store.write(entry)
    loaded = store.read("abseil")
    assert loaded is not None
    assert loaded.headword == "abseil"
    assert loaded.rendition_ids() == entry.rendition_ids()


def test_headword_and_slug_address_the_same_entry(store):
    store.write(make_entry("3D Model"))
    assert store.exists("3D Model")
    assert store.exists("3d_model")
    assert store.read("3d_model") is not None


def test_entries_are_sharded(store):
    store.write(make_entry("abseil"))
    path = store.path_for("abseil")
    assert path.relative_to(store.root).parts[:-1] != ()
    assert len(path.relative_to(store.root).parts) == 3


def test_write_leaves_no_temp_file(store):
    store.write(make_entry())
    leftovers = [p for p in store.root.rglob(".*") if p.is_file()]
    assert leftovers == []


def test_missing_entry_reads_as_none(store):
    assert store.read("nonexistent") is None


def test_corrupt_entry_is_skipped_by_iteration_but_raises_on_direct_read(store):
    store.write(make_entry())
    store.path_for("abseil").write_text("{not json", encoding="utf-8")
    assert list(store.iter_entries()) == []
    with pytest.raises(Exception, match="cannot read entry"):
        store.read("abseil")


async def test_lock_serialises_in_process_writers(store):
    order: list[str] = []

    async def worker(tag: str, hold: float) -> None:
        async with store.locked("abseil"):
            order.append(f"{tag}-in")
            await asyncio.sleep(hold)
            order.append(f"{tag}-out")

    await asyncio.gather(worker("a", 0.05), worker("b", 0.0))
    # Whatever the interleaving, no worker enters while another holds the lock.
    assert order in (
        ["a-in", "a-out", "b-in", "b-out"],
        ["b-in", "b-out", "a-in", "a-out"],
    )


async def test_lock_file_is_released(store):
    async with store.locked("abseil"):
        assert store.path_for("abseil").with_suffix(".lock").exists()
    assert not store.path_for("abseil").with_suffix(".lock").exists()


async def test_foreign_live_lock_times_out(tmp_path: Path):
    store = LexemeStore(
        StoreConfig(root=tmp_path / "store", lock_timeout_seconds=0.2, fsync_on_write=False)
    )
    lock_path = store.path_for("abseil").with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # A lock held by this (live) process must not be broken, however we ask.
    lock_path.write_text(f"{os.getpid()} 0\n", encoding="utf-8")
    with pytest.raises(LockTimeoutError):
        async with store.locked("abseil"):
            pass


async def test_stale_lock_from_dead_process_is_broken(tmp_path: Path):
    store = LexemeStore(
        StoreConfig(
            root=tmp_path / "store",
            lock_timeout_seconds=2.0,
            lock_stale_seconds=0.001,
            fsync_on_write=False,
        )
    )
    lock_path = store.path_for("abseil").with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999999 0\n", encoding="utf-8")
    async with store.locked("abseil"):
        pass


def test_count_and_iteration(store):
    for word in ("abseil", "rappel", "belay"):
        store.write(make_entry(word))
    assert store.count() == 3
    assert sorted(store.iter_ids()) == ["abseil", "belay", "rappel"]
    assert len(list(store.iter_entries())) == 3


async def test_dead_owner_lock_on_this_host_is_broken_immediately(tmp_path: Path):
    store = LexemeStore(
        StoreConfig(root=tmp_path / "store", lock_timeout_seconds=2.0, fsync_on_write=False)
    )
    lock_path = store.path_for("abseil").with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Fresh (age ~0, far under the 900 s TTL) but the owner pid does not exist here.
    lock_path.write_text(f"999999999 {time.time()} {socket.gethostname()}\n", encoding="utf-8")
    async with store.locked("abseil"):
        pass


async def test_fresh_lock_from_another_host_is_respected(tmp_path: Path):
    store = LexemeStore(
        StoreConfig(root=tmp_path / "store", lock_timeout_seconds=0.2, fsync_on_write=False)
    )
    lock_path = store.path_for("abseil").with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Same dead pid, but a different host: liveness cannot be probed, TTL must apply.
    lock_path.write_text(f"999999999 {time.time()} other-host\n", encoding="utf-8")
    with pytest.raises(LockTimeoutError):
        async with store.locked("abseil"):
            pass

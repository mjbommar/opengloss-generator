"""Sharded, atomically-written lexeme store.

Three mechanisms give FR-4.3/FR-4.4 (see ``docs/DESIGN.md`` § 3.4):

1. an in-process ``asyncio.Lock`` per lexeme, so two workers in one process serialise;
2. an ``O_CREAT|O_EXCL`` lock file, so two *processes* serialise — this is atomic on both
   local filesystems and NFS;
3. write-to-temp then ``os.replace``, so a reader (or a ``SIGKILL``) sees either the old
   complete file or the new complete file, never a truncated one.

Entries are sharded by a hash of the id. The v1.3 store put 205,996 files in one
directory, which is why a ``du`` over it takes ten minutes on NFS.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import socket
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import orjson

from opengloss_generator.errors import LockTimeoutError, StoreError
from opengloss_generator.identity import shard_for, slugify
from opengloss_generator.schema import Lexeme

if TYPE_CHECKING:
    from opengloss_generator.config import StoreConfig

__all__ = ["LexemeStore"]

_LOCK_SUFFIX = ".lock"
_ENTRY_SUFFIX = ".json"


class LexemeStore:
    """A filesystem-backed collection of :class:`~opengloss_generator.schema.Lexeme`.

    Args:
        config: Store configuration (root path, lock timeouts, fsync policy).
    """

    def __init__(self, config: StoreConfig) -> None:
        """Create the store root and the per-entry lock registry."""
        self._config = config
        self._root = Path(config.root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @property
    def root(self) -> Path:
        """Return the store root directory."""
        return self._root

    def path_for(self, headword_or_id: str) -> Path:
        """Return the on-disk path for an entry.

        Args:
            headword_or_id: A headword or an already-slugged lexeme id; both work,
                because slugifying a slug is a no-op.

        Returns:
            The absolute path the entry occupies, whether or not it exists.
        """
        lexeme_id = slugify(headword_or_id)
        return self._root.joinpath(*shard_for(lexeme_id), f"{lexeme_id}{_ENTRY_SUFFIX}")

    def exists(self, headword_or_id: str) -> bool:
        """Return whether an entry is present in the store."""
        return self.path_for(headword_or_id).is_file()

    def read(self, headword_or_id: str) -> Lexeme | None:
        """Load an entry.

        Args:
            headword_or_id: Headword or lexeme id.

        Returns:
            The parsed entry, or ``None`` if it is absent.

        Raises:
            StoreError: If the file exists but cannot be parsed or validated.
        """
        path = self.path_for(headword_or_id)
        if not path.is_file():
            return None
        try:
            return Lexeme.model_validate(orjson.loads(path.read_bytes()))
        except Exception as exc:
            raise StoreError(f"cannot read entry at {path}: {exc}") from exc

    def write(self, entry: Lexeme) -> Path:
        """Write an entry atomically.

        The temp file is created in the destination directory so ``os.replace`` stays
        within one filesystem, which is what makes the swap atomic.

        Args:
            entry: The entry to persist.

        Returns:
            The path written.
        """
        entry.touch()
        path = self.path_for(entry.lexeme_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = orjson.dumps(
            entry.model_dump(mode="json"),
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
        )
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with tmp.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                if self._config.fsync_on_write:
                    os.fsync(handle.fileno())
            tmp.replace(path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise StoreError(f"cannot write entry to {path}: {exc}") from exc
        return path

    def iter_entries(self) -> Iterator[Lexeme]:
        """Yield every entry in the store.

        Files that fail to parse are skipped rather than raising, so one bad entry does
        not make the whole store unreadable.
        """
        for path in sorted(self._root.rglob(f"*{_ENTRY_SUFFIX}")):
            if path.name.startswith("."):
                continue
            try:
                yield Lexeme.model_validate(orjson.loads(path.read_bytes()))
            except Exception:  # noqa: S112 - a corrupt entry must not halt iteration
                continue

    def iter_ids(self) -> Iterator[str]:
        """Yield every lexeme id in the store without parsing entry bodies."""
        for path in self._root.rglob(f"*{_ENTRY_SUFFIX}"):
            if not path.name.startswith("."):
                yield path.stem

    def count(self) -> int:
        """Return the number of entries in the store."""
        return sum(1 for _ in self.iter_ids())

    @contextlib.asynccontextmanager
    async def locked(self, headword_or_id: str) -> AsyncIterator[Path]:
        """Hold both the in-process and cross-process lock for an entry.

        Args:
            headword_or_id: Headword or lexeme id.

        Yields:
            The path the entry occupies.

        Raises:
            LockTimeoutError: If the cross-process lock cannot be taken within
                ``lock_timeout_seconds``.
        """
        lexeme_id = slugify(headword_or_id)
        path = self.path_for(lexeme_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(_LOCK_SUFFIX)

        async with self._locks[lexeme_id]:
            await self._acquire_file_lock(lock_path)
            try:
                yield path
            finally:
                lock_path.unlink(missing_ok=True)

    async def _acquire_file_lock(self, lock_path: Path) -> None:
        """Take the ``O_EXCL`` lock file, breaking it if it is stale.

        Args:
            lock_path: Path of the lock file.

        Raises:
            LockTimeoutError: If the lock is still held at the deadline.
        """
        deadline = time.monotonic() + self._config.lock_timeout_seconds
        delay = 0.02
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if self._break_if_stale(lock_path):
                    continue
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(
                        f"could not acquire {lock_path} within {self._config.lock_timeout_seconds}s"
                    ) from None
                await asyncio.sleep(delay)
                delay = min(delay * 2, 1.0)
            except OSError as exc:
                raise StoreError(f"cannot create lock {lock_path}: {exc}") from exc
            else:
                with os.fdopen(fd, "w") as handle:
                    handle.write(f"{os.getpid()} {time.time()} {socket.gethostname()}\n")
                return

    def _break_if_stale(self, lock_path: Path) -> bool:
        """Remove a lock whose holder is gone, or whose age exceeds the stale TTL.

        A lock written by a process on *this* host is broken as soon as that process no
        longer exists, whatever its age: a killed worker must not block its entry for
        the whole TTL. A lock from another host cannot be probed, so it is broken only
        by the TTL. Locks written before the hostname was recorded fall back to the TTL
        as well.

        Args:
            lock_path: Path of the lock file.

        Returns:
            ``True`` if the lock was removed and should be retried immediately.
        """
        try:
            age = time.time() - lock_path.stat().st_mtime
            fields = lock_path.read_text(encoding="utf-8").split()
        except FileNotFoundError:
            return True
        except OSError:
            fields = []
            age = 0.0
        holder = -1
        host: str | None = None
        try:
            holder = int(fields[0])
            host = fields[2] if len(fields) > 2 else None  # noqa: PLR2004 - pid, stamp, host
        except ValueError, IndexError:
            pass
        same_host = host is not None and host == socket.gethostname()
        if same_host and holder > 0 and not _pid_alive(holder):
            lock_path.unlink(missing_ok=True)
            return True
        if age < self._config.lock_stale_seconds:
            return False
        if holder > 0 and same_host and _pid_alive(holder):
            return False
        lock_path.unlink(missing_ok=True)
        return True


def _pid_alive(pid: int) -> bool:
    """Return whether a process id currently exists on this host.

    A lock held by a live process is never broken, however old it is; a lock left by a
    process that died is broken as soon as it passes the stale TTL. Cross-host staleness
    is handled by the TTL alone, since a remote pid cannot be probed.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True

"""Concurrent read-modify-write safety for the skills lockfile (#1557).

Two installs racing on `Lockfile.load()` -> mutate -> `Lockfile.save()` with
no locking means the second write can silently clobber the first's entry
instead of merging. `Lockfile.update()` holds an exclusive OS-level lock
across the whole cycle so concurrent writers serialize instead of racing.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

from agentos.skills.hub.installer import SkillInstaller
from agentos.skills.hub.lockfile import LockEntry, Lockfile
from agentos.skills.hub.source import SkillBundle


def _entry(name: str) -> LockEntry:
    return LockEntry(source="community", identifier=name)


def test_concurrent_updates_all_land_none_clobbered(tmp_path: Path, monkeypatch) -> None:
    """Many threads racing on `Lockfile.update` must all persist their entry.

    `save()` is slowed down to widen the window in which an unprotected
    read-modify-write would lose data: without the fix's lock, a thread that
    loaded before another thread's slow save finishes would overwrite that
    thread's entry on its own save. With the lock held across load->save,
    the delay only serializes the threads -- nothing gets dropped.
    """
    path = tmp_path / "skills-lock.json"
    real_save = Lockfile.save

    def slow_save(self: Lockfile, save_path: Path) -> None:
        time.sleep(0.01)
        real_save(self, save_path)

    monkeypatch.setattr(Lockfile, "save", slow_save)

    names = [f"skill-{i}" for i in range(20)]

    def worker(name: str) -> None:
        Lockfile.update(path, lambda lockfile: lockfile.add(name, _entry(name)))

    threads = [threading.Thread(target=worker, args=(name,)) for name in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = Lockfile.load(path)
    assert set(final.installed) == set(names)


def test_update_returns_mutate_result_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "skills-lock.json"

    Lockfile.update(path, lambda lockfile: lockfile.add("demo", _entry("demo")))
    assert "demo" in Lockfile.load(path).installed

    removed = Lockfile.update(path, lambda lockfile: lockfile.remove("demo"))
    assert removed is True
    assert "demo" not in Lockfile.load(path).installed

    # Removing again reports False but must not raise or corrupt the file.
    removed_again = Lockfile.update(path, lambda lockfile: lockfile.remove("demo"))
    assert removed_again is False


class _StaticRouter:
    """Router that always resolves to one fixed, named skill."""

    def __init__(self, name: str) -> None:
        self._name = name

    async def fetch(self, identifier: str, source_id: str) -> SkillBundle | None:
        body = f"---\nname: {self._name}\ndescription: Use when testing.\n---\n\n# {self._name}\n"
        return SkillBundle(name=self._name, files={"SKILL.md": body}, meta=None)

    async def inspect(self, identifier: str, source_id: str) -> None:
        return None


def test_concurrent_installs_of_different_skills_both_persist(tmp_path: Path) -> None:
    """Two real installs racing on the shared lockfile must both survive.

    Each runs on its own thread with its own event loop, mirroring two
    separate `agentos skill install` invocations racing on one machine.
    """
    lockfile_path = tmp_path / "lock.json"
    managed_dir = tmp_path / "managed"
    quarantine_dir = tmp_path / "quarantine"
    names = [f"skill-{i}" for i in range(8)]

    def install_one(name: str) -> None:
        async def _run() -> None:
            installer = SkillInstaller(
                router=_StaticRouter(name),
                managed_dir=managed_dir,
                quarantine_dir=quarantine_dir / name,
                lockfile_path=lockfile_path,
            )
            result = await installer.install(f"https://example.com/{name}", "community")
            assert result.success is True, result.message

        asyncio.run(_run())

    threads = [threading.Thread(target=install_one, args=(name,)) for name in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = Lockfile.load(lockfile_path)
    assert set(final.installed) == set(names)

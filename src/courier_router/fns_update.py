"""Versioned GAR builds, atomic publication, retention and rollback for the local service."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx

from .fns_import import build_dictionary, file_hash, validate_canaries, validate_dictionary
from .fns_source import gar_archive, latest_release


def now():
    return datetime.now(timezone.utc).isoformat()


def _fsync_dir(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _json(path: Path, value: dict):
    temporary = path.with_name(f".{path.name}-{uuid4().hex}")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@contextmanager
def _lock(root: Path, name: str, *, blocking=True):
    root.mkdir(parents=True, exist_ok=True)
    with (root / name).open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise RuntimeError("Обновление справочника уже выполняется") from None
        yield


def current_release(root: Path) -> Path | None:
    pointer = root / "current"
    if not pointer.is_symlink():
        return None
    result = pointer.resolve(strict=True)
    if result.parent != (root / "releases").resolve():
        raise ValueError("Некорректный указатель справочника")
    return result


def _switch(root: Path, release: Path):
    temporary = root / f".current-{uuid4().hex}"
    try:
        temporary.symlink_to(Path("releases") / release.name, target_is_directory=True)
        os.replace(temporary, root / "current")
        _fsync_dir(root)
    finally:
        temporary.unlink(missing_ok=True)


def status(root: Path) -> dict:
    root = Path(root)
    if not root.exists():
        return {"active": None, "state": {}}
    with _lock(root, ".publish.lock"):
        current = current_release(root)
        return {"active": _read(current / "manifest.json") if current else None,
                "state": _read(root / "status.json")}


def _retain(root: Path, active: Path, blocked_versions=(), keep: int = 3):
    # Call only under .publish.lock: a reader finishes loading its in-memory snapshot first.
    releases = sorted((p for p in (root / "releases").iterdir() if p.is_dir()),
                      key=lambda p: p.stat().st_mtime_ns, reverse=True)
    retained = {active}
    for release in releases:
        manifest = _read(release / "manifest.json")
        if (len(retained) < max(3, keep) and manifest
                and manifest["version"] not in blocked_versions):
            retained.add(release)
        if release not in retained:
            shutil.rmtree(release)


def update(root: Path, *, source=None, version=None, client=None, min_streets=500, canaries=True) -> dict:
    """Rebuild from selected members of a complete ZIP. No delta-chain dependence."""
    root = Path(root).resolve()
    with _lock(root, ".update.lock", blocking=False):
        state = _read(root / "status.json")
        state.update(last_check=now(), last_error=None)
        _json(root / "status.json", state)
        owned_client = client is None
        client = client or httpx.Client(timeout=45, headers={"Accept-Encoding": "identity"})
        stage = None
        try:
            if source is None:
                info = latest_release(client)
                source, version = info["url"], info["version"]
            else:
                if not version or not str(version).isdigit():
                    raise ValueError("Для локального полного ZIP требуется числовая --version")
                info = {"url": str(source), "version": str(version), "date": ""}
            state["available_version"] = str(version)
            current = current_release(root)
            previous = _read(current / "manifest.json") if current else None
            if previous and int(version) <= int(previous["version"]):
                _json(root / "status.json", state)
                return {"action": "up_to_date", "version": previous["version"]}
            if str(version) in state.get("blocked_versions", []):
                raise ValueError(f"Версия {version} заблокирована после отката")
            (root / "releases").mkdir(exist_ok=True)
            # Interrupted builds never become active; reclaim them under the updater lock.
            for stale in root.glob(".staging-*"):
                if stale.is_dir():
                    shutil.rmtree(stale)
            stage = root / f".staging-{uuid4().hex}"
            stage.mkdir()
            database = stage / "streets.sqlite"
            print(f"ГАР {version}: чтение улиц и иерархии регионов 47/78", flush=True)
            with gar_archive(source, client) as archive:
                stats = build_dictionary(archive, database, str(version))
            validate_dictionary(database, previous, min_streets=min_streets)
            if canaries:
                validate_canaries(database)
            manifest = {**info, **stats, "built_at": now(), "sha256": file_hash(database)}
            _json(stage / "manifest.json", manifest)
            with database.open("rb") as stream:
                os.fsync(stream.fileno())
            release = root / "releases" / f"{version}-{uuid4().hex[:8]}"
            os.replace(stage, release)
            stage = None
            _fsync_dir(root / "releases")
            with _lock(root, ".publish.lock"):
                try:
                    _switch(root, release)
                    # Re-open the exact path clients will use before recording success.
                    validate_dictionary(root / "current" / "streets.sqlite", min_streets=min_streets)
                except Exception:
                    if current:
                        _switch(root, current)
                    else:
                        (root / "current").unlink(missing_ok=True)
                        _fsync_dir(root)
                    state["blocked_versions"] = sorted(set(state.get("blocked_versions", [])) | {str(version)})
                    raise
                state.update(last_success=now(), active_version=str(version), last_error=None)
                _json(root / "status.json", state)
                _retain(root, release, state.get("blocked_versions", []))
            return {"action": "updated", **manifest}
        except Exception as error:
            state["last_error"] = str(error)
            _json(root / "status.json", state)
            raise
        finally:
            if stage is not None:
                shutil.rmtree(stage, ignore_errors=True)
            if owned_client:
                client.close()


def rollback(root: Path, *, min_streets=500) -> dict:
    root = Path(root).resolve()
    with _lock(root, ".update.lock", blocking=False), _lock(root, ".publish.lock"):
        current = current_release(root)
        if current is None:
            raise ValueError("Нет активного справочника")
        active = _read(current / "manifest.json")
        alternatives = []
        state = _read(root / "status.json")
        for path in (root / "releases").iterdir():
            manifest = _read(path / "manifest.json")
            if (path != current and manifest and int(manifest["version"]) < int(active["version"])
                    and manifest["version"] not in state.get("blocked_versions", [])):
                alternatives.append((int(manifest["version"]), path, manifest))
        if not alternatives:
            raise ValueError("Нет предыдущей проверенной версии для отката")
        _, target, manifest = max(alternatives, key=lambda item: item[0])
        if file_hash(target / "streets.sqlite") != manifest["sha256"]:
            raise ValueError("Контрольная сумма предыдущего справочника не совпала")
        validate_dictionary(target / "streets.sqlite", min_streets=min_streets)
        state["blocked_versions"] = sorted(set(state.get("blocked_versions", [])) | {active["version"]})
        state.update(active_version=manifest["version"], last_rollback=now())
        # Persist the quarantine before switching so a crash cannot re-enable the bad version.
        _json(root / "status.json", state)
        _switch(root, target)
        return {"action": "rolled_back", "version": manifest["version"], "blocked": active["version"]}

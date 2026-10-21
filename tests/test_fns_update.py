import fcntl
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from courier_router.address_dictionary import AddressDictionary
from courier_router.fns_import import build_dictionary, validate_dictionary
from courier_router import fns_update as updater


def test_rotation_snapshot_pinning_and_rollback(dictionary_root, gar_factory):
    pinned = AddressDictionary.open_current(dictionary_root)
    for day in (21, 22, 23):
        version = f"202610{day}"
        updater.update(dictionary_root, source=gar_factory(version), version=version, min_streets=1)
    assert len(list((dictionary_root / "releases").iterdir())) == 3
    assert pinned.version == "20261020"
    assert pinned.normalize("Санкт-Петербург, ул Горохвая, д 68").status == "corrected"
    assert AddressDictionary.open_current(dictionary_root).version == "20261023"
    rolled = updater.rollback(dictionary_root, min_streets=1)
    assert rolled["version"] == "20261022"
    assert updater.status(dictionary_root)["active"]["version"] == "20261022"
    with pytest.raises(ValueError, match="заблокирована"):
        updater.update(dictionary_root, source=gar_factory("20261023"), version="20261023", min_streets=1)
    assert AddressDictionary.open_current(dictionary_root).version == "20261022"


def test_same_version_does_not_reimport(dictionary_root, monkeypatch):
    monkeypatch.setattr(updater, "gar_archive", lambda *args: pytest.fail("Must not download again"))
    assert updater.update(dictionary_root, source=Path("unused.zip"), version="20261020")["action"] == "up_to_date"


def test_repeated_rollbacks_keep_two_usable_previous_versions(dictionary_root, gar_factory):
    for version in ("20261021", "20261022", "20261023", "20261024"):
        updater.update(dictionary_root, source=gar_factory(version), version=version, min_streets=1)
        if version in {"20261022", "20261023"}:
            assert updater.rollback(dictionary_root, min_streets=1)["version"] == "20261021"
    versions = {json.loads((path / "manifest.json").read_text())["version"]
                for path in (dictionary_root / "releases").iterdir()}
    assert versions == {"20261020", "20261021", "20261024"}
    assert updater.rollback(dictionary_root, min_streets=1)["version"] == "20261021"
    assert updater.rollback(dictionary_root, min_streets=1)["version"] == "20261020"


def test_switch_failure_after_replace_restores_previous(dictionary_root, gar_factory, monkeypatch):
    real_switch = updater._switch
    def switch(root, release):
        real_switch(root, release)
        if release.name.startswith("20261021-"):
            raise OSError("injected fsync failure after replace")
    monkeypatch.setattr(updater, "_switch", switch)
    with pytest.raises(OSError, match="fsync failure"):
        updater.update(dictionary_root, source=gar_factory("20261021"), version="20261021", min_streets=1)
    assert AddressDictionary.open_current(dictionary_root).version == "20261020"


def test_invalid_archive_keeps_active_version(dictionary_root, gar_factory):
    path = gar_factory("20261021", omit=("47", "AS_ADDR_OBJ"))
    with pytest.raises(ValueError, match="должна содержать"):
        updater.update(dictionary_root, source=path, version="20261021", min_streets=1)
    result = updater.status(dictionary_root)
    assert result["active"]["version"] == "20261020"
    assert result["state"]["last_error"]
    assert not list(dictionary_root.glob(".staging-*"))


def test_regression_canary_blocks_publication(dictionary_root, gar_factory):
    path = gar_factory("20261021", rename={100: "Совершенно другая"})
    with pytest.raises(ValueError, match="Контрольный адрес"):
        updater.update(dictionary_root, source=path, version="20261021", min_streets=1)
    assert AddressDictionary.open_current(dictionary_root).version == "20261020"


def test_failure_after_switch_restores_previous_and_quarantines(dictionary_root, gar_factory, monkeypatch):
    real_validate = updater.validate_dictionary
    def validate(path, *args, **kwargs):
        if "current" in path.parts:
            raise ValueError("injected post-switch failure")
        return real_validate(path, *args, **kwargs)
    monkeypatch.setattr(updater, "validate_dictionary", validate)
    with pytest.raises(ValueError, match="post-switch"):
        updater.update(dictionary_root, source=gar_factory("20261021"), version="20261021", min_streets=1)
    assert AddressDictionary.open_current(dictionary_root).version == "20261020"
    assert updater.status(dictionary_root)["state"]["blocked_versions"] == ["20261021"]


def test_concurrent_update_is_rejected(dictionary_root):
    with (dictionary_root / ".update.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="уже выполняется"):
            updater.update(dictionary_root)


def test_orphan_street_is_not_imported(gar_factory, tmp_path):
    database = tmp_path / "test.sqlite"
    with ZipFile(gar_factory(broken_parent=True)) as archive:
        stats = build_dictionary(archive, database, "20261020")
    assert stats["excluded_orphans"] == {"78": 1}
    snapshot = AddressDictionary(database)
    assert not any(s["id"] == 100 for s in snapshot.streets)
    assert snapshot.normalize("Санкт-Петербург, ул Горохвая, д 68").status == "not_found"


def test_count_drop_and_initial_small_import_are_rejected(dictionary_root):
    database = dictionary_root / "current" / "streets.sqlite"
    with pytest.raises(ValueError, match="недостаточно"):
        validate_dictionary(database)
    with pytest.raises(ValueError, match="Необычное"):
        validate_dictionary(database, {"counts": {"78": 500, "47": 500}}, min_streets=1)


def test_broken_current_fails_explicitly(tmp_path):
    (tmp_path / "current").symlink_to("releases/missing")
    with pytest.raises(ValueError, match="повреждён"):
        AddressDictionary.open_current(tmp_path)

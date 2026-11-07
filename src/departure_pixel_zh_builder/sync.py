"""Atomically synchronize a verified font build into a declared consumer."""

import hashlib
import json
import os
import shutil
import unicodedata
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from .build import digest, identity, lock_directory, write_json
from .check import check_build


def _contained(root, relative, label):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
        raise ValueError(f"{label} must remain inside the consumer root")
    root = Path(root).resolve()
    candidate = root / relative
    if any(
        (root.joinpath(*relative.parts[:index])).is_symlink()
        for index in range(1, len(relative.parts) + 1)
    ) or not candidate.parent.resolve().is_relative_to(root):
        raise ValueError(f"Refusing symbolic link or escaped {label}: {relative}")
    return candidate


def _path_identity(value):
    # APFS commonly compares names without regard to case or normalization.
    return unicodedata.normalize("NFD", str(value)).casefold()


def _validate_consumer_key(relative, manifest_name=None):
    if not isinstance(relative, str):
        raise ValueError("Consumer path must be a string")  # noqa: TRY004
    if Path(relative).as_posix() != relative:
        raise ValueError("Consumer path must use a canonical relative form")
    reserved = {
        ".departurepixelzh-sync.lock",
        ".departurepixelzh-sync-journal.json",
        ".departurepixelzh-sync-recovery",
    }
    parts = Path(relative).parts
    if parts and (
        _path_identity(parts[0]) in {_path_identity(name) for name in reserved}
        or (manifest_name and _path_identity(parts[0]) == _path_identity(manifest_name))
    ):
        raise ValueError("Consumer path conflicts with synchronization metadata")


def _manifest(path):
    path = Path(path).resolve()
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "id", "receipt", "font", "files"}
        or value["schema_version"] != 1
        or not isinstance(value["id"], str)
        or not isinstance(value["receipt"], str)
        or not isinstance(value["files"], list)
        or not value["files"]
        or not isinstance(value["font"], dict)
        or set(value["font"]) != {"version", "recipe_sha256", "families"}
        or not isinstance(value["font"]["version"], str)
        or not isinstance(value["font"]["recipe_sha256"], str)
        or not isinstance(value["font"]["families"], list)
    ):
        raise ValueError("Invalid consumer manifest")
    families = value["font"]["families"]
    if (
        not families
        or any(
            not isinstance(family, dict)
            or set(family) != {"postscript_name", "sha256"}
            or not isinstance(family["postscript_name"], str)
            or not re_full_sha(family["sha256"])
            for family in families
        )
        or len({family["postscript_name"] for family in families}) != len(families)
    ):
        raise ValueError("Consumer font pin must name each selected family and checksum")
    _validate_consumer_key(value["receipt"], path.name)
    files = []
    for entry in value["files"]:
        if not isinstance(entry, dict) or set(entry) != {"source", "destination"}:
            raise ValueError("Each consumer file needs source and destination")
        source, destination = entry["source"], entry["destination"]
        if not isinstance(source, str) or not isinstance(destination, str):
            raise TypeError("Consumer file paths must be strings")
        if Path(source).name != source:
            raise ValueError("Consumer sources must be build-root filenames")
        _validate_consumer_key(destination, path.name)
        _contained(path.parent, destination, "consumer destination")
        files.append((source, destination))
    if len({_path_identity(destination) for _, destination in files}) != len(files):
        raise ValueError("Consumer destinations must be unique")
    _contained(path.parent, value["receipt"], "consumer receipt")
    return path, value, files


def re_full_sha(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _read_receipt(path, manifest_id):
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise ValueError("Consumer receipt must be a regular file")
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("manifest_id") != manifest_id
        or not isinstance(value.get("font"), dict)
        or not isinstance(value.get("files"), dict)
    ):
        raise ValueError("Consumer receipt does not match the manifest")
    return value


def _sync_journal(root):
    return _contained(root, ".departurepixelzh-sync-journal.json", "sync journal")


def _sync_recovery_root(root):
    return _contained(root, ".departurepixelzh-sync-recovery", "sync recovery directory")


def pending_sync_recovery(root):
    """Whether a prior sync left recovery state that a read-only call must not change."""
    return _sync_journal(root).exists() or _sync_recovery_root(root).exists()


def _restore_sync_backup(root, recovery, key, checksum, target=None):
    backup = _contained(recovery, key, "synchronization recovery backup")
    if not backup.is_file() or digest(backup) != checksum:
        raise ValueError(f"Synchronization recovery backup is unavailable: {key}")
    if target is None:
        target = _contained(root, key, "synchronization recovery target")
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with NamedTemporaryFile(
            prefix=".departurepixelzh-recovery-", dir=target.parent, delete=False
        ) as stream:
            staged = Path(stream.name)
            with backup.open("rb") as source:
                shutil.copyfileobj(source, stream)
        shutil.copystat(backup, staged)
        os.replace(staged, target)
    finally:
        if staged is not None and staged.exists():
            staged.unlink()


def recover_pending_sync(
    root, receipt, manifest_id, manifest_name=None, allow_retirement_restore=False
):
    """Restore an interrupted synchronization before examining a new consumer state."""
    journal, recovery = _sync_journal(root), _sync_recovery_root(root)
    if not journal.exists():
        if recovery.exists():
            if not recovery.is_dir() or recovery.is_symlink():
                raise ValueError("Synchronization recovery is invalid; recover it manually")
            raise ValueError("Orphaned synchronization recovery; recover it manually")
        return False
    if (
        not journal.is_file()
        or journal.is_symlink()
        or not recovery.is_dir()
        or recovery.is_symlink()
    ):
        raise ValueError("Synchronization journal is invalid; recover it manually")
    value = json.loads(journal.read_text())
    required = {
        "schema_version",
        "manifest_id",
        "receipt_before",
        "receipt_after",
        "changed",
        "retired",
    }
    if (
        not isinstance(value, dict)
        or set(value)
        not in (required, required | {"receipt_before_sha256", "receipt_after_sha256"})
        or value["schema_version"] != 1
        or value["manifest_id"] != manifest_id
        or not isinstance(value["changed"], dict)
        or not isinstance(value["retired"], dict)
        or (value["receipt_before"] is not None and not isinstance(value["receipt_before"], dict))
        or not isinstance(value["receipt_after"], dict)
    ):
        raise ValueError("Synchronization journal is invalid; recover it manually")
    receipt_key = str(receipt.relative_to(root))
    journal_keys = (*value["changed"], *value["retired"])
    if len({_path_identity(key) for key in journal_keys}) != len(journal_keys):
        raise ValueError("Synchronization journal has overlapping records")
    for key in journal_keys:
        _validate_consumer_key(key, manifest_name)
        if _path_identity(key) == _path_identity(receipt_key):
            raise ValueError("Synchronization journal conflicts with consumer receipt")
        _contained(root, key, "synchronization recovery target")
    for key, record in value["changed"].items():
        if (
            not isinstance(record, dict)
            or set(record) not in ({"before", "after"}, {"before", "after", "applied"})
            or ("applied" in record and not isinstance(record["applied"], bool))
            or not re_full_sha(record["after"])
            or (record["before"] is not None and not re_full_sha(record["before"]))
        ):
            raise ValueError("Synchronization journal file record is invalid; recover it manually")
        target = _contained(root, key, "synchronization recovery target")
        if target.exists() and not target.is_file():
            raise ValueError(f"Synchronization recovery target is not a regular file: {key}")
        actual = digest(target) if target.is_file() else None
        if actual == record["before"]:
            continue
        if actual != record["after"] or not record.get("applied", False):
            raise ValueError(f"Synchronization recovery target changed or is ambiguous: {key}")
        if record["before"] is None:
            target.unlink()
        else:
            _restore_sync_backup(root, recovery, key, record["before"])
    for key, retired_record in value["retired"].items():
        # Old journals had only a checksum and cannot prove the unlink occurred.
        if isinstance(retired_record, str):
            checksum, applied = retired_record, False
        elif (
            isinstance(retired_record, dict)
            and set(retired_record) == {"before", "applied"}
            and isinstance(retired_record["applied"], bool)
        ):
            checksum, applied = retired_record["before"], retired_record["applied"]
        else:
            raise ValueError("Synchronization retirement record is invalid; recover it manually")
        if not re_full_sha(checksum):
            raise ValueError(
                "Synchronization journal retirement record is invalid; recover it manually"
            )
        target = _contained(root, key, "synchronization recovery target")
        if target.exists() and not target.is_file():
            raise ValueError(f"Synchronization recovery target is not a regular file: {key}")
        actual = digest(target) if target.is_file() else None
        if actual != checksum:
            if actual is not None or not applied or not allow_retirement_restore:
                raise ValueError(f"Synchronization retirement requires manual recovery: {key}")
            _restore_sync_backup(root, recovery, key, checksum)
    if receipt.is_symlink() or (receipt.exists() and not receipt.is_file()):
        raise ValueError("Synchronization receipt changed during recovery")
    actual_receipt = digest(receipt) if receipt.is_file() else None
    if "receipt_before_sha256" in value:
        before, after = value["receipt_before_sha256"], value["receipt_after_sha256"]
        if (before is not None and not re_full_sha(before)) or not re_full_sha(after):
            raise ValueError("Synchronization receipt journal is invalid")
        if actual_receipt not in (before, after):
            raise ValueError("Synchronization receipt changed during recovery")
        if actual_receipt != before:
            if before is None:
                receipt.unlink()
            else:
                _restore_sync_backup(
                    root, recovery, ".departurepixelzh-sync-journal.json", before, target=receipt
                )
    else:
        # Legacy journals lack exact-byte identity and cannot safely reconstruct a
        # changed receipt. Leave it for manual recovery instead of rewriting it.
        current = json.loads(receipt.read_text()) if receipt.is_file() else None
        if current != value["receipt_before"]:
            raise ValueError("Legacy synchronization receipt needs manual recovery")
    journal.unlink()
    shutil.rmtree(recovery)
    return True


def sync(recipe_path, manifest_path, output, dry_run=False, check=False):
    manifest_path, manifest, entries = _manifest(manifest_path)
    root, output = manifest_path.parent, Path(output).resolve()
    receipt = _contained(root, manifest["receipt"], "consumer receipt")
    with lock_directory(root, ".departurepixelzh-sync.lock"):
        if dry_run or check:
            if pending_sync_recovery(root):
                raise ValueError(
                    "Synchronization recovery is pending; run sync without --dry-run or --check"
                )
        else:
            recover_pending_sync(root, receipt, manifest["id"], manifest_path.name)
    build_report = check_build(recipe_path, output)
    recipe = json.loads(Path(recipe_path).read_text())
    provenance = json.loads((output / "provenance.json").read_text())
    expected_font = manifest["font"]
    if (
        expected_font["version"] != provenance["version"]
        or expected_font["recipe_sha256"] != build_report["recipe_sha256"]
    ):
        raise ValueError("Consumer manifest does not pin this font version and recipe")
    output_families = {
        variant.get("postscript_name", variant["family"]): variant["files"].get(
            variant.get("postscript_name", variant["family"]) + "-Regular.ttf"
        )
        for variant in provenance["variants"]
    }
    manifest_families = {
        family["postscript_name"]: family["sha256"] for family in expected_font["families"]
    }
    if manifest_families != output_families:
        raise ValueError("Consumer manifest font-family checksums do not match the verified build")
    required_manifest_sources = {"NOTICE.md", "OFL.txt", "provenance.json"}
    declared_sources = {source for source, _ in entries}
    if not required_manifest_sources <= declared_sources:
        raise ValueError("Consumer manifest must include NOTICE.md, OFL.txt, and provenance.json")
    wanted = {}
    for source, relative_destination in entries:
        source_path = output / source
        if not source_path.is_file():
            raise ValueError(f"Verified build does not contain {source}")
        destination = _contained(root, relative_destination, "consumer destination")
        if _path_identity(destination.relative_to(root)) == _path_identity(
            receipt.relative_to(root)
        ):
            raise ValueError("Consumer receipt cannot overwrite a synchronized asset")
        wanted[destination] = {"source": source, "sha256": digest(source_path)}
    license_root = output / "Licenses"
    if license_root.exists() and not license_root.is_dir():
        raise ValueError("Verified build license directory is not a directory")
    if license_root.is_dir():
        for source_path in sorted(path for path in license_root.rglob("*") if path.is_file()):
            source = str(source_path.relative_to(output))
            relative_destination = Path("Licenses") / source_path.relative_to(license_root)
            destination = _contained(root, relative_destination, "consumer license destination")
            if destination in wanted:
                raise ValueError(
                    f"Consumer manifest conflicts with required component notice: {source}"
                )
            wanted[destination] = {"source": source, "sha256": digest(source_path)}
    wanted_keys = [str(path.relative_to(root)) for path in wanted]
    wanted_identity_list = [_path_identity(key) for key in wanted_keys]
    if len(set(wanted_identity_list)) != len(wanted_keys):
        raise ValueError("Consumer destinations must be unique")
    if _path_identity(receipt.relative_to(root)) in wanted_identity_list:
        raise ValueError("Consumer receipt cannot overwrite a synchronized asset")
    previous = _read_receipt(receipt, manifest["id"])
    receipt_before_sha = digest(receipt) if receipt.is_file() else None
    previous_files = previous["files"] if previous else {}
    previous_identities = set()
    wanted_keys = {str(path.relative_to(root)) for path in wanted}
    wanted_identities = {_path_identity(key): key for key in wanted_keys}
    for key in previous_files:
        _validate_consumer_key(key, manifest_path.name)
        _contained(root, key, "previous consumer target")
        key_id = _path_identity(key)
        if key_id == _path_identity(receipt.relative_to(root)) or key_id in previous_identities:
            raise ValueError("Previous consumer target conflicts with receipt or another target")
        if key_id in wanted_identities and key != wanted_identities[key_id]:
            raise ValueError("Previous consumer target aliases a current destination")
        previous_identities.add(key_id)
    changes, unchanged, observed = [], [], {}
    for destination, record in wanted.items():
        key = str(destination.relative_to(root))
        actual = digest(destination) if destination.is_file() else None
        if destination.exists() and not destination.is_file():
            raise ValueError(f"Consumer destination is not a regular file: {key}")
        if actual == record["sha256"]:
            unchanged.append(key)
        elif (
            key in previous_files and actual == previous_files[key].get("sha256") or actual is None
        ):
            changes.append(key)
        else:
            raise ValueError(f"Refusing to replace a modified or unmanaged consumer file: {key}")
        observed[key] = actual
    retired = [
        key for key in previous_files if key not in {str(path.relative_to(root)) for path in wanted}
    ]
    retired_observed = {}
    for key in retired:
        path = _contained(root, key, "consumer retirement target")
        actual = digest(path) if path.is_file() else None
        if path.exists() and not path.is_file():
            raise ValueError(f"Consumer retirement target is not a regular file: {key}")
        if actual is not None and actual != previous_files[key].get("sha256"):
            raise ValueError(f"Refusing to retire modified consumer file: {key}")
        retired_observed[key] = actual
    result = {
        "changed": changes,
        "unchanged": unchanged,
        "retired": retired,
        "receipt": str(receipt),
        "recipe_sha256": build_report["recipe_sha256"],
        "font_version": provenance["version"],
    }
    if dry_run:
        return {**result, "dry_run": True}
    if check:
        if changes or retired or previous is None:
            raise ValueError("Consumer assets do not match the verified build")
        return {**result, "checked": True}
    with (
        lock_directory(root, ".departurepixelzh-sync.lock"),
        TemporaryDirectory(prefix=".departurepixelzh-sync-", dir=root) as temporary,
    ):
        stage = Path(temporary)
        # The preflight ran before acquiring the lock so it does not hold a consumer
        # hostage while checking the build. Re-read every managed path under the lock;
        # otherwise a concurrent editor could replace a previously managed file between
        # preflight and os.replace.
        for destination in wanted:
            key = str(destination.relative_to(root))
            if destination.exists() and not destination.is_file():
                raise ValueError(f"Consumer destination changed during synchronization: {key}")
            actual = digest(destination) if destination.is_file() else None
            if actual != observed[key]:
                raise ValueError(f"Consumer file changed during synchronization: {key}")
        for key in retired:
            path = _contained(root, key, "consumer retirement target")
            if path.exists() and not path.is_file():
                raise ValueError(
                    f"Consumer retirement target changed during synchronization: {key}"
                )
            actual = digest(path) if path.is_file() else None
            if actual != retired_observed[key]:
                raise ValueError(f"Consumer retirement changed during synchronization: {key}")
        if (
            _read_receipt(receipt, manifest["id"]) != previous
            or (digest(receipt) if receipt.is_file() else None) != receipt_before_sha
        ):
            raise ValueError("Consumer receipt changed during synchronization")
        old = {}
        for destination in wanted:
            key = str(destination.relative_to(root))
            if destination.is_file():
                old[key] = stage / f"old-{len(old)}"
                shutil.copy2(destination, old[key])
        for key in retired:
            path = _contained(root, key, "consumer retirement target")
            if path.is_file():
                old[key] = stage / f"old-{len(old)}"
                shutil.copy2(path, old[key])
        files = {
            str(destination.relative_to(root)): record for destination, record in wanted.items()
        }
        new_receipt = {
            "schema_version": 1,
            "manifest_id": manifest["id"],
            "font": {"version": provenance["version"], "recipe_sha256": identity(recipe)},
            "files": files,
        }
        journal, recovery = _sync_journal(root), _sync_recovery_root(root)
        if journal.exists() or recovery.exists():
            raise ValueError("Unexpected synchronization recovery state")
        recovery.mkdir()
        if receipt_before_sha is not None:
            shutil.copy2(receipt, recovery / ".departurepixelzh-sync-journal.json")
            if digest(recovery / ".departurepixelzh-sync-journal.json") != receipt_before_sha:
                raise ValueError("Consumer receipt changed while preparing recovery")
        for key, backup in old.items():
            durable = recovery / key
            durable.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, durable)
        transaction = {
            "schema_version": 1,
            "manifest_id": manifest["id"],
            "receipt_before": previous,
            "receipt_after": new_receipt,
            "receipt_before_sha256": receipt_before_sha,
            "receipt_after_sha256": hashlib.sha256(
                (json.dumps(new_receipt, indent=2) + "\n").encode()
            ).hexdigest(),
            "changed": {},
            "retired": {},
        }
        write_json(journal, transaction)
        if json.loads(journal.read_text()) != transaction:
            raise ValueError("Synchronization journal readback failed")
        applied_changes = set()
        try:
            for destination, record in wanted.items():
                key = str(destination.relative_to(root))
                if key not in changes:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                staged = stage / f"new-{len(old)}-{destination.name}"
                shutil.copy2(output / record["source"], staged)
                if digest(staged) != record["sha256"]:
                    raise ValueError(f"Staged file checksum failed: {destination.name}")
                actual = digest(destination) if destination.is_file() else None
                if actual != observed[key] or (destination.exists() and not destination.is_file()):
                    raise ValueError(f"Consumer file changed during synchronization: {key}")
                transaction["changed"][key] = {
                    "before": observed[key],
                    "after": record["sha256"],
                    "applied": False,
                }
                write_json(journal, transaction)
                if destination.is_symlink() or (destination.exists() and not destination.is_file()):
                    raise ValueError(f"Consumer file changed during synchronization: {key}")
                if (digest(destination) if destination.is_file() else None) != observed[key]:
                    raise ValueError(f"Consumer file changed during synchronization: {key}")
                os.replace(staged, destination)
                applied_changes.add(key)
                transaction["changed"][key]["applied"] = True
                write_json(journal, transaction)
                if digest(destination) != record["sha256"]:
                    raise ValueError(f"Synced file checksum failed: {destination.name}")
            for key in retired:
                path = _contained(root, key, "consumer retirement target")
                if path.exists():
                    actual = digest(path) if path.is_file() else None
                    if actual != retired_observed[key] or not path.is_file():
                        raise ValueError(
                            f"Consumer retirement changed during synchronization: {key}"
                        )
                    transaction["retired"][key] = {
                        "before": retired_observed[key],
                        "applied": False,
                    }
                    write_json(journal, transaction)
                    if (
                        path.is_symlink()
                        or not path.is_file()
                        or digest(path) != retired_observed[key]
                    ):
                        raise ValueError(
                            f"Consumer retirement changed during synchronization: {key}"
                        )
                    path.unlink()
                    transaction["retired"][key]["applied"] = True
                    write_json(journal, transaction)
            for destination, record in wanted.items():
                key = str(destination.relative_to(root))
                if (
                    destination.is_symlink()
                    or not destination.is_file()
                    or digest(destination) != record["sha256"]
                ):
                    raise ValueError(f"Consumer file changed during synchronization: {key}")
            for key in retired:
                path = _contained(root, key, "consumer retirement target")
                if path.exists():
                    raise ValueError(f"Consumer retirement changed during synchronization: {key}")
            if (
                _read_receipt(receipt, manifest["id"]) != previous
                or (digest(receipt) if receipt.is_file() else None) != receipt_before_sha
            ):
                raise ValueError("Consumer receipt changed during synchronization")
            write_json(receipt, new_receipt)
            if json.loads(receipt.read_text()) != new_receipt:
                raise ValueError("Synchronization receipt readback failed")
            journal.unlink()
        except BaseException:
            try:
                # An armed operation that did not return is ambiguous. Never
                # infer ownership from an after-hash in the live failure path.
                for key, record in transaction["changed"].items():
                    if key not in applied_changes:
                        target = _contained(root, key, "synchronization recovery target")
                        actual = digest(target) if target.is_file() else None
                        if actual != record["before"]:
                            raise ValueError(f"Synchronization operation is ambiguous: {key}")
                recover_pending_sync(
                    root, receipt, manifest["id"], manifest_path.name, allow_retirement_restore=True
                )
            except BaseException as recovery_error:
                raise ValueError(
                    "Synchronization failed and recovery needs attention; "
                    "inspect the journal and recovery directory"
                ) from recovery_error
            raise
        # The journal removal commits the verified receipt. An interrupted cleanup
        # leaves an orphan directory for manual inspection, not a rollback journal.
        shutil.rmtree(recovery)

    return result

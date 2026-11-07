"""Consumer synchronization keeps declared font assets, notices, and provenance together."""

import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from departure_pixel_zh_builder import build, sync
from departure_pixel_zh_builder.build import digest, identity
from tests import test_pipeline


class SyncTests(unittest.TestCase):
    def setUp(self):
        test_pipeline.PipelineTests.setUp(self)
        self.addCleanup(self.temp.cleanup)
        build.run_build(self.preset, self.output, formats=("ttf", "woff2"))
        self.consumer = self.root / "SampleConsumer"
        self.consumer.mkdir()
        self.manifest = self.consumer / "font-dependency.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "sample-consumer",
                    "receipt": ".departurepixelzh-sync.json",
                    "font": {
                        "version": "0.1.0",
                        "recipe_sha256": identity(json.loads(self.preset.read_text())),
                        "families": [
                            {
                                "postscript_name": "OneFirst",
                                "sha256": digest(self.output / "OneFirst-Regular.ttf"),
                            },
                            {
                                "postscript_name": "TwoFirst",
                                "sha256": digest(self.output / "TwoFirst-Regular.ttf"),
                            },
                        ],
                    },
                    "files": [
                        {
                            "source": "OneFirst-Regular.ttf",
                            "destination": "Assets/Fonts/OneFirst-Regular.ttf",
                        },
                        {"source": "NOTICE.md", "destination": "Licenses/NOTICE.md"},
                        {"source": "OFL.txt", "destination": "Licenses/OFL.txt"},
                        {"source": "provenance.json", "destination": "Licenses/provenance.json"},
                    ],
                }
            )
        )
        (self.output / "NOTICE.md").write_text("fixture notice\n")
        (self.output / "OFL.txt").write_text("fixture license\n")
        (self.output / "Licenses").mkdir()
        (self.output / "Licenses" / "Component.txt").write_text("component notice\n")

    def apply(self, **kwargs):
        return sync.sync(self.preset, self.manifest, self.output, **kwargs)

    def test_sync_records_font_pin_and_check_is_read_only(self):
        first = self.apply()
        self.assertEqual(len(first["changed"]), 5)
        receipt = json.loads((self.consumer / ".departurepixelzh-sync.json").read_text())
        self.assertEqual(receipt["font"]["version"], "0.1.0")
        self.assertIn("recipe_sha256", receipt["font"])
        self.assertTrue((self.consumer / "Licenses/Component.txt").is_file())
        before = (self.consumer / "Assets/Fonts/OneFirst-Regular.ttf").read_bytes()
        checked = self.apply(check=True)
        self.assertTrue(checked["checked"])
        self.assertEqual(before, (self.consumer / "Assets/Fonts/OneFirst-Regular.ttf").read_bytes())

    def test_modified_destination_stops_before_other_writes(self):
        self.apply()
        font = self.consumer / "Assets/Fonts/OneFirst-Regular.ttf"
        notice = self.consumer / "Licenses/NOTICE.md"
        font.write_bytes(b"modified")
        before = notice.read_bytes()
        with self.assertRaisesRegex(ValueError, "modified or unmanaged"):
            self.apply()
        self.assertEqual(notice.read_bytes(), before)

    def test_manifest_font_drift_stops_before_writes(self):
        value = json.loads(self.manifest.read_text())
        value["font"]["families"][0]["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "font-family checksums"):
            self.apply()
        self.assertFalse((self.consumer / "Assets").exists())

    def test_path_escape_and_symlink_parent_are_rejected(self):
        value = json.loads(self.manifest.read_text())
        value["receipt"] = "../escaped.json"
        self.manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "consumer receipt"):
            self.apply(dry_run=True)
        value["receipt"] = ".departurepixelzh-sync.json"
        value["files"][0]["destination"] = "Linked/font.ttf"
        self.manifest.write_text(json.dumps(value))
        outside = self.root / "Outside"
        outside.mkdir()
        (self.consumer / "Linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link or escaped"):
            self.apply(dry_run=True)

    def test_replacement_failure_rolls_back_all_consumer_files(self):
        self.apply()
        font = self.consumer / "Assets/Fonts/OneFirst-Regular.ttf"
        notice = self.consumer / "Licenses/NOTICE.md"
        receipt = self.consumer / ".departurepixelzh-sync.json"
        before = {path: path.read_bytes() for path in (font, notice, receipt)}
        (self.output / "NOTICE.md").write_text("changed fixture notice\n")
        self.assertIn("Licenses/NOTICE.md", self.apply(dry_run=True)["changed"])
        original = sync.os.replace
        replacements = 0

        def fail_second_staged_replacement(source, destination):
            nonlocal replacements
            if Path(source).name.startswith("new-"):
                replacements += 1
            if replacements == 1:
                raise OSError("injected consumer replacement failure")
            return original(source, destination)

        with (
            patch.object(sync.os, "replace", side_effect=fail_second_staged_replacement),
            self.assertRaisesRegex(OSError, "injected consumer replacement failure"),
        ):
            self.apply()
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_failure_preserves_concurrent_edit_to_untouched_asset(self):
        self.apply()
        font = self.consumer / "Assets/Fonts/OneFirst-Regular.ttf"
        (self.output / "NOTICE.md").write_text("changed fixture notice\n")
        original = sync.os.replace

        def edit_then_fail(source, destination):
            if Path(source).name.startswith("new-"):
                font.write_bytes(b"concurrent edit")
                raise OSError("injected failure")
            return original(source, destination)

        with (
            patch.object(sync.os, "replace", side_effect=edit_then_fail),
            self.assertRaisesRegex(OSError, "injected failure"),
        ):
            self.apply()
        self.assertEqual(font.read_bytes(), b"concurrent edit")

    def test_conflict_during_rollback_retains_journal_and_backup(self):
        self.apply()
        notice = self.consumer / "Licenses/NOTICE.md"
        (self.output / "NOTICE.md").write_text("changed fixture notice\n")
        original = sync.os.replace

        def edit_after_replace(source, destination):
            result = original(source, destination)
            if Path(source).name.startswith("new-"):
                notice.write_bytes(b"concurrent edit")
                raise OSError("injected failure")
            return result

        with (
            patch.object(sync.os, "replace", side_effect=edit_after_replace),
            self.assertRaisesRegex(ValueError, "inspect the journal and recovery directory"),
        ):
            self.apply()
        self.assertEqual(notice.read_bytes(), b"concurrent edit")
        self.assertTrue((self.consumer / ".departurepixelzh-sync-journal.json").is_file())
        self.assertTrue(
            (self.consumer / ".departurepixelzh-sync-recovery/Licenses/NOTICE.md").is_file()
        )
        with self.assertRaisesRegex(ValueError, "target changed"):
            self.apply()
        self.assertEqual(notice.read_bytes(), b"concurrent edit")

    def test_process_interruption_after_replacement_then_retry(self):
        self.apply()
        notice = self.consumer / "Licenses/NOTICE.md"
        (self.output / "NOTICE.md").write_text("changed fixture notice\n")
        pid = os.fork()
        if pid == 0:
            original = sync.os.replace

            def interrupt_after_replace(source, destination):
                result = original(source, destination)
                if Path(source).name.startswith("new-"):
                    os._exit(73)
                return result

            with patch.object(sync.os, "replace", side_effect=interrupt_after_replace):
                self.apply()
            os._exit(74)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        self.assertTrue((self.consumer / ".departurepixelzh-sync-journal.json").is_file())
        self.assertEqual(notice.read_text(), "changed fixture notice\n")
        with self.assertRaisesRegex(ValueError, "changed or is ambiguous"):
            self.apply()
        notice.write_bytes(
            (self.consumer / ".departurepixelzh-sync-recovery/Licenses/NOTICE.md").read_bytes()
        )
        self.apply()
        self.assertEqual(notice.read_text(), "changed fixture notice\n")
        self.assertFalse((self.consumer / ".departurepixelzh-sync-journal.json").exists())
        self.assertTrue(self.apply(check=True)["checked"])

    def test_process_interruption_after_retirement_then_retry(self):
        self.apply()
        target = self.consumer / "Licenses/Component.txt"
        (self.output / "Licenses/Component.txt").unlink()
        pid = os.fork()
        if pid == 0:
            original = Path.unlink

            def interrupt_after_unlink(path, *args, **kwargs):
                result = original(path, *args, **kwargs)
                if path.resolve() == target.resolve():
                    os._exit(73)
                return result

            with patch.object(Path, "unlink", interrupt_after_unlink):
                self.apply()
            os._exit(74)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        self.assertFalse(target.exists())
        with self.assertRaisesRegex(ValueError, "retirement requires manual recovery"):
            self.apply()
        # Simulate inspection and restoration of the retained backup before retry.
        target.write_bytes(
            (self.consumer / ".departurepixelzh-sync-recovery/Licenses/Component.txt").read_bytes()
        )
        self.apply()
        self.assertFalse(target.exists())
        self.assertTrue(self.apply(check=True)["checked"])

    def test_process_interruption_after_receipt_then_retry(self):
        self.apply()
        receipt = self.consumer / ".departurepixelzh-sync.json"
        (self.output / "NOTICE.md").write_text("changed fixture notice\n")
        pid = os.fork()
        if pid == 0:
            original = sync.os.replace

            def interrupt_after_receipt(source, destination):
                result = original(source, destination)
                if Path(destination).resolve() == receipt.resolve():
                    os._exit(73)
                return result

            with patch.object(sync.os, "replace", side_effect=interrupt_after_receipt):
                self.apply()
            os._exit(74)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        self.assertTrue((self.consumer / ".departurepixelzh-sync-journal.json").is_file())
        self.apply()
        self.assertTrue(self.apply(check=True)["checked"])

    def test_concurrent_unchanged_edit_prevents_receipt_update(self):
        self.apply()
        font = self.consumer / "Assets/Fonts/OneFirst-Regular.ttf"
        notice = self.consumer / "Licenses/NOTICE.md"
        old_notice = notice.read_bytes()
        receipt = self.consumer / ".departurepixelzh-sync.json"
        old_receipt = receipt.read_bytes()
        (self.output / "NOTICE.md").write_text("changed fixture notice\n")
        original = sync.os.replace

        def edit_after_replace(source, destination):
            result = original(source, destination)
            if Path(source).name.startswith("new-"):
                font.write_bytes(b"concurrent edit")
            return result

        with (
            patch.object(sync.os, "replace", side_effect=edit_after_replace),
            self.assertRaisesRegex(ValueError, "changed during synchronization"),
        ):
            self.apply()
        self.assertEqual(font.read_bytes(), b"concurrent edit")
        self.assertEqual(notice.read_bytes(), old_notice)
        self.assertEqual(receipt.read_bytes(), old_receipt)

    def test_concurrent_receipt_change_survives_failure(self):
        self.apply()
        receipt = self.consumer / ".departurepixelzh-sync.json"
        changed = json.loads(receipt.read_text())
        changed["user_edit"] = True
        (self.output / "NOTICE.md").write_text("changed fixture notice\n")
        original = sync.os.replace

        def edit_receipt(source, destination):
            result = original(source, destination)
            if Path(source).name.startswith("new-"):
                receipt.write_text(json.dumps(changed))
            return result

        with (
            patch.object(sync.os, "replace", side_effect=edit_receipt),
            self.assertRaisesRegex(ValueError, "inspect the journal and recovery directory"),
        ):
            self.apply()
        self.assertEqual(json.loads(receipt.read_text()), changed)
        self.assertTrue((self.consumer / ".departurepixelzh-sync-journal.json").is_file())

    def test_reserved_sync_paths_are_rejected(self):
        value = json.loads(self.manifest.read_text())
        for destination in (
            ".departurepixelzh-sync-journal.json",
            ".departurepixelzh-sync-recovery/file",
            ".departurepixelzh-sync.lock",
            ".DEPARTUREPIXELZH-SYNC-JOURNAL.JSON",
        ):
            value["files"][0]["destination"] = destination
            self.manifest.write_text(json.dumps(value))
            with (
                self.subTest(destination=destination),
                self.assertRaisesRegex(ValueError, "conflicts with synchronization metadata"),
            ):
                self.apply()

    def test_concurrent_receipt_format_edit_is_preserved(self):
        self.apply()
        receipt = self.consumer / ".departurepixelzh-sync.json"
        edited = json.dumps(json.loads(receipt.read_text()), separators=(",", ":")).encode()
        (self.output / "NOTICE.md").write_text("changed fixture notice\n")
        original = sync.os.replace

        def edit_after_replace(source, destination):
            result = original(source, destination)
            if Path(source).name.startswith("new-"):
                receipt.write_bytes(edited)
            return result

        with (
            patch.object(sync.os, "replace", side_effect=edit_after_replace),
            self.assertRaisesRegex(ValueError, "inspect the journal and recovery directory"),
        ):
            self.apply()
        self.assertEqual(receipt.read_bytes(), edited)

    def test_journal_cannot_case_alias_receipt(self):
        self.apply()
        receipt = self.consumer / ".departurepixelzh-sync.json"
        value = json.loads(receipt.read_text())
        recovery = self.consumer / ".departurepixelzh-sync-recovery"
        recovery.mkdir()
        journal = self.consumer / ".departurepixelzh-sync-journal.json"
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "manifest_id": "sample-consumer",
                    "receipt_before": value,
                    "receipt_after": value,
                    "changed": {
                        ".DEPARTUREPIXELZH-SYNC.JSON": {
                            "before": None,
                            "after": digest(receipt),
                            "applied": True,
                        }
                    },
                    "retired": {},
                }
            )
        )
        with self.assertRaisesRegex(ValueError, "conflicts with consumer receipt"):
            self.apply()
        self.assertTrue(receipt.is_file())
        self.assertTrue(journal.is_file())

    def test_journal_cannot_alias_receipt(self):
        self.apply()
        receipt = self.consumer / ".departurepixelzh-sync.json"
        value = json.loads(receipt.read_text())
        recovery = self.consumer / ".departurepixelzh-sync-recovery"
        recovery.mkdir()
        journal = self.consumer / ".departurepixelzh-sync-journal.json"
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "manifest_id": "sample-consumer",
                    "receipt_before": value,
                    "receipt_after": value,
                    "changed": {
                        "./.departurepixelzh-sync.json": {
                            "before": None,
                            "after": digest(receipt),
                            "applied": True,
                        }
                    },
                    "retired": {},
                }
            )
        )
        before = receipt.read_bytes()
        with self.assertRaisesRegex(ValueError, "canonical relative form"):
            self.apply()
        self.assertEqual(receipt.read_bytes(), before)
        self.assertTrue(journal.exists())
        self.assertTrue(recovery.exists())

    def test_previous_receipt_cannot_retire_metadata(self):
        self.apply()
        receipt = self.consumer / ".departurepixelzh-sync.json"
        original_manifest = self.manifest.read_bytes()
        value = json.loads(receipt.read_text())
        value["files"][self.manifest.name] = {"sha256": digest(self.manifest)}
        receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "conflicts with synchronization metadata"):
            self.apply()
        self.assertEqual(self.manifest.read_bytes(), original_manifest)

    def test_retirement_deletion_during_intent_preserves_conflict(self):
        self.apply()
        target = self.consumer / "Licenses/Component.txt"
        (self.output / "Licenses/Component.txt").unlink()
        original = sync.write_json

        def edit_after_intent(path, value):
            original(path, value)
            if value.get("retired", {}).get("Licenses/Component.txt") == {
                "before": digest(target),
                "applied": False,
            }:
                target.unlink()

        with (
            patch.object(sync, "write_json", side_effect=edit_after_intent),
            self.assertRaisesRegex(ValueError, "inspect the journal and recovery directory"),
        ):
            self.apply()
        self.assertFalse(target.exists())
        self.assertTrue((self.consumer / ".departurepixelzh-sync-journal.json").is_file())
        self.assertTrue(
            (self.consumer / ".departurepixelzh-sync-recovery/Licenses/Component.txt").is_file()
        )

    def test_file_changed_after_preflight_is_not_replaced(self):
        self.apply()
        font = self.consumer / "Assets/Fonts/OneFirst-Regular.ttf"

        class ChangeAtLock:
            def __enter__(self):
                font.write_bytes(b"concurrent edit")

            def __exit__(self, *args):
                return False

        with (
            patch.object(sync, "lock_directory", return_value=ChangeAtLock()),
            self.assertRaisesRegex(
                ValueError, "(changed during synchronization|modified or unmanaged)"
            ),
        ):
            self.apply()
        self.assertEqual(font.read_bytes(), b"concurrent edit")

    def test_incomplete_sync_journal_is_restored_before_the_next_sync(self):
        self.apply()
        receipt_path = self.consumer / ".departurepixelzh-sync.json"
        receipt_before = json.loads(receipt_path.read_text())
        target = self.consumer / "Licenses" / "NOTICE.md"
        old_bytes, old_sha256 = target.read_bytes(), digest(target)
        new_bytes = b"interrupted replacement\n"
        new_sha256 = hashlib.sha256(new_bytes).hexdigest()
        target.write_bytes(new_bytes)
        recovery = self.consumer / ".departurepixelzh-sync-recovery" / "Licenses"
        recovery.mkdir(parents=True)
        (recovery / "NOTICE.md").write_bytes(old_bytes)
        journal = self.consumer / ".departurepixelzh-sync-journal.json"
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "manifest_id": "sample-consumer",
                    "receipt_before": receipt_before,
                    "receipt_after": receipt_before,
                    "changed": {"Licenses/NOTICE.md": {"before": old_sha256, "after": new_sha256}},
                    "retired": {},
                }
            )
        )

        with self.assertRaisesRegex(ValueError, "changed or is ambiguous"):
            sync.recover_pending_sync(self.consumer, receipt_path, "sample-consumer")
        target.write_bytes(old_bytes)
        self.assertTrue(sync.recover_pending_sync(self.consumer, receipt_path, "sample-consumer"))

        self.assertEqual(target.read_bytes(), old_bytes)
        self.assertEqual(json.loads(receipt_path.read_text()), receipt_before)
        self.assertFalse(journal.exists())
        self.assertFalse(recovery.parent.exists())

    def test_read_only_modes_report_pending_recovery_without_changing_it(self):
        self.apply()
        receipt_path = self.consumer / ".departurepixelzh-sync.json"
        receipt_before = json.loads(receipt_path.read_text())
        target = self.consumer / "Licenses" / "NOTICE.md"
        old_bytes, old_sha256 = target.read_bytes(), digest(target)
        new_bytes = b"interrupted replacement\n"
        new_sha256 = hashlib.sha256(new_bytes).hexdigest()
        target.write_bytes(new_bytes)
        recovery = self.consumer / ".departurepixelzh-sync-recovery" / "Licenses"
        recovery.mkdir(parents=True)
        (recovery / "NOTICE.md").write_bytes(old_bytes)
        journal = self.consumer / ".departurepixelzh-sync-journal.json"
        journal.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "manifest_id": "sample-consumer",
                    "receipt_before": receipt_before,
                    "receipt_after": receipt_before,
                    "changed": {"Licenses/NOTICE.md": {"before": old_sha256, "after": new_sha256}},
                    "retired": {},
                }
            )
        )
        before = {
            path.relative_to(self.consumer): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.consumer.rglob("*")
            if path.is_file()
        }

        for mode in ({"dry_run": True}, {"check": True}):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "recovery is pending"):
                self.apply(**mode)
            after = {
                path.relative_to(self.consumer): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.consumer.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_normal_sync_preserves_an_orphaned_recovery_root(self):
        self.apply()
        recovery = self.consumer / ".departurepixelzh-sync-recovery"
        recovery.mkdir()
        (recovery / "staged-backup").write_bytes(b"orphaned recovery data")

        with self.assertRaisesRegex(ValueError, "Orphaned synchronization recovery"):
            self.apply()
        self.assertEqual((recovery / "staged-backup").read_bytes(), b"orphaned recovery data")


if __name__ == "__main__":
    unittest.main()

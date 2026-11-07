"""Protect unrelated files when staging build and receipt writes."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from departure_pixel_zh_builder import build


class AtomicWriteTests(unittest.TestCase):
    def test_existing_pending_and_colliding_symlink_are_preserved(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "result"
            target.write_bytes(b"old")
            victim = root / "victim"
            victim.write_bytes(b"keep")
            legacy = root / "result.pending"
            legacy.symlink_to(victim)
            collision = root / ".builder-collision.pending"
            collision.symlink_to(victim)
            with patch.object(build.secrets, "token_hex", side_effect=["collision", "fresh"]):
                build.write_bytes(target, b"new")
            self.assertEqual(target.read_bytes(), b"new")
            self.assertEqual(victim.read_bytes(), b"keep")
            self.assertTrue(legacy.is_symlink())
            self.assertTrue(collision.is_symlink())
            self.assertFalse((root / ".builder-fresh.pending").exists())

    def test_failed_replace_preserves_output_and_removes_own_staging(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "result"
            target.write_bytes(b"old")
            with (
                patch.object(Path, "replace", side_effect=OSError("interrupted")),
                self.assertRaisesRegex(OSError, "interrupted"),
            ):
                build.write_bytes(target, b"new")
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(root.iterdir()), [target])
            build.write_bytes(target, b"new")
            self.assertEqual(target.read_bytes(), b"new")

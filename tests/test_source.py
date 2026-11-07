"""Source delivery must inspect staged bytes and export only reviewed files."""

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from departure_pixel_zh_builder.source import audit_index


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source with spaces"
        self.root.mkdir()
        self.git("init", "-q")
        (self.root / "README.md").write_text("Public source\n")
        (self.root / "public-source.json").write_text(
            json.dumps({"schema_version": 1, "files": ["README.md", "public-source.json"]})
        )
        self.git("add", "README.md", "public-source.json")

    def git(self, *arguments):
        subprocess.run(["git", "-C", str(self.root), *arguments], check=True, capture_output=True)

    def test_staged_private_bytes_cannot_be_hidden_by_clean_worktree(self):
        (self.root / "README.md").write_text("/" + "Users/example/data\n")
        self.git("add", "README.md")
        (self.root / "README.md").write_text("Public source\n")
        with self.assertRaisesRegex(ValueError, "private absolute path"):
            audit_index(self.root)

    def test_export_reads_index_and_preserves_existing_destination(self):
        (self.root / "README.md").write_text("unstaged edit\n")
        (self.root / "private-note.txt").write_text("not staged\n")
        destination = self.root.parent / "candidate with spaces"
        report = audit_index(self.root, destination)
        self.assertEqual(report["scope"], "index")
        self.assertNotIn("root", report)
        self.assertEqual((destination / "README.md").read_text(), "Public source\n")
        self.assertEqual(
            {path.name for path in destination.iterdir()},
            {"README.md", "public-source.json", "SOURCE-RECEIPT.json"},
        )
        with self.assertRaisesRegex(ValueError, "already exists"):
            audit_index(self.root, destination)
        self.assertEqual((destination / "README.md").read_text(), "Public source\n")

    def test_new_staged_file_requires_allowlist_review(self):
        (self.root / "extra.txt").write_text("review me\n")
        self.git("add", "extra.txt")
        with self.assertRaisesRegex(ValueError, "allowlist"):
            audit_index(self.root, self.root.parent / "candidate")

    def test_index_symlinks_are_rejected_even_when_checkout_replaced(self):
        (self.root / "link").symlink_to("README.md")
        self.git("add", "link")
        (self.root / "link").unlink()
        (self.root / "link").write_text("ordinary file\n")
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            audit_index(self.root)

    def test_control_character_path_is_rejected(self):
        (self.root / "unexpected\nname").write_text("ordinary file\n")
        self.git("add", "unexpected\nname")
        with self.assertRaisesRegex(ValueError, "Unsafe source path"):
            audit_index(self.root)

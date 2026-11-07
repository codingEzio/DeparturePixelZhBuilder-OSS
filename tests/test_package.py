"""Release archives contain exactly the verified delivery allowlist."""

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from departure_pixel_zh_builder import package
from departure_pixel_zh_builder.check import verify_document_references


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recipe_path = self.root / "recipe.json"
        self.output = self.root / "Build"
        self.output.mkdir()
        self.destination = self.root / "dist"
        self.recipe = {
            "version": "1.2.3",
            "families": [{"name": "Sample", "postscript_name": "SamplePS"}],
            "documents": [
                "README.md",
                "NOTICE.md",
                "OFL.txt",
                "specimens/index.html",
                "specimens/outline.svg",
            ],
            "license_directory": "licenses",
        }
        (self.root / "licenses").mkdir()
        (self.root / "licenses" / "Required.txt").write_text("required notice\n")
        for name, content in {
            "SamplePS-Regular.ttf": b"ttf",
            "SamplePS-Regular.woff2": b"woff2",
            "SamplePS_coverage.json": b"{}\n",
            "README.md": b"readme\n",
            "NOTICE.md": b"notice\n",
            "OFL.txt": b"ofl\n",
            "provenance.json": b'{"version": "1.2.3"}\n',
        }.items():
            (self.output / name).write_bytes(content)
        (self.output / "specimens").mkdir()
        (self.output / "specimens" / "index.html").write_text("<a href='../README.md'>readme</a>\n")
        (self.output / "specimens" / "outline.svg").write_text("<svg/>\n")
        (self.output / "Licenses").mkdir()
        (self.output / "Licenses" / "Required.txt").write_text("required notice\n")

    def package(self, package_format="all"):
        with (
            patch.object(package, "check_build"),
            patch.object(package, "load_recipe", return_value=self.recipe),
        ):
            return package.package_release(
                self.recipe_path, self.output, self.destination, package_format=package_format
            )

    def test_desktop_and_web_packages_keep_notices_and_resolve_web_assets(self):
        self.recipe["documents"].append("web/fonts.css")
        (self.output / "web").mkdir()
        (self.output / "web" / "fonts.css").write_text(
            "@font-face{src:url('../SamplePS-Regular.woff2')}\n"
        )
        for package_format, suffix in (("desktop", ".ttf"), ("web", ".woff2")):
            with self.subTest(package_format=package_format):
                result = self.package(package_format)
                archive = Path(result["archive"])
                self.assertEqual(archive.name, f"DeparturePixelZh-1.2.3-{package_format}.zip")
                with TemporaryDirectory() as temporary, ZipFile(archive) as zip_file:
                    zip_file.extractall(temporary)
                    root = Path(temporary) / f"DeparturePixelZh-1.2.3-{package_format}"
                    self.assertEqual({p.suffix for p in root.glob("SamplePS-Regular.*")}, {suffix})
                    for name in (
                        "OFL.txt",
                        "NOTICE.md",
                        "Licenses/Required.txt",
                        "provenance.json",
                    ):
                        self.assertTrue((root / name).is_file(), name)
                    for line in (root / "SHA256SUMS").read_text().splitlines():
                        checksum, relative = line.split("  ", 1)
                        self.assertEqual(
                            checksum, hashlib.sha256((root / relative).read_bytes()).hexdigest()
                        )
                    if package_format == "web":
                        verify_document_references(root / "web/fonts.css", root)
                        verify_document_references(root / "specimens/index.html", root)
                    else:
                        self.assertFalse((root / "web/fonts.css").exists())
                        self.assertFalse((root / "specimens/index.html").exists())

    def test_archive_is_deterministic_and_excludes_stale_outputs(self):
        (self.output / "Old-Regular.ttf").write_bytes(b"stale font")
        (self.output / "Licenses" / "Old.txt").write_text("stale notice\n")
        first = self.package()
        archive = Path(first["archive"])
        first_bytes = archive.read_bytes()
        second = self.package()
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first_bytes, archive.read_bytes())
        root = "DeparturePixelZh-1.2.3/"
        with ZipFile(archive) as zip_file:
            names = set(zip_file.namelist())
            self.assertEqual(
                names,
                {
                    root + name
                    for name in (
                        "SamplePS-Regular.ttf",
                        "SamplePS-Regular.woff2",
                        "SamplePS_coverage.json",
                        "README.md",
                        "NOTICE.md",
                        "OFL.txt",
                        "specimens/index.html",
                        "specimens/outline.svg",
                        "provenance.json",
                        "Licenses/Required.txt",
                        "SHA256SUMS",
                    )
                },
            )
            sums = zip_file.read(root + "SHA256SUMS").decode().splitlines()
            for line in sums:
                checksum, relative = line.split("  ", 1)
                self.assertEqual(
                    checksum, hashlib.sha256(zip_file.read(root + relative)).hexdigest()
                )

    def test_existing_archives_are_preserved(self):
        self.destination.mkdir()
        archive = self.destination / "DeparturePixelZh-1.2.3.zip"
        archive.write_bytes(b"preserve existing")
        with self.assertRaisesRegex(ValueError, "different contents"):
            self.package()
        self.assertEqual(archive.read_bytes(), b"preserve existing")
        archive.unlink()
        target = self.root / "archive-target"
        target.write_bytes(b"preserve target")
        archive.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.package()
        self.assertTrue(archive.is_symlink())
        self.assertEqual(target.read_bytes(), b"preserve target")
        self.assertEqual(sorted(p.name for p in self.destination.iterdir()), [archive.name])

    def test_dangling_fixed_pending_symlink_is_untouched(self):
        self.destination.mkdir()
        target = self.root / "missing-target"
        pending = self.destination / "DeparturePixelZh-1.2.3.pending"
        pending.symlink_to(target)
        archive = Path(self.package()["archive"])
        self.assertTrue(archive.is_file())
        self.assertTrue(pending.is_symlink())
        self.assertFalse(target.exists())
        self.assertEqual({p.name for p in self.destination.iterdir()}, {pending.name, archive.name})

    def test_concurrent_archive_creation_is_preserved(self):
        import os

        real_link = os.link
        archive = self.destination / "DeparturePixelZh-1.2.3.zip"

        def competing_link(source, destination):
            archive.write_bytes(b"concurrent output")
            return real_link(source, destination)

        with (
            patch.object(package.os, "link", side_effect=competing_link),
            self.assertRaisesRegex(ValueError, "different contents"),
        ):
            self.package()
        self.assertEqual(archive.read_bytes(), b"concurrent output")
        self.assertEqual(list(self.destination.iterdir()), [archive])

    def test_failed_write_cleans_its_temporary_file_and_can_retry(self):
        with (
            patch.object(package.ZipFile, "writestr", side_effect=OSError("write failed")),
            self.assertRaisesRegex(OSError, "write failed"),
        ):
            self.package()
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertTrue(Path(self.package()["archive"]).is_file())

    def test_new_archive_permissions_follow_umask(self):
        import os

        for mask, expected in ((0o022, 0o644), (0o077, 0o600)):
            with self.subTest(mask=mask):
                self.destination = self.root / f"dist-{mask:o}"
                previous = os.umask(mask)
                try:
                    archive = Path(self.package()["archive"])
                finally:
                    os.umask(previous)
                self.assertEqual(archive.stat().st_mode & 0o777, expected)

    def test_failed_release_check_does_not_write_an_archive(self):
        with (
            patch.object(package, "check_build", side_effect=ValueError("not release-ready")),
            patch.object(package, "load_recipe", return_value=self.recipe),
            self.assertRaisesRegex(ValueError, "not release-ready"),
        ):
            package.package_release(self.recipe_path, self.output, self.destination)
        self.assertFalse(self.destination.exists())

    def test_specimen_relative_urls_resolve_in_built_and_packaged_layouts(self):
        specimen = self.output / "specimens" / "index.html"
        (self.output / "SamplePS-Regular.woff2").write_bytes(b"woff2")
        (self.output / "specimens" / "outline.svg").write_text("<svg/>\n")
        specimen.write_text(
            "<style>@font-face{src:url('../SamplePS-Regular.woff2')}</style>"
            "<img src='outline.svg'><a href='../README.md'>readme</a>\n"
        )
        verify_document_references(specimen, self.output)
        result = self.package()
        with TemporaryDirectory() as temporary, ZipFile(result["archive"]) as zip_file:
            zip_file.extractall(temporary)
            root = Path(temporary) / "DeparturePixelZh-1.2.3"
            verify_document_references(root / "specimens" / "index.html", root)


if __name__ == "__main__":
    unittest.main()

"""Test release versioning and packaging without publishing to GitHub."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

SPEC = importlib.util.spec_from_file_location(
    "arcane_release", Path(__file__).resolve().parents[1] / "scripts/release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_first_release_uses_manifest(self):
        self.assertEqual(release.next_version("1.0.6", []), "1.0.6")

    def test_manual_version_bump_is_retained(self):
        self.assertEqual(release.next_version("1.1.0", ["v1.0.6"]), "1.1.0")

    def test_existing_version_gets_patch_bump(self):
        self.assertEqual(release.next_version("1.0.6", ["v1.0.6"]), "1.0.7")

    def test_highest_tag_wins(self):
        self.assertEqual(
            release.next_version("1.0.5", ["v1.0.9", "v1.0.10", "v1.0.6"]),
            "1.0.11",
        )

    def test_unrelated_and_prerelease_tags_are_ignored(self):
        self.assertEqual(
            release.next_version("1.0.6", ["other", "v2.0.0-beta.1"]), "1.0.6"
        )

    def test_invalid_manifest_version_is_rejected(self):
        for version in ("../bad", "1.0", "1.0.6-beta", "01.0.6"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.next_version(version, [])

    def test_archive_contains_only_installation_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            component = root / "custom_components" / "arcane"
            (component / "__pycache__").mkdir(parents=True)
            (component / "translations").mkdir()
            (component / "manifest.json").write_text('{"version": "1.0.6"}')
            (component / "api.py").write_text("# client")
            (component / "__pycache__" / "api.pyc").write_bytes(b"cache")
            (component / "translations" / "de.json").write_text("{}")
            (root / ".env").write_text("SECRET=not-for-release")
            for name in ("README.md", "LICENSE", "NOTICE.md", "hacs.json"):
                (root / name).write_text("{}")
            archive_path = release.build_archive(root, "1.0.6")
            with ZipFile(archive_path) as archive:
                names = archive.namelist()
                self.assertEqual(len(names), 7)
                self.assertIn("custom_components/arcane/api.py", names)
                self.assertIn("custom_components/arcane/translations/de.json", names)
                self.assertFalse(any("__pycache__" in name for name in names))
                self.assertNotIn(".env", names)
                manifest = json.loads(archive.read("custom_components/arcane/manifest.json"))
                self.assertEqual(manifest["version"], "1.0.6")


if __name__ == "__main__":
    unittest.main()

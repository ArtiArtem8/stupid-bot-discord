"""Review exports reconstruct committed and explicitly selected working sources."""

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override
from zipfile import ZipFile

from tools.export_source import _git, export_source


class TestSourceExport(unittest.TestCase):
    @override
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        _git(self.root, "init")
        _git(self.root, "config", "user.name", "Source test")
        _git(self.root, "config", "user.email", "test@example.invalid")
        _git(self.root, "config", "core.autocrlf", "false")
        for name, content in {
            "main.py": b"original\n",
            "uv.lock": b"locked\n",
            "removed.py": b"remove\n",
            "binary.bin": b"\x00\xfforiginal",
            ".env": b"secret",
            "data/state.json": b"private",
            "logs/debug.log": b"private",
            ".vscode/profile.json": b"private",
            "static/asset.json": b"{}",
            ".github/workflows/test.yml": b"name: test\n",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-m", "fixture")

    def test_head_export_ignores_worktree_and_excludes_private_paths(self) -> None:
        (self.root / "main.py").write_text("changed", encoding="utf-8")
        output = self.root / "head.zip"
        export_source(self.root, output)
        with ZipFile(output) as archive:
            self.assertEqual(archive.read("source/main.py"), b"original\n")
            self.assertIn("source/uv.lock", archive.namelist())
            self.assertIn("source/static/asset.json", archive.namelist())
            self.assertIn("source/.github/workflows/test.yml", archive.namelist())
            for excluded in (
                ".env",
                "data/state.json",
                "logs/debug.log",
                ".vscode/profile.json",
            ):
                self.assertNotIn(f"source/{excluded}", archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(
                manifest["commit"],
                _git(self.root, "rev-parse", "HEAD").decode().strip(),
            )
            self.assertEqual(manifest["mode"], "HEAD")
            for item in manifest["files"]:
                self.assertEqual(
                    item["sha256"],
                    hashlib.sha256(archive.read(f"source/{item['path']}")).hexdigest(),
                )
        with self.assertRaises(FileExistsError):
            export_source(self.root, output)

    def test_worktree_patch_reconstructs_staged_unstaged_binary_new_and_deleted(
        self,
    ) -> None:
        (self.root / "main.py").write_bytes(b"staged\n")
        _git(self.root, "add", "main.py")
        (self.root / "main.py").write_bytes(b"unstaged\r\n")
        (self.root / "main.py").chmod(0o755)
        _git(self.root, "update-index", "--chmod=+x", "main.py")
        (self.root / "binary.bin").write_bytes(b"\x00\xffchanged")
        (self.root / "removed.py").unlink()
        (self.root / "new.py").write_bytes(b"included\n")
        (self.root / "staged_new.py").write_bytes(b"explicit only\n")
        _git(self.root, "add", "staged_new.py")
        status = _git(self.root, "status", "--porcelain")
        index_before = _git(self.root, "diff", "--cached", "--binary")
        output = self.root.parent / f"{self.root.name}.zip"
        self.addCleanup(output.unlink, missing_ok=True)
        export_source(self.root, output, worktree=True, include=["new.py"])
        self.assertEqual(_git(self.root, "status", "--porcelain"), status)
        self.assertEqual(_git(self.root, "diff", "--cached", "--binary"), index_before)
        with ZipFile(output) as archive:
            patch = archive.read("worktree.patch")
            self.assertIn(b"GIT binary patch", patch)
            self.assertIn(b"new mode 100755", patch)
            self.assertNotIn(b"secret", patch)
            self.assertNotIn("source/staged_new.py", archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["deleted"], ["removed.py"])
            self.assertEqual(
                next(
                    item["mode"]
                    for item in manifest["files"]
                    if item["path"] == "main.py"
                ),
                "100755",
            )
            self.assertEqual(
                manifest["patch_sha256"], hashlib.sha256(patch).hexdigest()
            )
            with TemporaryDirectory() as directory:
                clone = Path(directory) / "restored"
                _git(
                    self.root,
                    "clone",
                    "-c",
                    "core.autocrlf=false",
                    "--no-hardlinks",
                    str(self.root),
                    str(clone),
                )
                _git(clone, "apply", "--binary", "--whitespace=nowarn", "-", data=patch)
                for item in manifest["files"]:
                    self.assertEqual(
                        (clone / item["path"]).read_bytes(),
                        archive.read(f"source/{item['path']}"),
                    )
                self.assertFalse((clone / "removed.py").exists())

    def test_includes_cannot_escape_or_include_secret_paths(self) -> None:
        for name in ("../outside.py", ".env", "data/state.json", "C:/secret.py"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    export_source(
                        self.root, self.root / "bad.zip", worktree=True, include=[name]
                    )
        with self.assertRaises(ValueError):
            export_source(self.root, self.root / "bad.zip", include=["main.py"])

    def test_external_symlink_is_not_followed(self) -> None:
        link = self.root / "outside.py"
        try:
            link.symlink_to(self.root.parent / "outside.py")
        except OSError:
            self.skipTest("Symlinks require OS permission")
        with self.assertRaises(ValueError):
            export_source(
                self.root, self.root / "bad.zip", worktree=True, include=["outside.py"]
            )

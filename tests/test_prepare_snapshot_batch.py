"""Safety and selection tests for researcher-only snapshot preparation."""

from __future__ import annotations

import io
import hashlib
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_snapshot_batch import SnapshotError, prepare_batch


class PrepareSnapshotBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temp.name)
        self.dataset = self.root / "records.jsonl"
        self.archives = self.root / "archives"
        self.archives.mkdir()
        self.output = self.root / "prepared"
        records = [
            {"id": f"item{i}", "vuln_data": {"file_path": "src/target.py", "code_before": "old = 1\n", "code_after": "new = 2\n"}}
            for i in range(1, 4)
        ]
        self.dataset.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _archive(self, identifier: str, side: str, *, unsafe: bool = False, symlink: bool = False, duplicate_symlink: bool = False) -> None:
        archive_path = self.archives / f"{identifier}_{side}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            if unsafe:
                payload = b"escape"
                info = tarfile.TarInfo("root/../../outside.txt")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
                return
            data = b"old = 1\n" if side == "before" else b"new=2\n"
            info = tarfile.TarInfo("repo-main/src/target.py")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            if symlink:
                link = tarfile.TarInfo("repo-main/src/escape.py")
                link.type = tarfile.SYMTYPE
                link.linkname = "../../outside.txt"
                archive.addfile(link)
                if duplicate_symlink:
                    archive.addfile(link)

    def test_range_selection_and_whitespace_match_report(self) -> None:
        self._archive("item2", "before")
        self._archive("item2", "after")
        report_path = prepare_batch(self.dataset, self.archives, self.output, start=2, end=2)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual([record["id"] for record in report["records"]], ["item2"])
        self.assertEqual(report["records"][0]["snapshots"]["after"]["target_code_whitespace_match"], True)
        self.assertEqual(report["dataset_sha256"], hashlib.sha256(self.dataset.read_bytes()).hexdigest())
        self.assertTrue(report["researcher_only"])
        self.assertTrue((self.output / "2_item2" / "before" / "src/target.py").is_file())

    def test_skips_symlinks_without_materializing_them(self) -> None:
        self._archive("item1", "before", symlink=True)
        self._archive("item1", "after")
        prepare_batch(self.dataset, self.archives, self.output, start=1, end=1)
        tree = self.output / "1_item1" / "before"
        self.assertFalse((tree / "src/escape.py").exists())
        self.assertFalse((tree / "src/escape.py").is_symlink())
        report = json.loads((self.output / "prepare_snapshot_batch_report.json").read_text())
        self.assertEqual(report["records"][0]["snapshots"]["before"]["skipped_symlinks"], ["src/escape.py"])

    def test_rejects_traversal_before_extracting(self) -> None:
        self._archive("item1", "before", unsafe=True)
        self._archive("item1", "after")
        with self.assertRaises(SnapshotError):
            prepare_batch(self.dataset, self.archives, self.output, start=1, end=1)
        self.assertFalse((self.root / "outside.txt").exists())

    def test_rejects_duplicate_symlink_paths(self) -> None:
        self._archive("item1", "before", symlink=True, duplicate_symlink=True)
        self._archive("item1", "after")
        with self.assertRaises(SnapshotError):
            prepare_batch(self.dataset, self.archives, self.output, start=1, end=1)

    def test_rejects_symlinked_output_ancestor_before_resolving(self) -> None:
        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        link_parent = self.root / "linked-parent"
        try:
            link_parent.symlink_to(real_parent, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        with self.assertRaises(SnapshotError):
            prepare_batch(self.dataset, self.archives, link_parent / "prepared", start=1, end=1)
        self.assertFalse((real_parent / "prepared").exists())

    def test_refuses_existing_output(self) -> None:
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("preserve", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            prepare_batch(self.dataset, self.archives, self.output, start=1, end=1)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_missing_archive_is_reported_and_other_records_continue(self) -> None:
        self._archive("item2", "before")
        self._archive("item2", "after")
        report_path = prepare_batch(self.dataset, self.archives, self.output, start=1, end=2)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["records"][0]["status"], "missing_archive")
        self.assertEqual(report["records"][1]["status"], "prepared")


if __name__ == "__main__":
    unittest.main()

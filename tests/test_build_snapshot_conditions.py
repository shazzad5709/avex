"""Filesystem safety and deterministic-copy tests for snapshot conditions."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_snapshot_conditions import ConditionBuildError, build_conditions


class BuildSnapshotConditionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.source = self.repo / "snapshot"
        (self.source / "src").mkdir(parents=True)
        (self.source / "src/target.py").write_bytes(b"target bytes\n")
        (self.source / "src/helper.py").write_bytes(b"helper bytes\n")
        (self.source / "README.md").write_bytes(b"readme\n")
        self.plan_path = self.root / "plan.json"
        self.destination = self.root / "conditions"
        self.report = self.root / "report.json"
        self._write_plan()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_plan(self, record: dict | None = None) -> None:
        value = record or {
            "ordinal": 1,
            "id": "RP-001",
            "source": "snapshot",
            "target_file": "src/target.py",
            "exclude_paths": ["README.md"],
            "exclusion_reasons": {"README.md": "test exclusion"},
        }
        self.plan_path.write_text(json.dumps({"records": [value]}), encoding="utf-8")

    def test_builds_pair_with_only_target_difference_and_verified_hashes(self) -> None:
        before = {path.relative_to(self.source).as_posix(): path.read_bytes() for path in self.source.rglob("*") if path.is_file()}
        report_path = build_conditions(self.plan_path, self.destination, self.report, self.repo)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        root = self.destination / "001_RP-001"
        e12, e3 = root / "E1_E2", root / "E3"
        self.assertTrue((e12 / "src/target.py").is_file())
        self.assertFalse((e3 / "src/target.py").exists())
        self.assertTrue((e12 / "src/helper.py").is_file())
        self.assertTrue((e3 / "src/helper.py").is_file())
        self.assertFalse((e12 / "README.md").exists())
        rec = report["records"][0]
        self.assertEqual(rec["pair_difference"]["E3_additional_omission"], "src/target.py")
        for cond in ("E1_E2", "E3"):
            for item in rec["conditions"][cond]["files"]:
                copied = self.destination / rec["conditions"][cond]["path"] / item["path"]
                self.assertEqual(hashlib.sha256(copied.read_bytes()).hexdigest(), item["sha256"])
        after = {path.relative_to(self.source).as_posix(): path.read_bytes() for path in self.source.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(rec["source_inventory_digest_sha256"], hashlib.sha256(
            json.dumps(rec["source_inventory"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest())

    def test_rejects_traversal_before_any_output_is_created(self) -> None:
        self._write_plan({"ordinal": 1, "id": "RP-001", "source": "../outside", "target_file": "src/target.py", "exclude_paths": []})
        with self.assertRaises(ConditionBuildError):
            build_conditions(self.plan_path, self.destination, self.report, self.repo)
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.report.exists())

    def test_rejects_source_symlink_before_writing(self) -> None:
        link = self.source / "src/linked.py"
        try:
            link.symlink_to(self.source / "src/helper.py")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        with self.assertRaises(ConditionBuildError):
            build_conditions(self.plan_path, self.destination, self.report, self.repo)
        self.assertFalse(self.destination.exists())

    def test_refuses_existing_destination_or_report(self) -> None:
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_text("preserve", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            build_conditions(self.plan_path, self.destination, self.report, self.repo)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
        marker.unlink()
        self.destination.rmdir()
        self.report.write_text("preserve", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            build_conditions(self.plan_path, self.destination, self.report, self.repo)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.report.read_text(encoding="utf-8"), "preserve")

    def test_requires_existing_file_exclusions_and_forbids_target_exclusion(self) -> None:
        for excluded in ("missing.py", "src/target.py"):
            self._write_plan({"ordinal": 1, "id": "RP-001", "source": "snapshot", "target_file": "src/target.py", "exclude_paths": [excluded]})
            with self.subTest(excluded=excluded), self.assertRaises(ConditionBuildError):
                build_conditions(self.plan_path, self.destination, self.report, self.repo)
            self.assertFalse(self.destination.exists())

    def test_report_must_be_outside_destination(self) -> None:
        with self.assertRaises(ConditionBuildError):
            build_conditions(self.plan_path, self.destination, self.destination / "report.json", self.repo)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()

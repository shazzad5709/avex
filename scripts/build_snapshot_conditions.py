#!/usr/bin/env python3
"""Build isolated E1_E2 and E3 workspaces from pre-existing source snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class ConditionBuildError(ValueError):
    """Invalid plan or unsafe source/destination filesystem state."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest_inventory(inventory: list[dict[str, str]]) -> str:
    encoded = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ConditionBuildError(f"{label} must be a non-empty normalized relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ConditionBuildError(f"Unsafe {label}: {value!r}")
    if path.as_posix() != value or ":" in path.parts[0]:
        raise ConditionBuildError(f"{label} must be normalized and relative: {value!r}")
    return path


def _reject_symlink_components(path: Path, label: str) -> None:
    """Check lexical components before resolving, so no symlink is hidden by resolve()."""
    absolute = Path(os.path.abspath(path))
    for component in (absolute, *absolute.parents):
        if component.is_symlink():
            raise ConditionBuildError(f"{label} contains symlink component: {component}")


def _contained(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _source_inventory(source: Path) -> list[dict[str, str]]:
    if source.is_symlink() or not source.is_dir():
        raise ConditionBuildError(f"Source must be an existing physical directory: {source}")
    entries = sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix())
    inventory: list[dict[str, str]] = []
    for path in entries:
        if path.is_symlink():
            raise ConditionBuildError(f"Source contains symlink: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ConditionBuildError(f"Source contains non-regular file: {path}")
        inventory.append({"path": path.relative_to(source).as_posix(), "sha256": _sha256_file(path)})
    return inventory


def _parse_plan(plan_path: Path) -> list[dict[str, Any]]:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConditionBuildError(f"Invalid plan JSON: {exc}") from exc
    if not isinstance(plan, dict) or not isinstance(plan.get("records"), list) or not plan["records"]:
        raise ConditionBuildError("Plan must contain a non-empty records array")
    records: list[dict[str, Any]] = []
    seen_ordinals: set[int] = set()
    seen_ids: set[str] = set()
    for index, raw in enumerate(plan["records"]):
        if not isinstance(raw, dict):
            raise ConditionBuildError(f"records[{index}] must be an object")
        ordinal, identifier = raw.get("ordinal"), raw.get("id")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
            raise ConditionBuildError(f"records[{index}].ordinal must be a positive integer")
        if not isinstance(identifier, (str, int)) or isinstance(identifier, bool) or not str(identifier):
            raise ConditionBuildError(f"records[{index}].id must be a non-empty string or integer")
        identifier = str(identifier)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", identifier):
            raise ConditionBuildError(f"Unsafe record id: {identifier!r}")
        if ordinal in seen_ordinals or identifier in seen_ids:
            raise ConditionBuildError("Record ordinals and ids must each be unique")
        seen_ordinals.add(ordinal)
        seen_ids.add(identifier)
        source_value = raw.get("source")
        if not isinstance(source_value, str) or not source_value:
            raise ConditionBuildError(f"records[{index}].source must be a path relative to --repo-root")
        source_rel = _safe_relative(source_value, f"records[{index}].source")
        target = _safe_relative(raw.get("target_file"), f"records[{index}].target_file")
        exclusions_raw = raw.get("exclude_paths", [])
        if not isinstance(exclusions_raw, list):
            raise ConditionBuildError(f"records[{index}].exclude_paths must be an array")
        exclusions = [_safe_relative(item, f"records[{index}].exclude_paths item").as_posix() for item in exclusions_raw]
        if len(exclusions) != len(set(exclusions)):
            raise ConditionBuildError(f"records[{index}].exclude_paths contains duplicates")
        reasons = raw.get("exclusion_reasons", {})
        if not isinstance(reasons, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in reasons.items()):
            raise ConditionBuildError(f"records[{index}].exclusion_reasons must map paths to text")
        records.append({
            "ordinal": ordinal, "id": identifier, "source_rel": source_rel.as_posix(),
            "target_file": target.as_posix(), "exclude_paths": sorted(exclusions),
            "exclusion_reasons": reasons,
        })
    return sorted(records, key=lambda item: (item["ordinal"], item["id"]))


def build_conditions(plan_path: Path, destination: Path, report_path: Path, repo_root: Path | None = None) -> Path:
    """Validate all inputs, then copy physical regular files into paired conditions."""
    plan_path = plan_path.absolute()
    repo_root = (repo_root or Path.cwd()).absolute()
    _reject_symlink_components(plan_path, "Plan path")
    _reject_symlink_components(repo_root, "Repository root")
    _reject_symlink_components(destination, "Destination path")
    _reject_symlink_components(report_path, "Report path")
    if not plan_path.is_file():
        raise FileNotFoundError(f"Plan file not found: {plan_path}")
    if not repo_root.is_dir():
        raise FileNotFoundError(f"Repository root not found: {repo_root}")
    destination_abs = Path(os.path.abspath(destination))
    report_abs = Path(os.path.abspath(report_path))
    if destination_abs.exists():
        raise FileExistsError(f"Destination already exists: {destination_abs}")
    if report_abs.exists():
        raise FileExistsError(f"Report already exists: {report_abs}")
    if _contained(report_abs, destination_abs):
        raise ConditionBuildError("Report must be outside the destination directory")
    if _contained(destination_abs, report_abs):
        raise ConditionBuildError("Report path cannot be an ancestor of the destination")
    for candidate, label in ((destination_abs.parent, "Destination parent"), (report_abs.parent, "Report parent")):
        existing_parent = candidate
        while not existing_parent.exists() and existing_parent != existing_parent.parent:
            existing_parent = existing_parent.parent
        if existing_parent.exists() and not existing_parent.is_dir():
            raise ConditionBuildError(f"{label} is not a directory: {existing_parent}")

    records = _parse_plan(plan_path)
    prepared: list[dict[str, Any]] = []
    root_resolved = repo_root.resolve(strict=True)
    for record in records:
        source = root_resolved.joinpath(*PurePosixPath(record["source_rel"]).parts)
        _reject_symlink_components(source, "Source path")
        source_resolved = source.resolve(strict=True)
        if not _contained(source_resolved, root_resolved):
            raise ConditionBuildError(f"Source escapes --repo-root: {record['source_rel']}")
        if _contained(destination_abs.resolve(strict=False), source_resolved) or _contained(source_resolved, destination_abs.resolve(strict=False)):
            raise ConditionBuildError("Source and destination paths overlap")
        if _contained(report_abs, source_resolved) or _contained(source_resolved, report_abs):
            raise ConditionBuildError("Report path overlaps a source directory")
        inventory = _source_inventory(source_resolved)
        by_path = {item["path"]: item for item in inventory}
        target = record["target_file"]
        if target not in by_path:
            raise ConditionBuildError(f"Target file does not exist as a regular file: {record['id']}/{target}")
        missing = sorted(set(record["exclude_paths"]) - by_path.keys())
        if missing:
            raise ConditionBuildError(f"Excluded files do not exist for {record['id']}: {missing}")
        if target in record["exclude_paths"]:
            raise ConditionBuildError(f"Common exclusions cannot include target file for {record['id']}")
        prepared.append({**record, "source": source_resolved, "inventory": inventory, "by_path": by_path})

    # All plan, path, link, inventory, and exclusion validation completes before this point.
    destination_abs.mkdir(parents=True, exist_ok=False)
    report_records: list[dict[str, Any]] = []
    for record in prepared:
        pair_name = f"{record['ordinal']:03}_{record['id']}"
        conditions: dict[str, Any] = {}
        common_exclusions = set(record["exclude_paths"])
        for condition in ("E1_E2", "E3"):
            omitted = common_exclusions | ({record["target_file"]} if condition == "E3" else set())
            condition_root = destination_abs / pair_name / condition
            condition_root.mkdir(parents=True)
            file_entries: list[dict[str, str]] = []
            for item in record["inventory"]:
                relative = item["path"]
                if relative in omitted:
                    continue
                source_file = record["source"] / relative
                output_file = condition_root / relative
                output_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_file, output_file)
                copied_hash = _sha256_file(output_file)
                if copied_hash != item["sha256"]:
                    raise ConditionBuildError(f"Copied bytes failed SHA-256 verification: {pair_name}/{condition}/{relative}")
                file_entries.append({"path": relative, "sha256": copied_hash})
            conditions[condition] = {
                "path": condition_root.relative_to(destination_abs).as_posix(),
                "files": file_entries,
                "omitted_paths": [
                    {"path": path, "reason": ("target_file withheld for E3" if path == record["target_file"] else record["exclusion_reasons"].get(path))}
                    for path in sorted(omitted)
                ],
            }
        # Ensure no source file changed during the copy operation.
        if _source_inventory(record["source"]) != record["inventory"]:
            raise ConditionBuildError(f"Source changed while building conditions: {record['source']}")
        report_records.append({
            "ordinal": record["ordinal"], "id": record["id"],
            "source": str(record["source"]), "source_relative_to_repo_root": record["source_rel"],
            "target_file": record["target_file"], "exclude_paths": record["exclude_paths"],
            "source_inventory_digest_sha256": _digest_inventory(record["inventory"]),
            "source_inventory": record["inventory"], "conditions": conditions,
            "pair_difference": {
                "E1_E2_has_target": True, "E3_has_target": False,
                "E3_additional_omission": record["target_file"],
                "common_exclusions": record["exclude_paths"],
            },
        })
    report_abs.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "created_at": _timestamp(), "plan_path": str(plan_path),
        "repo_root": str(root_resolved), "destination": str(destination_abs),
        "records": report_records,
    }
    with report_abs.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return report_abs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path, help="JSON file containing records to build")
    parser.add_argument("--destination", required=True, type=Path, help="New output root for condition workspaces")
    parser.add_argument("--report", required=True, type=Path, help="New JSON report path outside --destination")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Root for relative source paths (default: current directory)")
    args = parser.parse_args()
    try:
        result = build_conditions(args.plan, args.destination, args.report, args.repo_root)
    except (OSError, ConditionBuildError) as exc:
        parser.error(str(exc))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Prepare researcher-only source snapshots from local RepoPairBench archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class SnapshotError(ValueError):
    """Invalid dataset record or unsafe archive contents."""


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _member_path(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise SnapshotError(f"Invalid archive member path: {name!r}")
    raw_parts = name.split("/")
    if name.startswith("/") or any(part in ("", ".", "..") for part in raw_parts):
        raise SnapshotError(f"Unsafe archive member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise SnapshotError(f"Unsafe archive member path: {name!r}")
    # Reject drive-like paths even though PurePosixPath does not consider them absolute.
    if path.parts and ":" in path.parts[0]:
        raise SnapshotError(f"Unsafe archive member path: {name!r}")
    return path


def _validated_members(archive: tarfile.TarFile) -> tuple[list[tuple[tarfile.TarInfo, str]], list[str]]:
    members = archive.getmembers()
    parsed = [(member, _member_path(member.name.rstrip("/"))) for member in members]
    prefixes = {path.parts[0] for _, path in parsed}
    strip_root = len(prefixes) == 1
    result: list[tuple[tarfile.TarInfo, str]] = []
    seen: set[str] = set()
    skipped_symlinks: list[str] = []
    for member, path in parsed:
        relative_parts = path.parts[1:] if strip_root else path.parts
        if not relative_parts:
            if member.isdir():
                continue
            raise SnapshotError(f"Archive root entry is not a directory: {member.name!r}")
        relative = PurePosixPath(*relative_parts).as_posix()
        if relative in seen:
            raise SnapshotError(f"Duplicate archive path: {relative!r}")
        seen.add(relative)
        if member.issym():
            skipped_symlinks.append(relative)
            continue
        if member.islnk():
            raise SnapshotError(f"Hard links are rejected: {member.name!r}")
        if not (member.isfile() or member.isdir()):
            raise SnapshotError(f"Special archive member is rejected: {member.name!r}")
        result.append((member, relative))
    return result, sorted(set(skipped_symlinks))


def extract_archive(archive_path: Path, destination: Path) -> dict[str, Any]:
    """Safely extract regular files/directories, skipping symlinks without following them."""
    with tarfile.open(archive_path, mode="r:gz") as archive:
        entries, skipped = _validated_members(archive)
        root = destination.resolve()
        destination.mkdir(parents=True, exist_ok=False)
        for member, relative in entries:
            target = root.joinpath(*PurePosixPath(relative).parts)
            resolved_parent = target.parent.resolve()
            if resolved_parent != root and root not in resolved_parent.parents:
                raise SnapshotError(f"Extraction escaped destination: {relative!r}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise SnapshotError(f"Cannot read archive member: {member.name!r}")
            with stream, target.open("xb") as output:
                shutil.copyfileobj(stream, output)
            os.chmod(target, stat.S_IMODE(member.mode) & 0o755)
    return {"skipped_symlinks": skipped}


def _record_fields(record: dict[str, Any], line_number: int) -> tuple[str, str, str, str]:
    identifier = record.get("id", record.get("repo_id"))
    if not isinstance(identifier, (str, int)) or not str(identifier):
        raise SnapshotError(f"JSONL line {line_number}: expected non-empty id or repo_id")
    identifier = str(identifier)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", identifier):
        raise SnapshotError(f"JSONL line {line_number}: unsafe identifier {identifier!r}")
    vuln_data = record.get("vuln_data", record)
    if not isinstance(vuln_data, dict):
        raise SnapshotError(f"JSONL line {line_number} ({identifier}): vuln_data must be an object")
    target = vuln_data.get("file_path", vuln_data.get("target_file", ""))
    before = vuln_data.get("code_before")
    after = vuln_data.get("code_after")
    if not isinstance(target, str) or not target:
        raise SnapshotError(f"JSONL line {line_number} ({identifier}): missing target_file/file_path")
    target_path = _member_path(target)
    if target_path.as_posix() != target:
        raise SnapshotError(f"JSONL line {line_number} ({identifier}): target path is not normalized")
    if not isinstance(before, str) or not isinstance(after, str):
        raise SnapshotError(f"JSONL line {line_number} ({identifier}): code_before/code_after must be text")
    if not _normalized(before) or not _normalized(after):
        raise SnapshotError(f"JSONL line {line_number} ({identifier}): code_before/code_after cannot be empty")
    return identifier, target, before, after


def _normalized(text: str) -> str:
    return "".join(text.split())


def _inventory(root: Path) -> list[dict[str, str]]:
    inventory: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_file():
            inventory.append({"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)})
    return inventory


def _reject_symlink_ancestors(path: Path) -> None:
    """Inspect the lexical path before resolve() so symlinked ancestors remain visible."""
    absolute = Path(os.path.abspath(path))
    for component in (absolute, *absolute.parents):
        if component.exists() or component.is_symlink():
            if component.is_symlink():
                raise SnapshotError(f"Output path or ancestor is a symlink: {component}")


def prepare_batch(dataset: Path, archive_dir: Path, output_dir: Path, start: int = 51, end: int = 75) -> Path:
    if start < 1 or end < start:
        raise ValueError("start/end must be one-based positive line numbers with end >= start")
    _reject_symlink_ancestors(output_dir)
    dataset, archive_dir, output_dir = dataset.resolve(), archive_dir.resolve(), output_dir.absolute()
    if output_dir.exists():
        raise FileExistsError(f"Output already exists; refusing to overwrite: {output_dir}")
    if not dataset.is_file():
        raise FileNotFoundError(f"Dataset JSONL not found: {dataset}")
    if not archive_dir.is_dir():
        raise FileNotFoundError(f"Archive directory not found: {archive_dir}")

    selected: list[tuple[int, dict[str, Any]]] = []
    with dataset.open(encoding="utf-8") as stream:
        for number, raw in enumerate(stream, 1):
            if number < start or number > end:
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SnapshotError(f"Invalid JSON at JSONL line {number}: {exc}") from exc
            if not isinstance(value, dict):
                raise SnapshotError(f"JSONL line {number} must contain an object")
            selected.append((number, value))
    if len(selected) != end - start + 1:
        raise SnapshotError(f"Requested lines {start}-{end}, but dataset has only {len(selected) + start - 1} lines")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir()
    report: dict[str, Any] = {
        "created_at": timestamp(), "dataset": str(dataset), "archive_dir": str(archive_dir),
        "dataset_sha256": sha256_file(dataset), "output_dir": str(output_dir),
        "researcher_only": True,
        "warning": "Contains benchmark metadata and source snapshot inventories; keep outside all agent-navigable workspaces.",
        "selection": {"start_line": start, "end_line": end}, "records": [],
    }
    for ordinal, (line_number, record) in enumerate(selected, start):
        identifier, target, before, after = _record_fields(record, line_number)
        record_report: dict[str, Any] = {
            "ordinal": ordinal, "jsonl_line": line_number, "id": identifier,
            "target_file": target, "status": "prepared", "snapshots": {},
        }
        for side, code in (("before", before), ("after", after)):
            archive_path = archive_dir / f"{identifier}_{side}.tar.gz"
            side_report: dict[str, Any] = {"archive": str(archive_path), "destination": None}
            record_report["snapshots"][side] = side_report
            if not archive_path.is_file():
                record_report["status"] = "missing_archive"
                side_report["status"] = "missing"
                continue
            side_report["archive_sha256"] = sha256_file(archive_path)
            side_report["destination"] = str(output_dir / f"{ordinal}_{identifier}" / side)
        if record_report["status"] == "missing_archive":
            report["records"].append(record_report)
            continue
        pair_root = output_dir / f"{ordinal}_{identifier}"
        pair_root.mkdir()
        for side, code in (("before", before), ("after", after)):
            archive_path = archive_dir / f"{identifier}_{side}.tar.gz"
            destination = pair_root / side
            details = extract_archive(archive_path, destination)
            side_report = record_report["snapshots"][side]
            side_report.update(details)
            target_path = destination.joinpath(*PurePosixPath(target).parts)
            target_exists = target_path.is_file() and not target_path.is_symlink()
            target_text_match = False
            if target_exists:
                source_text = target_path.read_text(encoding="utf-8", errors="replace")
                target_text_match = bool(_normalized(code)) and _normalized(code) in _normalized(source_text)
            side_report.update({
                "status": "extracted", "target_exists": target_exists,
                "target_code_whitespace_match": target_text_match,
                "sha256_inventory": _inventory(destination),
            })
        report["records"].append(record_report)
    report_path = output_dir / "prepare_snapshot_batch_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select a JSONL line range and safely extract matching local before/after archives."
    )
    parser.add_argument("--dataset", required=True, type=Path, help="RepoPairBench JSONL source (read-only)")
    parser.add_argument("--archive-dir", required=True, type=Path, help="Directory containing {id}_before/after.tar.gz")
    parser.add_argument("--output-dir", required=True, type=Path, help="New researcher-only output directory (must not exist)")
    parser.add_argument("--start", type=int, default=51, help="One-based first JSONL line (default: 51)")
    parser.add_argument("--end", type=int, default=75, help="One-based last JSONL line (default: 75)")
    args = parser.parse_args()
    try:
        report = prepare_batch(args.dataset, args.archive_dir, args.output_dir, args.start, args.end)
    except (OSError, ValueError, tarfile.TarError) as exc:
        parser.error(str(exc))
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

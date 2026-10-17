#!/usr/bin/env python3
"""Build vulnerable and target-file-absent RepoPairBench snapshots."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/repopairbench/repopairbench_100.jsonl"
OUTPUT = ROOT / "private/repopairbench_snapshots"
METADATA = ROOT / "private/repopairbench_snapshot_metadata"


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_target(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Missing vuln_data.file_path")
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise ValueError(f"Unsafe target path: {value!r}")
    return p.as_posix()


def extract_commit(repo, url, fix_commit, destination):
    run(["git", "init", "-q", str(repo)])
    run(["git", "-C", str(repo), "fetch", "--depth=1", url, fix_commit])

    commit_info = subprocess.check_output(
        ["git", "-C", str(repo), "cat-file", "-p", "FETCH_HEAD"],
        text=True,
    )
    parents = [
        line.split()[1]
        for line in commit_info.splitlines()
        if line.startswith("parent ")
    ]
    if not parents:
        raise RuntimeError(f"Fixing commit has no parent: {fix_commit}")

    vulnerable_commit = parents[0]
    run([
        "git", "-C", str(repo), "fetch", "--depth=1",
        url, vulnerable_commit
    ])

    destination.mkdir(parents=True)
    archive = destination.parent / "repository.tar"
    with archive.open("wb") as stream:
        run([
            "git", "-C", str(repo), "archive",
            "--format=tar", "FETCH_HEAD"
        ], stdout=stream)

    # Validate archive paths before extraction.
    with tarfile.open(archive, "r:") as tar:
        for member in tar.getmembers():
            p = PurePosixPath(member.name)
            if p.is_absolute() or any(part == ".." for part in p.parts):
                raise RuntimeError(f"Unsafe archive path: {member.name}")
        tar.extractall(destination)

    archive.unlink()
    return vulnerable_commit


def build_record(line_number, record, force=False):
    sample_id = record.get("id")
    url = record.get("repo_url")
    fix_commit = record.get("commit_hash")
    target = safe_target(record.get("vuln_data", {}).get("file_path"))

    if not all(isinstance(x, str) and x for x in (sample_id, url, fix_commit)):
        raise ValueError(f"Line {line_number}: missing ID, URL, or fixing commit")

    name = f"line_{line_number}_{sample_id}"
    final_dir = OUTPUT / name
    metadata_file = METADATA / f"{name}.json"

    if final_dir.exists() and not force:
        raise FileExistsError(
            f"{final_dir} already exists; inspect it before using --force"
        )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    METADATA.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=OUTPUT))
    try:
        e12 = staging / "E1E2"
        e3 = staging / "E3"

        with tempfile.TemporaryDirectory(prefix="avex-git-") as temp:
            repo = Path(temp) / "repo"
            vulnerable_commit = extract_commit(
                repo, url, fix_commit, e12
            )

        target_file = e12 / target
        if not target_file.is_file() or target_file.is_symlink():
            raise RuntimeError(
                f"{sample_id}: target is missing or not a regular file "
                f"in the vulnerable snapshot: {target}"
            )

        before = record.get("vuln_data", {}).get("code_before")
        source_text = target_file.read_text(encoding="utf-8")
        if isinstance(before, str) and before not in source_text:
            raise RuntimeError(
                f"{sample_id}: code_before does not match the parent snapshot"
            )

        shutil.copytree(e12, e3, symlinks=True)
        e3_target = e3 / target
        if not e3_target.is_file() or e3_target.is_symlink():
            raise RuntimeError(f"{sample_id}: E3 target unexpectedly absent")
        e3_target.unlink()

        if (e12 / target).read_bytes() != target_file.read_bytes():
            raise RuntimeError(f"{sample_id}: E1E2 target changed unexpectedly")
        if (e3 / target).exists() or (e3 / target).is_symlink():
            raise RuntimeError(f"{sample_id}: E3 target still exists")

        metadata = {
            "dataset_line": line_number,
            "sample_id": sample_id,
            "project_name": record.get("project_name"),
            "repo_url": url,
            "fixing_commit": fix_commit,
            "vulnerable_parent_commit": vulnerable_commit,
            "target_file": target,
            "target_sha256": sha256_file(e12 / target),
            "target_removed_from_E3": True,
            "code_before_matches_parent": (
                before in source_text if isinstance(before, str) else None
            ),
        }

        # Keep researcher metadata outside the agent workspace directories.
        if final_dir.exists():
            shutil.rmtree(final_dir)
        staging.rename(final_dir)
        metadata_file.write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"OK line={line_number} id={sample_id} "
            f"parent={vulnerable_commit[:12]} "
            f"target_removed_from_E3=yes"
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-line", type=int, default=26)
    parser.add_argument("--end-line", type=int, default=50)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.start_line < 1 or args.end_line < args.start_line:
        parser.error("Invalid line range")
    if not DATASET.is_file():
        parser.error(f"Dataset not found: {DATASET}")

    lines = DATASET.read_text(encoding="utf-8").splitlines()
    if args.end_line > len(lines):
        parser.error(f"Dataset has only {len(lines)} lines")

    for line_number in range(args.start_line, args.end_line + 1):
        record = json.loads(lines[line_number - 1])
        build_record(line_number, record, force=args.force)


if __name__ == "__main__":
    main()

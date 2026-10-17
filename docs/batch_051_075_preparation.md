# RepoPairBench records 51–75: snapshot handoff

This batch is explicitly requested by the researcher and is separate from the
existing single-scenario pilot. No model runs are part of this task.

## Final snapshot deliverable

The requested condition folders are under `pilot/conditions/batch_051_075/`:

```text
051_7c167feb/
    E1_E2/   # pre-fix repository, with common leakage exclusions
    E3/      # identical included files except the target file is omitted
...
075_b2079dad/
    E1_E2/
    E3/
```

There are 25 pairs, or 50 condition folders. These are different from the 50
before/after reference trees retained privately. No patched tree is used as a
condition source. Included file bytes are copied without editing; the E1/E2
target file is unchanged. Common exclusions remove 37 files across the batch
that matched patch-artifact or vulnerability-reference filters. Each excluded
path and reason is recorded, and both variants receive identical exclusions.

Ordinary upstream machine-learning uses of “ground truth” and reviewed natural
language uses of “vulnerable” are retained, with 223 occurrence-level exceptions
recorded privately. These are not AVEX reference answers. A marker scan is not
proof of exhaustive leakage freedom or scientific condition validity.

The original source extraction omitted symlinks (592 across before/after trees).
The condition builder uses those physical source trees; no links are introduced.
The snapshots are for source inspection and are not claimed to be runnable builds.

Researcher manifests and verification results remain outside the condition
folders under `private/batch_051_075/`. Do not expose that directory or this
repository root to an experimental agent. Each run must be confined to its
individual condition folder using the separate execution controls.

The existing `prompts/common_prompt.txt` is unchanged. E1/E2 share repository
contents, not an initial evidence packet. The target function remains part of
the intended initial E2/E3 evidence even when its file is absent from E3.
No runs were launched and no new per-scenario E1 sufficiency claims are made.

## Source and selection

- Dataset repository: https://github.com/huhusmang/DREA
- Pinned revision: `f858c49a40310e4cd5f5db6129462e15e04a8de4`
- Dataset: `data/repopairbench_100.jsonl`
- Selection: one-based records 51 through 75 inclusive, preserving file order.
- Scope: 25 records across 22 upstream repositories.

The dataset instructions identify `commit_hash` as the fixing commit. The upstream
reconstruction script uses its first parent for the pre-fix tree. This batch uses
the same revision rule and downloads GitHub source archives for both revisions.
It does not execute downloaded code or install project dependencies.

## Researcher-only handoff files

All downloaded data lives under `private/batch_051_075/`, excluded from Git:

- `upstream/`: pinned dataset, instructions, upstream reconstruction scripts and
  GitHub commit metadata.
- `selected_records.jsonl`: exactly the 25 selected dataset records.
- `selection.json`: original record positions, pinned revision and dataset hash.
- `record_inventory.csv`: project, target path and resolved source revisions.
- `provenance.json`: exact archive URLs and revisions, including parent counts.
- `archives/`: retained compressed source archives.
- `snapshots/`: extracted before/after source trees and SHA-256 inventory report.
- `evidence_rules_required.json`: incomplete template identifying missing inputs.

The raw source pairs are **not approved E1/E2/E3 experimental workspaces**. They
contain unfiltered upstream material and sit beside researcher-only answers.
Never launch an experimental agent here or from the project root.

## Clarified removal rule and remaining decisions

The linked `DATASET.md`, reconstruction scripts and selected JSONL records do not
specify E3 removal rules. The JSONL's `vuln_data` contains only `file_path`,
`code_before`, and `code_after`. The user subsequently clarified the group's rule:
remove the entire file containing the vulnerable target. This is recorded for
each scenario in `private/batch_051_075/target_file_omission_rules.json`.

Following the existing prompt pattern, the target function remains in initial
evidence. Target-file omission differs from the existing pilot's supporting
evidence omission. It does not automatically establish evidence insufficiency.
A before/after patch is not an evidence-removal manifest, and the patched tree
is not E3.

For scientific use of each scenario, remaining review includes:

1. Review of the recorded common file exclusions and benign marker exceptions.
2. The E1 initial evidence and the shared E2/E3 initial evidence.
3. Verification of the delivered evidence packets under the target-file omission rule.
4. Researcher validation of E1 sufficiency, E2 retrievability and E3 unavailability,
   including whether alternative evidence survives and whether the target alone
   already answers the question.

These scientific judgments are **REQUIRES RESEARCHER VALIDATION**. The empty
template deliberately does not invent them. Existing pilot rules apply to its
one iRedAdmin scenario and cannot be reused blindly for these 25 records.

## Reproducing extraction

From the project root, with the downloaded archives present:

```sh
python3 scripts/prepare_snapshot_batch.py \
  --dataset private/batch_051_075/upstream/repopairbench_100.jsonl \
  --archive-dir private/batch_051_075/archives \
  --output-dir private/batch_051_075/snapshots \
  --start 51 --end 75
```

The output directory must not already exist. Use a new directory for another
extraction; the tool will not overwrite an earlier result. Archive traversal,
duplicate member paths, hard links and special files are rejected. Symlinks are
not materialized and are recorded as omissions. Original compressed archives
retain their original contents. No repository is claimed runnable after link
omission.

Target checks report a whitespace-normalized substring match against the dataset
function. This is a mechanical consistency check, not proof of semantic identity,
vulnerability validity or evidence sufficiency. Review any mismatch before using
that scenario.

## Verified acquisition outcome

All 25 records were acquired as 50 before/after source trees. All compressed
archives passed gzip integrity checks, and all 50 target functions matched the
dataset after whitespace normalization. Inventories cover 70,598 regular files.
Extraction omitted and recorded 592 symlink entries across both revisions; the
original archives remain intact. Four timed-out download attempts are retained
separately, and their replacement downloads passed integrity checks.

The extraction-stage unit suite passed 41 tests. With the condition builder's six
additional safety tests, the full suite passed 47 tests. No experiments were launched.

## Reproducing condition construction

```sh
python3 scripts/build_snapshot_conditions.py \
  --plan private/batch_051_075/condition_build_plan.json \
  --destination pilot/conditions/batch_051_075 \
  --report private/batch_051_075/condition_build_report.json
```

Both destination and report must be new paths; existing outputs are not overwritten.
The plan, acquisition inputs, exceptions and reports are local researcher artifacts
and are intentionally excluded from Git. Copy the condition folders separately
from the researcher material when handing off the dataset.

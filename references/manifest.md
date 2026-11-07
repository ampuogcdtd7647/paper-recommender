# Project Manifest

`project-manifest.json` is private scan state. It is separate from the reusable
skill source and from the recommendation profile.

## Responsibilities

- `scan.json` says which roots and file classes may be inspected.
- `project-manifest.json` records the observed project roots and evidence-file fingerprints.
- `profile.json` contains the curated project and interest units used for queries and ranking.

The manifest stores paths, file sizes, modification times, SHA-256 hashes, project
IDs, and scan summaries. It does not store the source text of project files.

## Refresh

When a manifest is present, `profile_manager.py refresh` compares the current
evidence fingerprint with the prior one. Unchanged projects reuse their existing
profile entry. New or changed projects are re-extracted. Projects no longer
discovered remain in the manifest as `stale` and are retained in the profile for
one refresh so the change is visible and auditable.

```bash
python3 scripts/profile_manager.py refresh \
  --profile ~/.codex/paper-recommender/profile.json \
  --manifest ~/.codex/paper-recommender/project-manifest.json \
  --config ~/.codex/paper-recommender/scan.json \
  --write \
  --audit ~/.codex/paper-recommender/profile-audit.jsonl
```

The command emits a JSON summary with `new_projects`, `changed_projects`,
`unchanged_projects`, `stale_projects`, `profile_updated`, and
`recommendation_needed`. A scheduler can use `recommendation_needed` to avoid
starting a paper-search run when the scan found no project change.

## Portability

The manifest is private machine state. Keep it outside the skill repository and
do not commit it when it contains local absolute paths. A future portable profile
export may replace absolute paths with configured root aliases; the scanner's
stable project IDs remain derived from the normalized project root.

---
name: paper-recommender
description: Recommend papers or technical readings for a personalized research digest. Use when the user asks to recommend papers, articles, reports, or reading lists, especially with project context, feedback, scheduled publication, or search-based candidate ranking.
---

# Paper Recommender

Turn scattered search results into a transparent, reproducible recommendation list. The skill is optimized for Codex workflows where Codex performs web/API searches and a local script ranks the verified candidates.

## Workflow

1. **Refresh authorized project context when configured.** If a private scan config exists, run `profile_manager.py refresh` with the private profile and manifest before building queries. Unchanged projects may be reused from the manifest; new, changed, and stale projects must be reported. Never write project-specific state into this skill's source files.
2. **Prepare the profile.** Use an existing profile JSON when available. Otherwise create a private file outside this skill from the generic default—it works with no personalization:
   ```bash
   python3 scripts/profile_manager.py init --output ~/.codex/paper-recommender/profile.json
   ```
   Do not place personal project information in the skill source directory.
3. **Generate search queries.** Run:
   ```bash
   python3 scripts/recommend.py build-queries --profile PROFILE.json
   ```
   Search the highest-priority queries first. Expand, combine, or drop queries based on result volume; do not treat generated queries as mandatory.
4. **Collect candidates.** Use available search tools or paper APIs. Save a JSON array or JSON Lines file matching `references/input-output.md`. Include at least title, URL, source type, published date when known, and abstract/snippet. Preserve `project_ids` or the originating query when a candidate was found for a specific project. Never invent metadata or URLs.
5. **Rank candidates.** Run:
   ```bash
   python3 scripts/recommend.py rank --profile PROFILE.json --candidates CANDIDATES.json --history HISTORY.json --top 5
   ```
   Use `--explain` for score components. The result is decision support, not a substitute for reading the source.
6. **Verify before presenting.** Open or inspect each recommended source enough to confirm its title, venue/source, date, link, and central claim. Mark unverified fields as unverified. Prefer primary pages over aggregator pages.
7. **Present a useful digest.** Include title, authors/organization, date, direct link, why it matters, the strongest project connection, 3–5 core points, one practical exercise, and limitations. Explain ranking briefly when the user asks.
8. **Record feedback without expanding the preference model.** Keep the existing history states (`selected`, `liked`, `disliked`, `rejected`, `read`) and preserve associated project IDs when available. This version keeps the existing backward-compatible treatment of `selected` in `learn-feedback`; do not add new inferred or project-scoped preference rules until that policy is reviewed.

## Conversational configuration

Users should be able to configure this skill entirely in chat; they should not need to edit JSON manually.

When the user asks to set up, configure, personalize, or change Paper Recommender:

1. Inspect existing private files if present and report their current state. If the user asks about local folder access, use `profile_manager.py list-roots`.
2. Ask at most one compact round of questions covering:
   - long-term interests;
   - active projects or goals, if the user wants project-aware recommendations;
   - exact folder roots that may be scanned, if the user wants project discovery.
3. Offer a no-personalization default: use the generic profile and do not scan local folders.
4. Convert the confirmed answers into a temporary JSON file shaped like `assets/conversational-answers.example.json`. Keep this file outside the skill source.
5. Preview without writing:
   ```bash
   python3 scripts/profile_manager.py configure      --profile ~/.codex/paper-recommender/profile.json      --config ~/.codex/paper-recommender/scan.json      --answers /absolute/path/to/answers.json
   ```
6. Show the user the resulting interests, projects, authorized roots, and important defaults. Obtain explicit confirmation before any write.
7. After confirmation, rerun with `--write` and, when appropriate, `--audit`.
8. Report what changed and how to change or revoke folder access later.

Never treat a mentioned folder as authorization to scan it. Authorization must be an explicit user decision, ideally with an exact root path.

## Progressive personalization

Personalization is optional and updates a private profile, never this skill's source or an automation prompt.

When the user asks which folders may be scanned, answer from the explicit scan config—do not infer or expand the allowlist:

```bash
python3 scripts/profile_manager.py list-roots \
  --config ~/.codex/paper-recommender/scan.json
```

To inspect authorized project folders:

```bash
python3 scripts/profile_manager.py refresh   --profile ~/.codex/paper-recommender/profile.json   --config ~/.codex/paper-recommender/scan.json
```

Preview first, then add `--write` and an optional `--audit` path. Only roots listed in the scan config or command line may be inspected. Treat deterministic discovery results as candidates with evidence paths; Codex may curate their names, goals, summaries, and terms after reading the evidence, but must not invent facts. Read `references/profile-maintenance.md` before running or explaining this mode.

## Requirements

- Keep reusable skill code free of user-specific paths, private project names, API keys, and browsing history.
- Treat a preprint, peer-reviewed paper, official report, engineering blog, and secondary summary as different evidence classes.
- Apply source-quality and recency weights rather than choosing only the newest result.
- Avoid consecutive recommendations with the same topic unless the user explicitly requests a focused series.
- Do not change an existing automation merely because this skill is used; update an automation only when the user asks for that integration.
- If no high-quality match exists, say so and recommend a search strategy instead of forcing a weak candidate.

For schemas, scoring details, and examples, read `references/input-output.md`, `references/algorithm.md`, and `references/profile-maintenance.md` only when needed.
For manifest fields and incremental project refresh behavior, read `references/manifest.md`.

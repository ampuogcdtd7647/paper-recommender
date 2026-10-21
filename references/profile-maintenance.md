# Progressive Profile Maintenance

The skill separates four layers:

1. **Reusable skill source**: `SKILL.md`, scripts, examples, and documentation. It is versioned and should not contain user-specific state.
2. **Private profile**: user interests and project context. Start from the generic default and evolve it gradually.
3. **Private scan config**: an explicit allowlist of roots that may be inspected.
4. **Private history and audit log**: selected/liked/rejected papers and a record of profile changes.

This design is inspired by useful properties of agent memory systems such as Hermes: isolated profile state, bounded curated memory, and durable state across sessions. It intentionally adds candidate provenance and explicit file evidence so profile changes can be audited rather than silently treated as authoritative.

## Zero-personalization start

A new user can begin with:

```bash
python3 scripts/profile_manager.py init --output ~/.codex/paper-recommender/profile.json
python3 scripts/recommend.py build-queries --profile ~/.codex/paper-recommender/profile.json
```

The default profile is generic but usable. No project scan is required.

## Conversational configuration

Manual JSON editing is an implementation detail, not the intended user interface. A user should be able to say “configure paper recommendations” in chat and answer a short set of questions.

Codex should:

1. Read existing private state when present.
2. Ask about interests, active projects, and—only if local discovery is wanted—exact folder roots.
3. Offer the generic/no-scan default.
4. Write a temporary answers file outside the skill.
5. Preview `profile_manager.py configure`.
6. Obtain explicit confirmation.
7. Write with `--write` and optionally `--audit`.
8. Report the new state and how to revoke folder access.

The answers schema supports adding `roots`, removing roots with `remove_roots`, and replacing the complete allowlist with `replace_roots: true` plus `roots`. Mentioned paths are not authorization; the user must explicitly grant them.

## Allowed-root transparency

A user can ask in the chat which folders the recommender may scan. Codex should run:

```bash
python3 scripts/profile_manager.py list-roots \
  --config ~/.codex/paper-recommender/scan.json
```

The command reports only the explicit allowlist, whether each path exists, scan depth, and lookback window. Missing config means no local folders are authorized. It never treats the current working directory, home directory, or a mentioned path as implicit permission.

## Optional project discovery

Create a private scan config:

```bash
python3 scripts/profile_manager.py init-scan \
  --output ~/.codex/paper-recommender/scan.json \
  --root ~/Documents \
  --root ~/Desktop
```

Only roots listed in the config (or passed explicitly for that run) are inspected. Preview first:

```bash
python3 scripts/profile_manager.py refresh \
  --profile ~/.codex/paper-recommender/profile.json \
  --config ~/.codex/paper-recommender/scan.json
```

Write the updated private profile:

```bash
python3 scripts/profile_manager.py refresh \
  --profile ~/.codex/paper-recommender/profile.json \
  --config ~/.codex/paper-recommender/scan.json \
  --write \
  --audit ~/.codex/paper-recommender/profile-audit.jsonl
```

The scanner is conservative:

- It skips common dependency, cache, build, VCS, and temporary directories.
- It skips filenames or paths that look secret or credential-related.
- It reads only configured text extensions and files below the size cap.
- It stores project roots, selected evidence paths, modification times, and extracted terms—not raw file bodies.
- It preserves projects marked `manual` or `curated`.
- It marks stale discovered projects inactive instead of deleting them immediately.
- It keeps the profile under `maintenance.profile_character_budget`.

A deterministic scanner is only a candidate generator. Codex may inspect the reported evidence and improve names, goals, summaries, and terms, but must not invent facts or bypass the allowlist.

## Feedback learning

When `recommend.py rank --record-selection` is used, selected papers enter history with topics and keywords. To fold feedback into the profile without manual editing:

```bash
python3 scripts/profile_manager.py learn-feedback \
  --profile ~/.codex/paper-recommender/profile.json \
  --history ~/.codex/paper-recommender/history.json \
  --write \
  --audit ~/.codex/paper-recommender/profile-audit.jsonl
```

Positive feedback updates `liked_topics`; explicit negative feedback updates `disliked_topics`. Existing manual preferences are retained. This is intentionally shallow: it avoids turning a few clicks into a opaque identity model.

## Recommended automation split

- **Daily**: generate queries, search, rank, verify, present, record selection.
- **Weekly**: refresh the profile from authorized project roots and learn from history.
- **Occasionally**: ask Codex to curate the profile after reviewing the audit log.

Never update the reusable skill source or an automation prompt as part of normal personalization.

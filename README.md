# Paper Recommender Skill for Codex

**Read this in: [简体中文](README.zh-CN.md)**

A reusable, open-source Codex skill for personalized paper and technical-reading recommendations. Codex generates search queries from a private profile, searches the web or paper APIs, saves candidate metadata, and uses a dependency-free Python script to score relevance, project fit, source quality, recency, actionability, novelty, and topic diversity.

## What it does

- Starts with a generic profile, so it works without personalization.
- Generates structured search queries from long-term interests and active projects.
- Ranks papers, reports, engineering posts, datasets, and tutorials.
- Uses transparent scoring rather than an opaque “AI picked this” result.
- Learns from selected or rejected readings.
- Optionally discovers active local projects through an explicit folder allowlist.
- Keeps reusable skill source separate from private user state.
- Produces an auditable record when the private profile changes.

## Recommendation signals

The ranking combines:

- BM25-style lexical relevance
- Project-fit matching
- Source type and venue quality
- Recency with configurable decay
- Practical actionability: code, datasets, benchmarks, system design, deployment details
- Feedback from liked or rejected topics
- Reading-history deduplication
- MMR-style topic diversity

See [`references/algorithm.md`](references/algorithm.md) for the exact scoring model.

## Install

Clone this repository into your Codex skills directory:

```bash
git clone https://github.com/ampuogcdtd7647/paper-recommender.git \
  "$HOME/.codex/skills/paper-recommender"
```

Reload or restart Codex so the new skill is discovered.

## Quick start

### 1. Create a generic private profile

```bash
mkdir -p ~/.codex/paper-recommender

python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  init --output ~/.codex/paper-recommender/profile.json
```

No manual profile editing is required for the first run.

### 2. Generate search queries

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/recommend.py" \
  build-queries --profile ~/.codex/paper-recommender/profile.json
```

Ask Codex to search the highest-priority queries and save verified results to `candidates.json`.

### 3. Rank candidates

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/recommend.py" \
  rank \
  --profile ~/.codex/paper-recommender/profile.json \
  --candidates candidates.json \
  --top 5 \
  --explain
```

The output separates selected items, unselected items, duplicates, and validation warnings.

## Conversational setup in chat

Users can configure the skill entirely by talking to Codex; manual JSON editing is not required.

A typical setup conversation covers:

1. Long-term reading interests.
2. Active projects or goals, if project-aware recommendations are wanted.
3. Exact folders that may be scanned, if local project discovery is wanted.

Codex converts the answers into a temporary private answers file, previews the result with:

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" configure \
  --profile ~/.codex/paper-recommender/profile.json \
  --config ~/.codex/paper-recommender/scan.json \
  --answers /absolute/path/to/answers.json
```

Nothing is written until the user confirms. The confirmed run adds `--write` and may add `--audit`. A user can always choose the generic profile and no local scanning.

The answers file can add roots. It can also remove roots with `remove_roots`, or replace the complete allowlist with `replace_roots: true` plus `roots`. These controls let users grant or revoke folder access entirely through chat.

## Progressive personalization

The profile starts generic and can gradually become personal through explicit, auditable signals.

### Check which folders may be scanned

Users can ask Codex in the chat:

> Which folders are you allowed to scan?

Codex will run:

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  list-roots --config ~/.codex/paper-recommender/scan.json
```

If no config exists, the answer is “none yet.” The skill does not treat the current directory, home directory, or a merely mentioned path as implicit permission.

### Create a scan allowlist

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  init-scan \
  --output ~/.codex/paper-recommender/scan.json \
  --root ~/Documents \
  --root ~/Desktop
```

### Preview project discovery

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  refresh \
  --profile ~/.codex/paper-recommender/profile.json \
  --config ~/.codex/paper-recommender/scan.json
```

Preview first. When you want to update the private profile, add `--write` and optionally an audit log:

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  refresh \
  --profile ~/.codex/paper-recommender/profile.json \
  --config ~/.codex/paper-recommender/scan.json \
  --write \
  --audit ~/.codex/paper-recommender/profile-audit.jsonl
```

The scanner stores project roots, evidence paths, modification times, and extracted terms. It does not copy raw documents into the profile.

### Learn from reading feedback

When ranking includes `--record-selection`, selected papers are written to private history. Fold those signals into the profile:

```bash
python3 "$HOME/.codex/skills/paper-recommender/scripts/profile_manager.py" \
  learn-feedback \
  --profile ~/.codex/paper-recommender/profile.json \
  --history ~/.codex/paper-recommender/history.json \
  --write \
  --audit ~/.codex/paper-recommender/profile-audit.jsonl
```

This is intentionally shallow learning: it updates liked and disliked topics from explicit history records rather than constructing an opaque user model.

## Privacy and safety boundaries

- Local project scanning is opt-in.
- Only roots in the explicit allowlist, or roots passed explicitly for that run, may be inspected.
- The scanner skips common dependency, build, cache, VCS, and temporary directories.
- Sensitive-looking paths such as `.env`, credentials, tokens, passwords, API keys, and private keys are skipped.
- Only configured text extensions are read.
- Private profiles, history, scan configs, and audit logs belong outside the repository.
- The reusable skill source does not contain user-specific paths.
- Normal personalization updates a private profile; it does not edit the skill or an automation prompt.

See [`references/profile-maintenance.md`](references/profile-maintenance.md) for details.

## Design boundary

The Python scripts intentionally make no network requests. Codex, another agent, or your own integration performs candidate discovery using available search tools or paper APIs. This makes the skill portable and compatible with arXiv, OpenReview, Semantic Scholar, PubMed, Google Scholar exports, or ordinary web search.

## Repository structure

```text
SKILL.md                         Skill instructions for Codex
README.md                        English documentation
README.zh-CN.md                  Chinese documentation
scripts/recommend.py             Query generation and ranking
scripts/profile_manager.py       Profile bootstrap and progressive maintenance
references/                      Formats, algorithm, and maintenance design
assets/                          Generic and example configurations
tests/                           Offline integration tests
```

## Development tests

```bash
python3 -m unittest discover -s tests -v
```

## License

Released under the [MIT License](LICENSE).

# Input and Output Formats

All files use UTF-8 JSON. Candidate files may be a JSON array, an object containing `{"candidates": [...]}`, or one JSON object per line (JSON Lines).

## Profile

See `assets/profile.example.json`. Important fields:

- `interests`: long-lived research interests. Each may contain `name`, `terms`, `weight`, and `topic`.
- `projects`: active contexts. Each may contain `id`, `name`, `summary`, `terms`, `weight`, `priority`, and `goal`.
- `preferences.recency_half_life_days`: age at which recency contributes half its score. Use a larger value for foundational surveys and a smaller value for fast-moving areas.
- `preferences.diversity_lambda`: `0.0` means pure relevance; `0.5`–`1.0` usually gives a useful topic mix.
- `preferences.max_per_topic`: optional cap during final selection.

## Candidates

Required fields:

- `title`: string
- `url`: string

Useful fields:

- `abstract` or `snippet`
- `authors`: array of strings
- `published_at`: ISO date or datetime
- `source`: site, publisher, or repository name
- `source_type`: one of `peer_reviewed`, `preprint`, `official_report`, `official_engineering`, `secondary_article`, `course_tutorial`, `dataset`, `repository`, or `unknown`
- `venue`: conference, journal, report series, or blog series
- `keywords`: array of strings
- `topics`: array of short labels
- `citations`: integer
- `code_url`: string
- `dataset_url`: string
- `doi`: string
- `arxiv_id`: string
- `project_ids`: candidate IDs from the profile that this work appears relevant to

Only include metadata actually observed in a source. Use null or omit unknown fields.

## History

History is private and should normally live outside the repository. It contains:

- `seen`: presented items for deduplication; new entries use `feedback: "shown"`.
- `recommendations`: presentation IDs, stable paper IDs, conversation IDs, and
  frozen topics, keywords, project IDs/matches for grounding later replies.
- `feedback_events`: sourced per-message decisions, including follow-ups, explicit
  interest/disinterest, neutral actions and retractions.
- `liked_topics` / `disliked_topics`: optional explicit manual global preferences.

See [conversation-feedback.md](conversation-feedback.md) for the decision schema,
commands, scope, replay rules, and legacy migration behavior. The profile stores
`feedback_preferences` as a derived snapshot; it does not rewrite project facts.

## Rank output

The output is a JSON object with:

- `generated_at`
- `selected`: ranked recommendations
- `not_selected`: remaining valid candidates
- `duplicates_removed`: duplicates found before ranking
- `warnings`: validation problems

Selected items also include `paper_id` and `recommendation_id`. Each ranked item includes the original candidate fields plus `rank`, `score`, `score_breakdown`, `matched_terms`, `project_matches`, `selection_note`, and `recommendation_reason`. With `--explain`, score components and evidence are especially explicit.

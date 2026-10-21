# Ranking Algorithm

The goal is not to predict a single objective “best paper.” It is to make a transparent shortlist that balances personal relevance, project usefulness, evidence quality, freshness, actionability, novelty, and variety.

## Query generation

Queries are derived from profile units:

1. High-priority projects combine their most specific terms with broader interest terms.
2. Interests produce focused queries and survey/benchmark queries.
3. Bridge queries combine two profile units when the user wants cross-domain ideas.
4. Freshness is requested with the current year, but the ranker does not automatically discard older foundational work.

Generated queries have a priority and purpose. Codex may rewrite them for a specific search provider.

## Candidate normalization

The ranker lowercases Unicode text, normalizes common aliases such as `llm` and `large language model`, tokenizes English words, extracts CJK character bigrams, removes noisy stopwords, and normalizes URLs. It detects duplicates by DOI, arXiv ID, canonical URL, and normalized title.

## Score components

The default total score is based on these ranges:

- **Relevance, 0–30:** BM25-style IDF-weighted lexical matching over title, abstract/snippet, venue, source, keywords, and topics. Title and keyword matches receive higher field weights.
- **Project fit, 0–25:** overlap with explicit project terms/goals, plus a bonus when `project_ids` names a profile project.
- **Source quality, 0–15:** source type, peer-reviewed venue, citation evidence, and official authorship. Official engineering writeups can score well for practical purposes, but are not represented as peer review.
- **Actionability, 0–12:** code, datasets, benchmarks, evaluation protocols, system design, deployment details, tutorials, or concrete practices.
- **Recency, 0–10:** exponential decay using `recency_half_life_days`. A classic paper can still win through relevance, quality, and actionability.
- **Feedback, -10 to +8:** liked topics/terms increase the score; disliked topics/terms decrease it.
- **Seen penalty, default -25:** avoids repeating an item already present in history. `--exclude-seen` removes it instead.
- **Diversity adjustment, 0 to `-diversity_lambda * 15`:** during final selection, similarity to an already selected item is penalized in the MMR style.

The exact weights come from the profile's `weights` section. A recommendation explains the important reasons; it does not need to expose every arithmetic component unless requested.

## Selection

After scoring, the script:

1. Removes malformed candidates and duplicates.
2. Sorts by score.
3. Iteratively selects the candidate with the best score minus its maximum similarity to already selected items.
4. Applies optional `max_per_topic`.
5. Stops at `--top`.
6. Writes selected and unselected candidates separately, retaining score evidence.

Similarity is a token-vector cosine over title, abstract, keywords, and topics.

## Limitations

- Lexical matching can miss synonyms not present in the profile; expand profile terms or aliases when this happens.
- The ranker cannot judge scientific correctness. Codex must read the source and state limitations.
- Citation counts are incomplete and lag behind new work.
- Diversity can reduce average relevance; lower `diversity_lambda` for a focused reading list.

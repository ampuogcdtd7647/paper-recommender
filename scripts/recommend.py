#!/usr/bin/env python3
"""Query generation and transparent candidate ranking for Paper Recommender.

This script is intentionally offline and dependency-free. Codex or another
search agent collects candidates; this tool normalizes, scores, deduplicates,
and selects a diverse shortlist.

Supported commands:
  build-queries  Generate structured search queries from a profile.
  rank           Rank a JSON/JSONL candidate file.

Python 3.9+ is required.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple


ALIASES = {
    "large language model": "llm",
    "large language models": "llm",
    "language model": "lm",
    "language models": "lm",
    "retrieval augmented generation": "rag",
    "retrieval-augmented generation": "rag",
    "mixture of experts": "moe",
    "reinforcement learning from human feedback": "rlhf",
    "chrome devtools protocol": "cdp",
    "robotic process automation": "rpa",
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "in", "into", "is", "it", "of", "on", "or", "that", "the", "their",
    "this", "to", "using", "vs", "versus", "what", "when", "with", "without",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(_text(v) for v in value.values())
    return str(value)


def normalize_text(value: Any) -> str:
    s = _text(value).strip().casefold()
    s = unicodedata.normalize("NFKC", s)
    for source, target in ALIASES.items():
        s = re.sub(r"(?<![\w])" + re.escape(source) + r"(?![\w])", target, s)
    return s


def extract_tokens(value: Any, include_stopwords: bool = False) -> List[str]:
    s = normalize_text(value)
    tokens = re.findall(r"[a-z0-9]+", s)
    cjk_runs = re.findall(r"[\u3400-\u9fff]+", s)
    for run in cjk_runs:
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    if not include_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return tokens


def parse_json_or_jsonl(path: Path) -> Any:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        items: List[Any] = []
        for line_no, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_no}: {exc}") from exc
        return items


def load_json(path: Path) -> Any:
    data = parse_json_or_jsonl(path)
    if isinstance(data, dict):
        return data
    raise ValueError(f"Expected a JSON object in {path}")


def candidates_from_data(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("candidates"), list):
        return [item for item in data["candidates"] if isinstance(item, dict)]
    raise ValueError("Candidates must be a JSON array, an object with 'candidates', or JSON Lines")


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(s)
    except ValueError:
        parsed = None
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
        if match:
            try:
                parsed = datetime.fromisoformat(match.group(0))
            except ValueError:
                parsed = None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_url(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url.strip())
        if not p.scheme or not p.netloc:
            return url.strip().casefold().rstrip("/")
        host = p.netloc.casefold()
        path = urllib.parse.unquote(p.path).rstrip("/")
        # Canonicalize common arXiv URL variants.
        match = re.search(r"(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?$", path, re.I)
        if "arxiv.org" in host and match:
            return f"arxiv:{match.group(1).lower()}"
        return urllib.parse.urlunsplit(("https", host, path, "", ""))
    except Exception:
        return url.strip().casefold().rstrip("/")


def normalize_doi(doi: str) -> str:
    value = doi.strip().casefold()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value)
    value = value.strip("/")
    return f"doi:{value}"


def normalize_title(title: str) -> str:
    return re.sub(r"[\W_]+", " ", normalize_text(title)).strip()


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def weighted_counter(tokens: Iterable[str], boost: float = 1.0) -> Counter:
    counter: Counter = Counter()
    for token in tokens:
        counter[token] += boost
    return counter


def profile_units(profile: Mapping[str, Any]) -> List[Dict[str, Any]]:
    units: List[Dict[str, Any]] = []
    for item in profile.get("interests", []):
        if not isinstance(item, dict):
            continue
        phrases = [item.get("name"), *item.get("terms", [])]
        units.append({
            "id": item.get("id") or item.get("name", "interest"),
            "label": item.get("name", "Interest"),
            "kind": "interest",
            "topic": item.get("topic") or item.get("name", "general"),
            "phrases": [str(x) for x in phrases if x],
            "search_terms": [str(x) for x in item.get("terms", [item.get("name")]) if x],
            "weight": float(item.get("weight", 0.7)),
            "goal": item.get("goal", ""),
        })
    for item in profile.get("projects", []):
        if not isinstance(item, dict):
            continue
        phrases = [item.get("name"), item.get("goal"), item.get("summary"), *item.get("terms", [])]
        priority = str(item.get("priority", "normal")).casefold()
        priority_boost = {"high": 1.25, "medium": 1.0, "normal": 1.0, "low": 0.8}.get(priority, 1.0)
        units.append({
            "id": str(item.get("id") or item.get("name", "project")),
            "label": item.get("name", "Project"),
            "kind": "project",
            "topic": item.get("topic") or item.get("name", "project"),
            "phrases": [str(x) for x in phrases if x],
            "search_terms": [str(x) for x in item.get("terms", [item.get("name")]) if x],
            "weight": float(item.get("weight", 1.0)) * priority_boost,
            "goal": item.get("goal", ""),
        })
    return units


def preference(profile: Mapping[str, Any], key: str, default: Any) -> Any:
    prefs = profile.get("preferences", {})
    return prefs.get(key, default) if isinstance(prefs, dict) else default


def weight(profile: Mapping[str, Any], key: str, default: float) -> float:
    weights = profile.get("weights", {})
    try:
        return float(weights.get(key, default)) if isinstance(weights, dict) else float(default)
    except (TypeError, ValueError):
        return float(default)


def build_query_objects(profile: Mapping[str, Any], max_queries: int) -> List[Dict[str, Any]]:
    units = profile_units(profile)
    interests = [u for u in units if u["kind"] == "interest"]
    projects = [u for u in units if u["kind"] == "project"]
    year = datetime.now().year
    generated: List[Dict[str, Any]] = []

    def short_terms(unit: Mapping[str, Any], count: int = 3) -> List[str]:
        terms = [str(x) for x in unit.get("search_terms", unit.get("phrases", [])) if x]
        terms = list(dict.fromkeys(terms))[: max(1, count)]
        return terms

    for project in projects:
        for terms in (short_terms(project, 2), short_terms(project, 3)):
            query = " ".join(terms)
            if query:
                generated.append({
                    "query": f"{query} paper {year}",
                    "purpose": f"Find research relevant to {project['label']}",
                    "priority": 90 + project["weight"] * 8,
                    "source_unit": project["id"],
                })
        generated.append({
            "query": " ".join(short_terms(project, 3)) + " benchmark OR evaluation",
            "purpose": f"Find evaluation methods for {project['label']}",
            "priority": 78 + project["weight"] * 7,
            "source_unit": project["id"],
        })
        for interest in interests[:4]:
            bridge = (short_terms(project, 1) + short_terms(interest, 1))
            generated.append({
                "query": " ".join(bridge) + " research",
                "purpose": f"Bridge {project['label']} with {interest['label']}",
                "priority": 65 + min(project["weight"], interest["weight"]) * 10,
                "source_unit": project["id"],
                "bridge_unit": interest["id"],
            })

    for interest in interests:
        generated.append({
            "query": " ".join(short_terms(interest, 3)) + f" paper {year}",
            "purpose": f"Refresh {interest['label']} reading list",
            "priority": 60 + interest["weight"] * 10,
            "source_unit": interest["id"],
        })
        generated.append({
            "query": " ".join(short_terms(interest, 2)) + " survey OR benchmark",
            "purpose": f"Find foundational or comparative material for {interest['label']}",
            "priority": 50 + interest["weight"] * 8,
            "source_unit": interest["id"],
        })

    generated.sort(key=lambda x: (-float(x["priority"]), x["query"]))
    output: List[Dict[str, Any]] = []
    seen_queries: Set[str] = set()
    for item in generated:
        q = re.sub(r"\s+", " ", item["query"]).strip()
        key = normalize_title(q)
        if not q or key in seen_queries:
            continue
        seen_queries.add(key)
        item["query"] = q
        output.append(item)
        if len(output) >= max_queries:
            break
    return output


def source_quality_fraction(candidate: Mapping[str, Any]) -> Tuple[float, List[str]]:
    source_type = str(candidate.get("source_type", "unknown")).casefold()
    base = {
        "peer_reviewed": 0.88,
        "preprint": 0.68,
        "official_report": 0.78,
        "official_engineering": 0.72,
        "course_tutorial": 0.58,
        "dataset": 0.62,
        "repository": 0.55,
        "secondary_article": 0.34,
        "unknown": 0.38,
    }.get(source_type, 0.38)

    evidence: List[str] = []
    if source_type != "unknown":
        evidence.append(f"source_type={source_type}")

    venue = normalize_text(candidate.get("venue", ""))
    if venue:
        evidence.append("venue provided")
        if source_type != "peer_reviewed" and any(word in venue for word in ("conference", "journal", "transactions", "proceedings")):
            base = max(base, 0.76)
            evidence.append("venue looks archival")

    try:
        citations = int(candidate.get("citations") or 0)
    except (TypeError, ValueError):
        citations = 0
    if citations > 0:
        base += min(0.15, 0.025 * math.log1p(citations) / math.log(10))
        evidence.append(f"citations={citations}")
    return clamp(base, 0.05, 1.0), evidence


def actionability_fraction(candidate: Mapping[str, Any]) -> Tuple[float, List[str]]:
    evidence: List[str] = []
    score = 0.0
    if candidate.get("code_url"):
        score += 0.35
        evidence.append("code_url")
    if candidate.get("dataset_url"):
        score += 0.20
        evidence.append("dataset_url")
    text = normalize_text(" ".join([
        _text(candidate.get("title", "")),
        _text(candidate.get("abstract", candidate.get("snippet", ""))),
        _text(candidate.get("keywords", "")),
        _text(candidate.get("topics", "")),
        _text(candidate.get("source_type", "")),
    ]))
    signals = {
        "benchmark": 0.16, "evaluation": 0.11, "dataset": 0.10,
        "system": 0.10, "implementation": 0.10, "deployment": 0.09,
        "reproduction": 0.08, "tutorial": 0.08, "serving": 0.07, "latency": 0.06,
    }
    for signal, value in signals.items():
        if signal in text:
            score += value
            evidence.append(signal)
    source_type = str(candidate.get("source_type", "")).casefold()
    if source_type in {"dataset", "repository", "course_tutorial", "official_engineering"}:
        score += 0.10
        evidence.append(f"source_type={source_type}")
    return clamp(score), evidence


def recency_fraction(candidate: Mapping[str, Any], half_life_days: float) -> Tuple[float, List[str]]:
    published = parse_datetime(candidate.get("published_at"))
    if published is None:
        return 0.35, ["published_at missing"]
    age_days = max(0.0, (datetime.now(timezone.utc) - published).total_seconds() / 86400.0)
    fraction = math.pow(0.5, age_days / max(1.0, half_life_days))
    return fraction, [f"age_days={age_days:.1f}", f"half_life_days={half_life_days:.1f}"]


def cosine(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(value * large.get(token, 0.0) for token, value in small.items())
    if dot == 0:
        return 0.0
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb)


def unit_phrase_weights(unit: Mapping[str, Any]) -> Tuple[Dict[str, float], List[str]]:
    token_weights: Dict[str, float] = {}
    phrases: List[str] = []
    phrase_list = unit.get("phrases", [])
    # Shorter phrases are more specific; give each phrase equal initial weight.
    for phrase in phrase_list:
        if not phrase:
            continue
        phrases.append(phrase)
        tokens = extract_tokens(phrase)
        if not tokens:
            continue
        per_token = 1.0 / max(1, len(tokens))
        for token in tokens:
            token_weights[token] = token_weights.get(token, 0.0) + per_token
    # Frequent generic profile words should not dominate.
    for token in token_weights:
        token_weights[token] /= math.sqrt(max(1.0, token_weights[token]))
    return token_weights, phrases


def exact_phrase_hits(candidate_text: str, phrases: Sequence[str]) -> List[str]:
    hits = []
    for phrase in phrases:
        p = normalize_text(phrase)
        if len(p) >= 3 and p in candidate_text:
            hits.append(phrase)
    return hits


def make_candidate_vectors(candidate: Mapping[str, Any]) -> Tuple[Dict[str, float], str]:
    fields = [
        (candidate.get("title", ""), 2.8),
        (candidate.get("abstract", candidate.get("snippet", "")), 1.0),
        (candidate.get("keywords", ""), 1.9),
        (candidate.get("topics", ""), 1.5),
        (candidate.get("venue", ""), 0.8),
        (candidate.get("source", ""), 0.5),
        (candidate.get("authors", ""), 0.15),
    ]
    counter: Counter = Counter()
    for value, boost in fields:
        for token in extract_tokens(value):
            counter[token] += boost
    vector = {token: float(value) for token, value in counter.items()}
    searchable = normalize_text(" ".join([
        _text(candidate.get("title", "")),
        _text(candidate.get("abstract", candidate.get("snippet", ""))),
        _text(candidate.get("keywords", "")),
        _text(candidate.get("topics", "")),
    ]))
    return vector, searchable


def remove_duplicates(candidates: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    primary: Dict[str, str] = {}
    title_keys: Dict[str, str] = {}
    kept: List[Dict[str, Any]] = []
    duplicates: List[Dict[str, Any]] = []
    ranked_candidates = sorted(
        enumerate(candidates),
        key=lambda pair: (-source_quality_fraction(pair[1])[0], pair[0]),
    )
    for original_index, candidate in ranked_candidates:
        keys: List[str] = []
        if candidate.get("doi"):
            keys.append(normalize_doi(str(candidate["doi"])))
        if candidate.get("arxiv_id"):
            keys.append("arxiv:" + str(candidate["arxiv_id"]).casefold())
        if candidate.get("url"):
            keys.append(normalize_url(str(candidate["url"])))
        duplicate_of = None
        for key in keys:
            if key in primary:
                duplicate_of = primary[key]
                break
        title_key = normalize_title(candidate.get("title", ""))
        if duplicate_of is None and title_key and len(title_key) > 12 and title_key in title_keys:
            duplicate_of = title_keys[title_key]
        if duplicate_of is not None:
            copied = dict(candidate)
            copied["duplicate_of"] = duplicate_of
            duplicates.append(copied)
            continue
        for key in keys:
            primary[key] = str(candidate.get("url") or candidate.get("title") or key)
        if title_key:
            title_keys[title_key] = str(candidate.get("url") or candidate.get("title") or title_key)
        kept.append(candidate)
    kept.sort(key=lambda c: str(c.get("title", "")))
    return kept, duplicates


def score_candidates(
    profile: Mapping[str, Any],
    candidates: Sequence[Dict[str, Any]],
    history: Optional[Mapping[str, Any]],
    explain: bool,
    exclude_seen: bool,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    units = profile_units(profile)
    vectors = {id(c): make_candidate_vectors(c)[0] for c in candidates}
    searchable = {id(c): make_candidate_vectors(c)[1] for c in candidates}
    docs = list(vectors.values())
    df: Counter = Counter()
    for vector in docs:
        df.update(vector.keys())
    n_docs = max(1, len(docs))
    avgdl = sum(sum(v.values()) for v in docs) / n_docs or 1.0
    k1, b = 1.4, 0.72

    def idf(token: str) -> float:
        return math.log(1.0 + (n_docs - df[token] + 0.5) / (df[token] + 0.5))

    seen_urls: Dict[str, Dict[str, Any]] = {}
    seen_titles: Dict[str, Dict[str, Any]] = {}
    if history:
        for item in history.get("seen", []):
            if not isinstance(item, dict):
                continue
            if item.get("url"):
                seen_urls[normalize_url(str(item["url"]))] = item
            if item.get("title"):
                key = normalize_title(str(item["title"]))
                if key:
                    seen_titles[key] = item

    liked_topics = [str(x) for x in (history or {}).get("liked_topics", [])]
    disliked_topics = [str(x) for x in (history or {}).get("disliked_topics", [])]
    warnings: List[str] = []
    scored: List[Dict[str, Any]] = []
    relevance_weight = weight(profile, "relevance", 30)
    project_weight = weight(profile, "project_fit", 25)
    source_weight = weight(profile, "source_quality", 15)
    actionability_weight = weight(profile, "actionability", 12)
    recency_weight = weight(profile, "recency", 10)
    liked_bonus = weight(profile, "liked_bonus", 8)
    disliked_penalty = weight(profile, "disliked_penalty", 10)
    seen_penalty = weight(profile, "seen_penalty", 25)
    half_life = float(preference(profile, "recency_half_life_days", 270))

    for candidate in candidates:
        if not candidate.get("title"):
            warnings.append("Skipped a candidate without a title")
            continue
        if not candidate.get("url"):
            warnings.append(f"Skipped candidate without URL: {candidate.get('title', '')[:80]}")
            continue
        vector = vectors[id(candidate)]
        candidate_text = searchable[id(candidate)]

        # BM25-style relevance across profile units.
        relevance_components: Dict[str, float] = {}
        matched_terms_by_unit: Dict[str, List[str]] = {}
        total_relevance = 0.0
        max_relevance = 0.0
        for unit in units:
            token_weights, phrases = unit_phrase_weights(unit)
            unit_score = 0.0
            unit_max = 0.0
            matched: Set[str] = set()
            for token, query_weight in token_weights.items():
                tf = vector.get(token, 0.0)
                term_idf = idf(token)
                saturation = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * sum(vector.values()) / avgdl))
                unit_score += query_weight * term_idf * saturation
                unit_max += query_weight * term_idf
                if tf > 0:
                    matched.add(token)
            fraction = clamp(unit_score / unit_max) if unit_max else 0.0
            hits = exact_phrase_hits(candidate_text, phrases)
            if hits:
                fraction = clamp(fraction + 0.08 * min(3, len(hits)))
                matched.update(normalize_text(x) for x in hits)
            contribution = fraction * unit["weight"]
            total_relevance += contribution
            max_relevance += unit["weight"]
            relevance_components[unit["id"]] = round(contribution, 4)
            if matched:
                matched_terms_by_unit[str(unit["id"])] = sorted(matched)[:12]
        relevance_fraction = clamp(total_relevance / max_relevance) if max_relevance else 0.0
        relevance_score = relevance_fraction * relevance_weight

        # Explicit project fit.
        project_matches: List[Dict[str, Any]] = []
        project_fractions: List[float] = []
        named_project_ids = {str(x) for x in candidate.get("project_ids", [])}
        for unit in units:
            if unit["kind"] != "project":
                continue
            token_weights, phrases = unit_phrase_weights(unit)
            overlap = sum(1 for token in token_weights if token in vector)
            coverage = overlap / len(token_weights) if token_weights else 0.0
            phrase_hits = exact_phrase_hits(candidate_text, phrases)
            fraction = clamp(coverage * 0.65 + min(0.35, 0.12 * len(phrase_hits)))
            if unit["id"] in named_project_ids:
                fraction = clamp(fraction + 0.35)
            if fraction > 0:
                project_fractions.append(fraction * unit["weight"])
                project_matches.append({
                    "project_id": unit["id"],
                    "project": unit["label"],
                    "fraction": round(fraction, 4),
                    "matched_terms": sorted({t for t in token_weights if t in vector})[:12],
                    "phrase_hits": phrase_hits[:5],
                })
        project_fraction = clamp(max(project_fractions, default=0.0))
        project_score = project_fraction * project_weight

        source_fraction, source_evidence = source_quality_fraction(candidate)
        source_score = source_fraction * source_weight
        action_fraction, action_evidence = actionability_fraction(candidate)
        actionability_score = action_fraction * actionability_weight
        rec_fraction, recency_evidence = recency_fraction(candidate, half_life)
        recency_score = rec_fraction * recency_weight

        feedback_score = 0.0
        feedback_evidence: List[str] = []
        for topic in liked_topics:
            if topic and normalize_text(topic) in candidate_text:
                feedback_score += liked_bonus / max(1, len(liked_topics))
                feedback_evidence.append(f"liked:{topic}")
        for topic in disliked_topics:
            if topic and normalize_text(topic) in candidate_text:
                feedback_score -= disliked_penalty / max(1, len(disliked_topics))
                feedback_evidence.append(f"disliked:{topic}")
        feedback_score = max(-disliked_penalty, min(liked_bonus, feedback_score))

        seen_item: Optional[Dict[str, Any]] = None
        if candidate.get("url"):
            seen_item = seen_urls.get(normalize_url(str(candidate.get("url"))))
        if seen_item is None and candidate.get("title"):
            seen_item = seen_titles.get(normalize_title(str(candidate.get("title"))))
        if seen_item is not None and exclude_seen:
            continue
        seen_score = -seen_penalty if seen_item is not None else 0.0

        score = relevance_score + project_score + source_score + actionability_score + recency_score + feedback_score + seen_score
        result = dict(candidate)
        result["score"] = round(score, 4)
        result["score_breakdown"] = {
            "relevance": round(relevance_score, 3),
            "project_fit": round(project_score, 3),
            "source_quality": round(source_score, 3),
            "actionability": round(actionability_score, 3),
            "recency": round(recency_score, 3),
            "feedback": round(feedback_score, 3),
            "seen": round(seen_score, 3),
        }
        result["matched_terms"] = matched_terms_by_unit
        result["project_matches"] = project_matches
        if seen_item is not None:
            result["seen"] = True
        if explain:
            result["evidence"] = {
                "relevance_units": relevance_components,
                "source_quality": source_evidence,
                "actionability": action_evidence,
                "recency": recency_evidence,
                "feedback": feedback_evidence,
                "seen": bool(seen_item),
            }
        top_terms = sorted({t for values in matched_terms_by_unit.values() for t in values})[:8]
        reason_bits: List[str] = []
        strong_project_matches = [x for x in project_matches if x["fraction"] >= 0.15]
        if strong_project_matches:
            reason_bits.append("matches " + ", ".join(x["project"] for x in strong_project_matches[:2]))
        if top_terms:
            reason_bits.append("terms: " + ", ".join(top_terms[:5]))
        if source_fraction >= 0.68:
            reason_bits.append(str(candidate.get("source_type", "credible source")))
        if action_evidence:
            reason_bits.append("actionable: " + ", ".join(action_evidence[:3]))
        result["recommendation_reason"] = "; ".join(reason_bits) or "weak lexical match; inspect manually before recommending"
        scored.append(result)

    scored.sort(key=lambda x: (-float(x["score"]), normalize_text(x.get("title", ""))))
    return scored, warnings


def select_diverse(
    scored: Sequence[Dict[str, Any]],
    profile: Mapping[str, Any],
    top: int,
) -> List[Dict[str, Any]]:
    remaining = list(scored)
    selected: List[Dict[str, Any]] = []
    diversity_lambda = float(preference(profile, "diversity_lambda", 0.65))
    diversity_penalty = weight(profile, "diversity_penalty", 15)
    max_per_topic = preference(profile, "max_per_topic", None)
    topic_counts: Counter = Counter()
    vectors = {id(item): make_candidate_vectors(item)[0] for item in remaining}

    while remaining and len(selected) < top:
        eligible_indexes = []
        for index, item in enumerate(remaining):
            topics = [str(x) for x in item.get("topics", [])] or ["general"]
            if not (max_per_topic and any(topic_counts[t] >= int(max_per_topic) for t in topics)):
                eligible_indexes.append(index)
        if not eligible_indexes:
            break
        best_index = eligible_indexes[0]
        best_value = -float("inf")
        for index in eligible_indexes:
            item = remaining[index]
            adjustment = 0.0
            if selected:
                item_vector = vectors[id(item)]
                similarities = [cosine(item_vector, make_candidate_vectors(chosen)[0]) for chosen in selected]
                adjustment = diversity_lambda * diversity_penalty * max(similarities)
            value = float(item["score"]) - adjustment
            if value > best_value:
                best_value = value
                best_index = index
        item = remaining.pop(best_index)
        for topic in ([str(x) for x in item.get("topics", [])] or ["general"]):
            topic_counts[topic] += 1
        copied = dict(item)
        copied["rank"] = len(selected) + 1
        copied["final_score"] = round(best_value, 4)
        copied["selection_note"] = "selected by relevance and diversity"
        selected.append(copied)
    return selected


def record_selection(
    history_path: Path,
    selected: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    history: Dict[str, Any]
    if history_path.exists():
        history = load_json(history_path)
        if not isinstance(history, dict):
            raise ValueError(f"History file must contain a JSON object: {history_path}")
    else:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history = {"version": 1, "seen": [], "liked_topics": [], "disliked_topics": []}
    seen = history.setdefault("seen", [])
    if not isinstance(seen, list):
        raise ValueError("History 'seen' must be a list")
    now = iso_now()
    for item in selected:
        if not item.get("url"):
            continue
        normalized = normalize_url(str(item["url"]))
        already = any(normalize_url(str(x.get("url", ""))) == normalized for x in seen if isinstance(x, dict))
        if not already:
            seen.append({
                "url": item.get("url"),
                "title": item.get("title"),
                "first_seen_at": now,
                "selected_at": now,
                "feedback": "selected",
                "topics": item.get("topics", []),
                "keywords": item.get("keywords", []),
                "source_type": item.get("source_type", "unknown"),
            })
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return history


def write_output(path: Optional[Path], payload: Mapping[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def command_build_queries(args: argparse.Namespace) -> int:
    profile = load_json(Path(args.profile))
    queries = build_query_objects(profile, args.max)
    write_output(args.output, {"generated_at": iso_now(), "queries": queries})
    return 0


def command_rank(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile)
    candidates_path = Path(args.candidates)
    profile = load_json(profile_path)
    raw_candidates = candidates_from_data(parse_json_or_jsonl(candidates_path))
    unique, duplicates = remove_duplicates(raw_candidates)

    history: Optional[Dict[str, Any]] = None
    if args.history:
        history_path = Path(args.history)
        if history_path.exists():
            history = load_json(history_path)
            if not isinstance(history, dict):
                raise ValueError(f"History file must contain a JSON object: {history_path}")

    exclude_seen = args.exclude_seen or bool(preference(profile, "exclude_seen", False))
    scored, warnings = score_candidates(profile, unique, history, args.explain, exclude_seen)
    selected = select_diverse(scored, profile, args.top)
    selected_urls = {normalize_url(str(x.get("url", ""))) for x in selected}
    not_selected = [item for item in scored if normalize_url(str(item.get("url", ""))) not in selected_urls]

    payload = {
        "generated_at": iso_now(),
        "profile_file": str(profile_path.resolve()),
        "candidates_file": str(candidates_path.resolve()),
        "selected": selected,
        "not_selected": not_selected,
        "duplicates_removed": duplicates,
        "warnings": warnings,
    }
    write_output(args.output, payload)

    if args.record_selection:
        if not args.history:
            raise ValueError("--record-selection requires --history")
        record_selection(Path(args.history), selected)
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-queries", help="Generate search queries from a profile")
    build.add_argument("--profile", required=True, help="Profile JSON file")
    build.add_argument("--max", type=int, default=12, help="Maximum queries to generate (default: 12)")
    build.add_argument("--output", type=Path, help="Write JSON output to this file")
    build.set_defaults(handler=command_build_queries)

    rank = subparsers.add_parser("rank", help="Rank collected candidates")
    rank.add_argument("--profile", required=True, help="Profile JSON file")
    rank.add_argument("--candidates", required=True, help="JSON/JSONL candidate file")
    rank.add_argument("--history", help="Private history JSON file")
    rank.add_argument("--top", type=int, default=5, help="Number of recommendations to select")
    rank.add_argument("--output", type=Path, help="Write JSON output to this file")
    rank.add_argument("--explain", action="store_true", help="Include detailed score evidence")
    rank.add_argument("--exclude-seen", action="store_true", help="Remove already-seen items")
    rank.add_argument("--record-selection", action="store_true", help="Append selected items to history (requires --history)")
    rank.set_defaults(handler=command_rank)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

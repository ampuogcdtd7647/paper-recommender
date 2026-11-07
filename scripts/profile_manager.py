#!/usr/bin/env python3
"""Bootstrap and progressively maintain a private Paper Recommender profile.

Commands:
  init          Create a usable generic profile.
  init-scan     Create a private, explicit project-scan allowlist.
  refresh       Discover active local projects and update the private manifest/profile.
  learn-feedback Fold shallow positive/negative reading feedback into the profile.

The scanner is offline, conservative, and deterministic. It does not modify skill
source files or automation prompts. The manifest records evidence fingerprints so
unchanged projects can be reused without re-extracting their context.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = SKILL_DIR / "assets" / "default.profile.json"
DEFAULT_SCAN_CONFIG = SKILL_DIR / "assets" / "scan-config.example.json"

PROJECT_MARKERS = {
    ".git", "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
    "pom.xml", "requirements.txt", "setup.py", "Dockerfile", "Makefile",
}
SOFT_PROJECT_MARKERS = {"README.md", "README.markdown", "readme.md", "docs", "src", "tests"}
PROGRESS_HINTS = (
    "readme", "project_handoff", "handoff", "status", "progress", "todo",
    "roadmap", "plan", "summary", "report", "execution_log", "交接",
    "进展", "状态", "待办", "计划", "总结",
)
NOISE_TERMS = {
    "project", "document", "documentation", "file", "update", "updated",
    "record", "note", "notes", "current", "content", "index", "guide",
    "assistant", "chatgpt", "codex", "user", "task", "date", "time",
    "version", "document", "文档", "项目", "更新", "记录", "当前", "时间",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_args_bool(value: Any) -> bool:
    return str(value).casefold() in {"1", "true", "yes", "on"}


def load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return data


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_fingerprint(root: Path, files: Sequence[Path]) -> Tuple[str, List[Dict[str, Any]]]:
    """Return a stable fingerprint and inspectable metadata for evidence files."""
    records: List[Dict[str, Any]] = []
    digest = hashlib.sha256()
    digest.update(str(root).encode("utf-8"))
    for path in sorted(files, key=lambda item: str(item)):
        try:
            stat = path.stat()
            relative = str(path.relative_to(root))
            content_hash = sha256_file(path)
        except (OSError, PermissionError, ValueError):
            continue
        record = {
            "path": str(path),
            "relative_path": relative,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": content_hash,
        }
        records.append(record)
        digest.update(relative.encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(content_hash.encode("ascii"))
    return digest.hexdigest(), records


def append_audit(path: Path, event: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": iso_now(), **dict(event)}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def safe_resolved_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def is_sensitive_path(path: Path, patterns: Sequence[str]) -> bool:
    lowered = str(path).casefold()
    name = path.name.casefold()
    if name == ".env" or name.startswith(".env."):
        return True
    return any(str(pattern).casefold() in lowered for pattern in patterns)


def dir_depth(root: Path, directory: Path) -> int:
    try:
        return len(directory.relative_to(root).parts)
    except ValueError:
        return 0


def is_project_directory(directory: Path) -> bool:
    try:
        entries = {entry.name for entry in directory.iterdir()}
    except (OSError, PermissionError):
        return False
    strong = bool(entries & PROJECT_MARKERS)
    soft_count = sum(1 for name in SOFT_PROJECT_MARKERS if name in entries)
    # A directory with no build manifest can still be a useful project if it has
    # multiple soft signals. This catches folders containing only docs/handoffs.
    return strong or soft_count >= 2


def evidence_score(path: Path, root_name: str) -> Tuple[int, float]:
    name = path.name.casefold()
    stem = unicodedata.normalize("NFKC", path.stem).casefold()
    hint_bonus = sum(4 for hint in PROGRESS_HINTS if hint in stem)
    readme_bonus = 2 if "readme" in name else 0
    docs_bonus = 1 if "docs" in path.parts else 0
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0
    return hint_bonus + readme_bonus + docs_bonus, modified


def collect_projects(
    roots: Sequence[Path],
    config: Mapping[str, Any],
    max_projects: int,
    lookback_days: int,
    previous_manifest: Optional[Mapping[str, Any]] = None,
    previous_projects: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    exclude_dirs = {str(x) for x in config.get("exclude_dir_names", [])}
    sensitive_parts = [str(x) for x in config.get("exclude_path_parts", [])]
    extensions = {str(x).casefold() for x in config.get("supported_extensions", [".md", ".txt"])}
    progress_patterns = [str(x).casefold() for x in config.get("progress_filename_patterns", [])]
    max_depth = int(config.get("max_depth", 3))
    max_files = int(config.get("max_files_per_project", 24))
    max_file_bytes = int(config.get("max_file_bytes", 200000))
    cutoff = datetime.now().timestamp() - max(1, lookback_days) * 86400
    raw_projects: List[Dict[str, Any]] = []
    stats: Counter = Counter()
    warnings: List[str] = []
    manifest_projects = previous_manifest.get("projects", {}) if isinstance(previous_manifest, Mapping) else {}
    if not isinstance(manifest_projects, Mapping):
        manifest_projects = {}
    previous_by_root = {
        str(item.get("root", "")): item
        for item in (previous_projects or [])
        if isinstance(item, Mapping) and item.get("source") == "scan" and item.get("root")
    }

    for root in roots:
        root = root.resolve()
        if not root.exists():
            warnings.append(f"Root does not exist: {root}")
            continue
        if not root.is_dir():
            warnings.append(f"Root is not a directory: {root}")
            continue
        stats["roots"] += 1
        for directory, dirnames, filenames in os.walk(root):
            current = Path(directory)
            if dir_depth(root, current) > max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = sorted(
                name for name in dirnames
                if name not in exclude_dirs
                and not name.startswith(".")
                and not is_sensitive_path(current / name, sensitive_parts)
            )
            stats["directories_seen"] += 1
            if not is_project_directory(current):
                continue
            stats["project_candidates"] += 1
            eligible_files: List[Path] = []
            # Inspect evidence files below the project root, not only files at
            # its top level. This captures common layouts such as docs/PLAN.md
            # and docs/PROJECT_HANDOFF.md while retaining the configured depth
            # and exclusion boundaries.
            for evidence_directory, evidence_dirnames, evidence_filenames in os.walk(current):
                evidence_current = Path(evidence_directory)
                if dir_depth(current, evidence_current) > max_depth:
                    evidence_dirnames[:] = []
                    continue
                evidence_dirnames[:] = sorted(
                    name for name in evidence_dirnames
                    if name not in exclude_dirs
                    and not name.startswith(".")
                    and not is_sensitive_path(evidence_current / name, sensitive_parts)
                )
                for filename in evidence_filenames:
                    path = evidence_current / filename
                    stats["files_seen"] += 1
                    if path.suffix.casefold() not in extensions:
                        continue
                    if is_sensitive_path(path, sensitive_parts):
                        stats["sensitive_skipped"] += 1
                        continue
                    lowered_path = str(path).casefold()
                    if not any(pattern in path.name.casefold() or pattern in lowered_path for pattern in progress_patterns):
                        continue
                    try:
                        if path.stat().st_size > max_file_bytes:
                            stats["oversize_skipped"] += 1
                            continue
                    except OSError:
                        continue
                    eligible_files.append(path)
            if not eligible_files:
                continue
            eligible_files.sort(key=lambda p: evidence_score(p, current.name), reverse=True)
            chosen_files = eligible_files[:max_files]
            try:
                newest_mtime = max(path.stat().st_mtime for path in chosen_files)
            except OSError:
                continue
            fingerprint, file_records = evidence_fingerprint(current, chosen_files)
            previous_manifest_item = manifest_projects.get(str(current))
            unchanged = bool(
                isinstance(previous_manifest_item, Mapping)
                and previous_manifest_item.get("fingerprint") == fingerprint
                and str(current) in previous_by_root
            )
            raw_projects.append({
                "root": str(current),
                "name": current.name,
                "evidence_files": [str(path) for path in chosen_files],
                "evidence_records": file_records,
                "fingerprint": fingerprint,
                "unchanged": unchanged,
                "newest_mtime": newest_mtime,
                "active_recently": newest_mtime >= cutoff,
            })
            if len(raw_projects) >= max_projects:
                break
        if len(raw_projects) >= max_projects:
            break

    raw_projects.sort(key=lambda item: (not item["active_recently"], -item["newest_mtime"], item["root"]))

    global_term_counts: Counter = Counter()
    preliminary: List[Dict[str, Any]] = []
    for item in raw_projects:
        if item["unchanged"]:
            previous = dict(previous_by_root[item["root"]])
            preliminary.append({
                **item,
                "reused_project": previous,
                "token_counts": Counter(),
                "read_count": 0,
                "text_sample": "",
            })
            stats["projects_reused"] += 1
            continue
        text_parts: List[str] = []
        read_count = 0
        for file_path_string in item["evidence_files"]:
            path = Path(file_path_string)
            try:
                stats["files_read"] += 1
                read_count += 1
                text = path.read_text(encoding="utf-8", errors="ignore")[:max_file_bytes]
            except (OSError, PermissionError):
                stats["files_unreadable"] += 1
                continue
            headings = re.findall(r"^#{1,6}\s+(.{3,160})$", text, flags=re.MULTILINE)
            filename_terms = " ".join(path.with_suffix("").parts[-2:])
            text_parts.extend(headings[:40])
            text_parts.append(filename_terms)
            # Headings and filenames carry more signal than full prose.
            text_parts.extend(headings[:40])
            text_parts.extend([line for line in text.splitlines() if line.strip()][:40])
        from recommend import extract_tokens, normalize_text

        token_counts: Counter = Counter()
        for part in text_parts:
            for token in extract_tokens(part):
                if len(token) < 2 or token in NOISE_TERMS:
                    continue
                token_counts[token] += 2 if len(part) < 180 else 1
        global_term_counts.update(token_counts.keys())
        preliminary.append({
            **item,
            "token_counts": token_counts,
            "read_count": read_count,
            "text_sample": "\n".join(text_parts)[:100000],
        })

    projects: List[Dict[str, Any]] = []
    for item in preliminary:
        if item.get("reused_project"):
            reused = dict(item["reused_project"])
            reused["active"] = bool(item["active_recently"])
            reused.pop("stale", None)
            reused["_scan_unchanged"] = True
            projects.append(reused)
            continue
        specific_terms: List[str] = []
        for token, count in item["token_counts"].most_common():
            if len(token) < 3 and not re.search(r"[\u3400-\u9fff]", token):
                continue
            # Prefer terms that are not present in every discovered project.
            if global_term_counts[token] >= max(3, len(preliminary)):
                continue
            if token not in specific_terms:
                specific_terms.append(token)
            if len(specific_terms) >= int(config.get("max_terms_per_project", 14)):
                break
        first_heading = ""
        for line in item["text_sample"].splitlines():
            value = line.strip().lstrip("#").strip()
            if value and not value.startswith(("!", "[!")):
                first_heading = value[:180]
                break
        evidence = item["evidence_files"][: int(config.get("max_evidence_files_per_project", 6))]
        root_path = Path(item["root"])
        stable_id = "project-" + hashlib.sha1(str(root_path).encode("utf-8")).hexdigest()[:10]
        projects.append({
            "id": stable_id,
            "name": root_path.name,
            "topic": root_path.name,
            "goal": first_heading or "Discovered active project; inspect evidence to curate the goal",
            "summary": f"Discovered from {len(evidence)} progress document(s). Update the goal and terms after review if needed.",
            "terms": specific_terms,
            "priority": "high" if item["active_recently"] else "normal",
            "weight": 0.75 if item["active_recently"] else 0.45,
            "source": "scan",
            "root": str(root_path),
            "active": bool(item["active_recently"]),
            "last_seen_at": iso_now(),
            "evidence": [
                {
                    "path": path,
                    "modified_at": datetime.fromtimestamp(Path(path).stat().st_mtime).astimezone().isoformat(timespec="seconds"),
                }
                for path in evidence
                if Path(path).exists()
            ],
        })

    stats_output = {key: stats[key] for key in sorted(stats)}
    manifest = {
        "version": 1,
        "updated_at": iso_now(),
        "projects": {
            item["root"]: {
                "project_id": "project-" + hashlib.sha1(item["root"].encode("utf-8")).hexdigest()[:10],
                "root": item["root"],
                "name": item["name"],
                "fingerprint": item["fingerprint"],
                "evidence_files": item["evidence_records"],
                "active_recently": bool(item["active_recently"]),
                "stale": False,
                "last_seen_at": iso_now(),
            }
            for item in raw_projects
        },
    }
    return projects, {"stats": stats_output, "warnings": warnings}, manifest


def merge_projects(
    old_projects: Sequence[Mapping[str, Any]],
    discovered: Sequence[Mapping[str, Any]],
    max_projects: int,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    preserved: List[Dict[str, Any]] = []
    changes: List[str] = []
    protected: List[Dict[str, Any]] = []
    old_scan_by_root = {
        str(item.get("root", "")): item
        for item in old_projects
        if isinstance(item, dict) and item.get("source") == "scan" and item.get("root")
    }
    protected_ids: Set[str] = set()

    for item in old_projects:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "manual"))
        if item.get("source") in {"manual", "curated"} or "source" not in item:
            copied = dict(item)
            copied["source"] = source
            protected.append(copied)
            protected_ids.add(str(copied.get("id", copied.get("name", ""))))
        else:
            # Old scan entries not rediscovered remain for one refresh as inactive.
            root_key = str(item.get("root", ""))
            if root_key and root_key not in {str(x.get("root", "")) for x in discovered}:
                copied = dict(item)
                copied["active"] = False
                copied["stale"] = True
                protected.append(copied)
                protected_ids.add(str(copied.get("id", "")))
                changes.append(f"Marked stale scan project: {item.get('name')}")

    merged = list(protected)
    for candidate in discovered:
        existing = old_scan_by_root.get(str(candidate.get("root", "")))
        if existing:
            if candidate.get("_scan_unchanged"):
                copied = dict(existing)
                candidate_active = bool(candidate.get("active", copied.get("active", True)))
                if bool(copied.get("active", True)) != candidate_active:
                    copied["active"] = candidate_active
                    copied["priority"] = "high" if candidate_active else "normal"
                    copied["weight"] = 0.75 if candidate_active else 0.45
                    changes.append(f"Updated activity for discovered project: {candidate.get('name')}")
                merged.append(copied)
                continue
            old_id = str(existing.get("id", candidate["id"]))
            candidate = {**candidate, "id": old_id}
            if protected_ids.intersection({str(existing.get("manual_replacement_id", old_id))}):
                # A manual/protected project already occupies this identity; do not add a duplicate.
                changes.append(f"Skipped scan duplicate of protected project: {candidate.get('name')}")
                continue
        if any(str(existing.get("root", "")) == str(candidate.get("root", "")) for existing in merged):
            changes.append(f"Skipped duplicate root: {candidate.get('root')}")
            continue
        if any(str(existing.get("id", "")) == str(candidate.get("id", "")) for existing in merged):
            candidate["id"] = candidate["id"] + "-" + str(len(merged) + 1)
        merged.append(candidate)
        changes.append(f"Updated discovered project: {candidate.get('name')}")

    active_count = sum(1 for item in merged if item.get("active", True))
    if active_count > max_projects:
        # Keep protected projects first, then recent active discoveries.
        scan_active = [item for item in merged if item.get("source") == "scan" and item.get("active", False)]
        scan_active.sort(key=lambda item: (str(item.get("priority")) != "high", str(item.get("last_seen_at", ""))))
        remove_ids = {str(item.get("id")) for item in scan_active[: active_count - max_projects]}
        merged = [item for item in merged if str(item.get("id")) not in remove_ids]
        changes.append(f"Dropped {len(remove_ids)} discovery item(s) to respect max_projects")

    merged.sort(key=lambda item: (
        str(item.get("source")) in {"manual", "curated"},
        item.get("active", True),
        str(item.get("priority")) == "high",
        str(item.get("last_seen_at", "")),
    ), reverse=True)
    return merged, changes


def enforce_profile_budget(profile: Dict[str, Any], budget: int) -> None:
    current = len(json.dumps(profile, ensure_ascii=False))
    if current <= budget:
        return
    # First remove stale scan projects and trim old evidence. Never remove manual entries.
    projects = profile.get("projects", [])
    if not isinstance(projects, list):
        raise ValueError("Profile 'projects' must be a list")
    changed = True
    while len(json.dumps(profile, ensure_ascii=False)) > budget and changed:
        changed = False
        for index, item in enumerate(projects):
            if not isinstance(item, dict) or item.get("source") not in {"scan"}:
                continue
            evidence = item.get("evidence", [])
            if isinstance(evidence, list) and len(evidence) > 1:
                item["evidence"] = evidence[: max(1, len(evidence) - 1)]
                changed = True
                break
            if item.get("stale") or item.get("active") is False:
                projects.pop(index)
                changed = True
                break
    final_size = len(json.dumps(profile, ensure_ascii=False))
    if final_size > budget:
        raise ValueError(
            f"Profile is {final_size} characters, exceeding the {budget}-character budget. "
            "Curate manual projects or increase maintenance.profile_character_budget."
        )


def command_init(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser()
    if output.exists() and not args.force:
        raise ValueError(f"Refusing to overwrite existing profile: {output}. Use --force only if you mean it.")
    payload = load_json(DEFAULT_PROFILE)
    payload["created_at"] = iso_now()
    payload["updated_at"] = iso_now()
    payload["personalization"] = {
        "mode": "progressive",
        "started_from": "default",
        "note": "Generic defaults work immediately; project scanning and reading feedback personalize this file over time."
    }
    atomic_write_json(output, payload)
    print(json.dumps({"written": str(output), "mode": "default", "personalization_required": False}, ensure_ascii=False, indent=2))
    return 0


def command_init_scan(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser()
    if output.exists() and not args.force:
        raise ValueError(f"Refusing to overwrite existing scan config: {output}. Use --force only if you mean it.")
    payload = load_json(DEFAULT_SCAN_CONFIG)
    roots = [str(safe_resolved_path(value)) for value in args.root]
    if not roots:
        raise ValueError("At least one --root is required")
    payload["roots"] = list(dict.fromkeys(roots))
    payload["created_at"] = iso_now()
    payload["updated_at"] = iso_now()
    atomic_write_json(output, payload)
    print(json.dumps({"written": str(output), "roots": payload["roots"]}, ensure_ascii=False, indent=2))
    return 0


def command_list_roots(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser()
    if not config_path.exists():
        payload = {
            "config": str(config_path),
            "configured": False,
            "roots": [],
            "message": "No scan config exists. The recommender may not inspect local project folders until the user creates an explicit allowlist."
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    config = load_json(config_path)
    roots = config.get("roots", [])
    if not isinstance(roots, list):
        raise ValueError("Scan config 'roots' must be a list")
    output_roots = []
    for value in roots:
        root = safe_resolved_path(str(value))
        output_roots.append({
            "path": str(root),
            "exists": root.exists(),
            "is_directory": root.is_dir(),
            "configured_path": str(value),
        })
    payload = {
        "config": str(config_path.resolve()),
        "configured": True,
        "roots": output_roots,
        "max_depth": config.get("max_depth"),
        "lookback_days": config.get("lookback_days"),
        "message": "Only these explicitly configured roots may be scanned."
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def normalize_config_value(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        output: List[str] = []
        for item in value:
            output.extend(normalize_config_value(item))
        return output
    return [str(value)]


def slug_id(prefix: str, *values: str) -> str:
    canonical = "\n".join(value.casefold() for value in values if value)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def same_interest(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    def canonical(value: Any) -> str:
        return unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return canonical(a.get("name", "")) == canonical(b.get("name", ""))


def same_project(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    a_keys = {str(a.get(key, "")) for key in ("id", "name", "root") if a.get(key)}
    b_keys = {str(b.get(key, "")) for key in ("id", "name", "root") if b.get(key)}
    return bool(a_keys & b_keys)


def command_configure(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile).expanduser()
    config_path = Path(args.config).expanduser()
    answers_path = Path(args.answers).expanduser()
    answers = load_json(answers_path)

    profile = load_json(profile_path) if profile_path.exists() else load_json(DEFAULT_PROFILE)
    if config_path.exists():
        config = load_json(config_path)
    else:
        # Never inherit the example's "." root: conversational setup must not
        # silently authorize the current working directory.
        config = load_json(DEFAULT_SCAN_CONFIG)
        config["roots"] = []
    changes: List[str] = []

    interests = profile.setdefault("interests", [])
    if not isinstance(interests, list):
        raise ValueError("Profile 'interests' must be a list")
    answer_interests = answers.get("interests", [])
    if not isinstance(answer_interests, list):
        raise ValueError("Answers 'interests' must be a list")
    for raw in answer_interests:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        item = dict(raw)
        item["name"] = str(item["name"])
        item["terms"] = normalize_config_value(item.get("terms", item["name"])) or [item["name"]]
        try:
            item["weight"] = float(item.get("weight", 0.8))
        except (TypeError, ValueError):
            item["weight"] = 0.8
        item["weight"] = min(1.5, max(0.1, item["weight"]))
        item["source"] = "manual"
        found = False
        for index, existing in enumerate(interests):
            if isinstance(existing, dict) and same_interest(existing, item):
                interests[index] = item
                changes.append(f"Updated interest: {item['name']}")
                found = True
                break
        if not found:
            interests.append(item)
            changes.append(f"Added interest: {item['name']}")

    projects = profile.setdefault("projects", [])
    if not isinstance(projects, list):
        raise ValueError("Profile 'projects' must be a list")
    answer_projects = answers.get("projects", [])
    if not isinstance(answer_projects, list):
        raise ValueError("Answers 'projects' must be a list")
    for raw in answer_projects:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        item = dict(raw)
        item["name"] = str(item["name"])
        root_value = str(item.get("root", "")).strip()
        if root_value:
            item["root"] = str(safe_resolved_path(root_value))
        else:
            item.pop("root", None)
        item["terms"] = normalize_config_value(item.get("terms", item["name"])) or [item["name"]]
        item["goal"] = str(item.get("goal", "User-described active project"))
        item["summary"] = str(item.get("summary", item["goal"]))
        item["priority"] = str(item.get("priority", "normal")).casefold()
        if item["priority"] not in {"high", "medium", "normal", "low"}:
            item["priority"] = "normal"
        try:
            item["weight"] = float(item.get("weight", 1.0 if item["priority"] == "high" else 0.8))
        except (TypeError, ValueError):
            item["weight"] = 1.0 if item["priority"] == "high" else 0.8
        item["weight"] = min(1.5, max(0.1, item["weight"]))
        item["id"] = str(item.get("id") or slug_id("manual-project", item["name"], root_value))
        item["source"] = "manual"
        item["active"] = True
        item["configured_at"] = iso_now()
        found = False
        for index, existing in enumerate(projects):
            if isinstance(existing, dict) and same_project(existing, item):
                item["id"] = str(existing.get("id") or item["id"])
                projects[index] = item
                changes.append(f"Updated project: {item['name']}")
                found = True
                break
        if not found:
            projects.append(item)
            changes.append(f"Added project: {item['name']}")

    configured_roots = [safe_resolved_path(value) for value in normalize_config_value(answers.get("roots", []))]
    existing_roots = [safe_resolved_path(value) for value in normalize_config_value(config.get("roots", []))]
    removed_roots = {safe_resolved_path(value) for value in normalize_config_value(answers.get("remove_roots", []))}
    replace_roots = bool(answers.get("replace_roots", False))
    if replace_roots:
        merged_roots = configured_roots
        for root in existing_roots:
            if root not in configured_roots:
                changes.append(f"Removed scan root: {root}")
        for root in configured_roots:
            if root not in existing_roots:
                changes.append(f"Added scan root: {root}")
    else:
        merged_roots = [root for root in existing_roots if root not in removed_roots]
        for root in existing_roots:
            if root in removed_roots:
                changes.append(f"Removed scan root: {root}")
        for root in configured_roots:
            if root not in merged_roots:
                merged_roots.append(root)
                changes.append(f"Added scan root: {root}")
    config["roots"] = [str(root) for root in merged_roots]

    for key, profile_key in (("liked_topics", "manual_liked_topics"), ("disliked_topics", "manual_disliked_topics")):
        values = normalize_config_value(answers.get(key, []))
        if values:
            manual = list(dict.fromkeys(normalize_config_value(profile.get(profile_key, [])) + values))
            profile[profile_key] = manual
            profile[key] = list(dict.fromkeys(normalize_config_value(profile.get(key, [])) + values))
            changes.append(f"Updated {key}")

    profile["updated_at"] = iso_now()
    profile.setdefault("personalization", {"mode": "progressive"})
    profile["personalization"]["last_conversational_configuration_at"] = iso_now()
    config["updated_at"] = iso_now()

    budget = int(profile.get("maintenance", {}).get("profile_character_budget", 24000))
    enforce_profile_budget(profile, budget)
    payload = {
        "write": bool(args.write),
        "profile": str(profile_path),
        "scan_config": str(config_path),
        "changes": changes,
        "roots": config["roots"],
        "projects": profile.get("projects", []),
        "interests": profile.get("interests", []),
    }
    if args.write:
        atomic_write_json(profile_path, profile)
        atomic_write_json(config_path, config)
        if args.audit:
            append_audit(Path(args.audit).expanduser(), {
                "event": "configure",
                "profile": str(profile_path),
                "scan_config": str(config_path),
                "answers_file": str(answers_path),
                "changes": changes,
                "roots": config["roots"],
            })
        payload["written"] = True
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_refresh(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile).expanduser()
    profile = load_json(profile_path)
    manifest_path = Path(args.manifest).expanduser() if args.manifest else profile_path.with_name("project-manifest.json")
    previous_manifest = load_json(manifest_path) if manifest_path.exists() else {}
    config: Dict[str, Any] = {}
    if args.config:
        config = load_json(Path(args.config).expanduser())
    roots = [safe_resolved_path(value) for value in (args.root or config.get("roots", []))]
    if not roots:
        raise ValueError("No scan roots configured. Use --config or --root.")
    max_projects = int(args.max_projects or config.get("max_projects") or profile.get("maintenance", {}).get("max_discovered_projects", 12))
    lookback_days = int(args.lookback_days or config.get("lookback_days", 60))
    old_projects = profile.get("projects", [])
    if not isinstance(old_projects, list):
        raise ValueError("Profile 'projects' must be a list")
    discovered, discovery_report, refreshed_manifest = collect_projects(
        roots,
        config,
        max_projects,
        lookback_days,
        previous_manifest=previous_manifest,
        previous_projects=old_projects,
    )
    merged, changes = merge_projects(old_projects, discovered, max_projects)
    previous_manifest_projects = previous_manifest.get("projects", {}) if isinstance(previous_manifest, Mapping) else {}
    if not isinstance(previous_manifest_projects, Mapping):
        previous_manifest_projects = {}
    refreshed_manifest_projects = refreshed_manifest.get("projects", {})
    if not isinstance(refreshed_manifest_projects, dict):
        refreshed_manifest_projects = {}
    discovered_roots = set(refreshed_manifest_projects)
    stale_roots = set(previous_manifest_projects) - discovered_roots
    for root in stale_roots:
        stale_item = dict(previous_manifest_projects[root])
        stale_item["active_recently"] = False
        stale_item["stale"] = True
        stale_item["stale_at"] = iso_now()
        refreshed_manifest_projects[root] = stale_item
    refreshed_manifest["projects"] = refreshed_manifest_projects

    previous_fingerprints = {
        str(root): str(item.get("fingerprint", ""))
        for root, item in previous_manifest_projects.items()
        if isinstance(item, Mapping)
    }
    current_fingerprints = {
        str(root): str(item.get("fingerprint", ""))
        for root, item in refreshed_manifest_projects.items()
        if isinstance(item, Mapping) and not item.get("stale")
    }
    new_roots = sorted(set(current_fingerprints) - set(previous_fingerprints))
    changed_roots = sorted(
        root for root in set(current_fingerprints) & set(previous_fingerprints)
        if current_fingerprints[root] != previous_fingerprints[root]
    )
    refreshed_manifest["summary"] = {
        "new_projects": len(new_roots),
        "changed_projects": len(changed_roots),
        "stale_projects": len(stale_roots),
        "unchanged_projects": int(discovery_report.get("stats", {}).get("projects_reused", 0)),
    }

    profile_changed = merged != old_projects
    updated = dict(profile)
    if profile_changed:
        updated["projects"] = merged
        updated["updated_at"] = iso_now()
        updated.setdefault("personalization", {"mode": "progressive"})
        updated["personalization"]["last_scan_at"] = iso_now()
    budget = int(updated.get("maintenance", {}).get("profile_character_budget", 24000))
    enforce_profile_budget(updated, budget)
    payload = {
        "would_write": str(profile_path),
        "write": bool(args.write),
        "manifest": str(manifest_path),
        "profile_updated": profile_changed,
        "manifest_updated": bool(new_roots or changed_roots or stale_roots or not previous_manifest),
        "recommendation_needed": bool(new_roots or changed_roots or profile_changed),
        "roots": [str(root) for root in roots],
        "projects": merged,
        "changes": changes,
        "discovery": discovery_report,
        "summary": refreshed_manifest["summary"],
    }
    if args.write:
        if profile_changed:
            atomic_write_json(profile_path, updated)
        atomic_write_json(manifest_path, refreshed_manifest)
        if args.audit:
            append_audit(Path(args.audit).expanduser(), {
                "event": "refresh",
                "profile": str(profile_path),
                "manifest": str(manifest_path),
                "roots": [str(root) for root in roots],
                "changes": changes,
                "stats": discovery_report.get("stats", {}),
                "warnings": discovery_report.get("warnings", []),
                "summary": refreshed_manifest["summary"],
                "profile_updated": profile_changed,
            })
        payload["profile_written"] = profile_changed
        payload["manifest_written"] = True
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_learn_feedback(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile).expanduser()
    profile = load_json(profile_path)
    history_path = Path(args.history).expanduser()
    history = load_json(history_path) if history_path.exists() else {"version": 1, "seen": []}
    seen = history.get("seen", [])
    if not isinstance(seen, list):
        raise ValueError("History 'seen' must be a list")
    positive: List[str] = []
    negative: List[str] = []
    from recommend import extract_tokens
    for item in seen:
        if not isinstance(item, dict):
            continue
        feedback = str(item.get("feedback", "selected")).casefold()
        signals: List[str] = []
        for key in ("topics", "keywords"):
            values = item.get(key, [])
            if isinstance(values, list):
                signals.extend(str(value) for value in values if value)
        if not signals and item.get("title") and feedback in {"liked", "selected"}:
            # Use only the most salient title tokens as a weak fallback.
            tokens = [token for token in extract_tokens(item.get("title")) if len(token) >= 3]
            signals.extend(tokens[:5])
        target = positive if feedback in {"liked", "selected"} else negative if feedback in {"disliked", "rejected"} else None
        if target is not None:
            target.extend(signal for signal in signals if signal not in target)

    existing_positive = list(profile.get("liked_topics", []))
    existing_negative = list(profile.get("disliked_topics", []))
    # History is the source of truth for learned topics, while manual entries remain.
    manual_positive = set(str(x) for x in profile.get("manual_liked_topics", []))
    manual_negative = set(str(x) for x in profile.get("manual_disliked_topics", []))
    liked = list(dict.fromkeys(existing_positive + positive[:30]))
    disliked = list(dict.fromkeys(existing_negative + negative[:30]))
    if manual_positive:
        liked = [value for value in liked if value in manual_positive] + [value for value in liked if value not in manual_positive]
    if manual_negative:
        disliked = [value for value in disliked if value in manual_negative] + [value for value in disliked if value not in manual_negative]

    updated = dict(profile)
    updated["liked_topics"] = liked[:30]
    updated["disliked_topics"] = disliked[:30]
    updated["updated_at"] = iso_now()
    updated.setdefault("personalization", {})["last_feedback_learning_at"] = iso_now()
    budget = int(updated.get("maintenance", {}).get("profile_character_budget", 24000))
    enforce_profile_budget(updated, budget)
    payload = {
        "would_write": str(profile_path),
        "write": bool(args.write),
        "liked_topics": updated["liked_topics"],
        "disliked_topics": updated["disliked_topics"],
    }
    if args.write:
        atomic_write_json(profile_path, updated)
        if args.audit:
            append_audit(Path(args.audit).expanduser(), {
                "event": "learn-feedback",
                "profile": str(profile_path),
                "history": str(history_path),
                "liked_topics": updated["liked_topics"],
                "disliked_topics": updated["disliked_topics"],
            })
        payload["written"] = str(profile_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create a generic private profile")
    init.add_argument("--output", required=True)
    init.add_argument("--force", action="store_true")
    init.set_defaults(handler=command_init)

    init_scan = subparsers.add_parser("init-scan", help="Create a private scan allowlist")
    init_scan.add_argument("--output", required=True)
    init_scan.add_argument("--root", action="append", required=True)
    init_scan.add_argument("--force", action="store_true")
    init_scan.set_defaults(handler=command_init_scan)

    configure = subparsers.add_parser("configure", help="Apply chat-collected setup answers to private files")
    configure.add_argument("--profile", required=True)
    configure.add_argument("--config", required=True)
    configure.add_argument("--answers", required=True)
    configure.add_argument("--write", action="store_true")
    configure.add_argument("--audit")
    configure.set_defaults(handler=command_configure)

    list_roots = subparsers.add_parser("list-roots", help="List folders explicitly allowed for project scanning")
    list_roots.add_argument("--config", required=True)
    list_roots.set_defaults(handler=command_list_roots)

    refresh = subparsers.add_parser("refresh", help="Discover projects and update the private profile")
    refresh.add_argument("--profile", required=True)
    refresh.add_argument("--manifest", help="Private project manifest JSON (defaults beside the profile)")
    refresh.add_argument("--config")
    refresh.add_argument("--root", action="append")
    refresh.add_argument("--write", action="store_true")
    refresh.add_argument("--audit")
    refresh.add_argument("--max-projects", type=int)
    refresh.add_argument("--lookback-days", type=int)
    refresh.set_defaults(handler=command_refresh)

    learn = subparsers.add_parser("learn-feedback", help="Learn shallow topic preferences from history")
    learn.add_argument("--profile", required=True)
    learn.add_argument("--history", required=True)
    learn.add_argument("--write", action="store_true")
    learn.add_argument("--audit")
    learn.set_defaults(handler=command_learn_feedback)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

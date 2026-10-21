import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE_MANAGER = ROOT / "scripts" / "profile_manager.py"
RECOMMEND = ROOT / "scripts" / "recommend.py"


def run(*args):
    return subprocess.run(
        [sys.executable] + [str(arg) for arg in args],
        text=True,
        capture_output=True,
        check=True,
    )


class ProfileManagerTests(unittest.TestCase):
    def test_conversational_configure_previews_then_writes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            allowed = base / "projects"
            allowed.mkdir()
            profile = base / "profile.json"
            scan = base / "scan.json"
            answers = base / "answers.json"
            answers.write_text(
                json.dumps(
                    {
                        "interests": [
                            {
                                "name": "LLM inference optimization",
                                "terms": ["LLM inference", "KV cache"],
                                "weight": 1.0,
                            }
                        ],
                        "projects": [
                            {
                                "name": "Browser agent reliability",
                                "goal": "Reduce unsafe actions in browser automation",
                                "terms": ["browser automation", "agent safety"],
                                "priority": "high",
                            }
                        ],
                        "roots": [str(allowed)],
                        "liked_topics": ["evaluation"],
                    }
                ),
                encoding="utf-8",
            )
            preview = json.loads(
                run(
                    PROFILE_MANAGER,
                    "configure",
                    "--profile",
                    profile,
                    "--config",
                    scan,
                    "--answers",
                    answers,
                ).stdout
            )
            self.assertFalse(preview["write"])
            self.assertFalse(profile.exists())
            self.assertFalse(scan.exists())

            written = json.loads(
                run(
                    PROFILE_MANAGER,
                    "configure",
                    "--profile",
                    profile,
                    "--config",
                    scan,
                    "--answers",
                    answers,
                    "--write",
                ).stdout
            )
            self.assertTrue(written["write"])
            saved_profile = json.loads(profile.read_text(encoding="utf-8"))
            self.assertEqual("manual", saved_profile["projects"][0]["source"])
            roots = json.loads(run(PROFILE_MANAGER, "list-roots", "--config", scan).stdout)
            self.assertEqual([str(allowed.resolve())], [item["path"] for item in roots["roots"]])

            revoke = base / "revoke.json"
            revoke.write_text(json.dumps({"remove_roots": [str(allowed)]}), encoding="utf-8")
            run(
                PROFILE_MANAGER,
                "configure",
                "--profile",
                profile,
                "--config",
                scan,
                "--answers",
                revoke,
                "--write",
            )
            roots = json.loads(run(PROFILE_MANAGER, "list-roots", "--config", scan).stdout)
            self.assertEqual([], roots["roots"])


    def test_generic_profile_scan_and_feedback_loop(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            workspace = base / "workspace"
            project = workspace / "browser-agent"
            docs = project / "docs"
            docs.mkdir(parents=True)
            (project / "README.md").write_text(
                "# Browser Agent Reliability\nBrowser automation safety and replay evaluation.\n",
                encoding="utf-8",
            )
            (docs / "PROJECT_HANDOFF.md").write_text(
                "# Current Progress\nCDP diagnostics and action safety benchmark.\n",
                encoding="utf-8",
            )
            (project / ".env").write_text("TOKEN=example", encoding="utf-8")

            profile = base / "profile.json"
            scan = base / "scan.json"
            audit = base / "audit.jsonl"
            run(PROFILE_MANAGER, "init", "--output", profile)
            run(PROFILE_MANAGER, "init-scan", "--output", scan, "--root", workspace)
            roots = json.loads(
                run(PROFILE_MANAGER, "list-roots", "--config", scan).stdout
            )
            self.assertTrue(roots["configured"])
            self.assertEqual([str(workspace.resolve())], [item["path"] for item in roots["roots"]])
            preview = json.loads(
                run(
                    PROFILE_MANAGER,
                    "refresh",
                    "--profile",
                    profile,
                    "--config",
                    scan,
                ).stdout
            )
            self.assertTrue(any(item["name"] == "browser-agent" for item in preview["projects"]))
            run(
                PROFILE_MANAGER,
                "refresh",
                "--profile",
                profile,
                "--config",
                scan,
                "--write",
                "--audit",
                audit,
            )
            updated = json.loads(profile.read_text(encoding="utf-8"))
            discovered = [item for item in updated["projects"] if item.get("source") == "scan"]
            self.assertEqual(1, len(discovered))
            self.assertTrue(discovered[0]["terms"])
            self.assertNotIn(".env", json.dumps(discovered))
            self.assertTrue(audit.exists())

            history = base / "history.json"
            candidates = base / "candidates.json"
            ranked = base / "ranked.json"
            candidates.write_text(
                json.dumps(
                    [
                        {
                            "title": "Agent Safety Benchmark",
                            "url": "https://example.org/agent-safety",
                            "abstract": "Browser automation agent safety benchmark and replay evaluation.",
                            "source_type": "official_report",
                            "published_at": "2026-10-01",
                            "topics": ["agents", "safety"],
                            "keywords": ["browser automation", "evaluation"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            run(
                RECOMMEND,
                "rank",
                "--profile",
                profile,
                "--candidates",
                candidates,
                "--history",
                history,
                "--top",
                "1",
                "--output",
                ranked,
                "--record-selection",
            )
            feedback = json.loads(
                run(
                    PROFILE_MANAGER,
                    "learn-feedback",
                    "--profile",
                    profile,
                    "--history",
                    history,
                    "--write",
                ).stdout
            )
            self.assertIn("browser automation", feedback["liked_topics"])


if __name__ == "__main__":
    unittest.main()

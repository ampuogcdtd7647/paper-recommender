import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from feedback import derive_preferences, record_event
from recommend import paper_identity, score_candidates


def run(script, *args, check=True):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
                          capture_output=True, text=True, check=check)


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.paper = {"title": "Evaluation Methods", "url": "https://example.org/a",
                      "topics": ["evaluation"], "keywords": ["benchmark"], "project_ids": ["p1"]}
        self.history = {"seen": [], "recommendations": [{
            **self.paper, "recommendation_id": "r1", "paper_id": paper_identity(self.paper),
            "conversation_id": "chat1", "project_matches": [],
        }]}
        self.profile = {"projects": [{"id": "p1", "name": "Evaluation", "terms": ["evaluation"]},
                                     {"id": "p2", "name": "Other", "terms": ["other"]}]}

    def decision(self, message_id, signal="follow_up", **extra):
        return {"recommendation_id": "r1", "signal": signal,
                "message": {"id": message_id, "conversation_id": "chat1", "role": "user",
                            "content": "这种评测方法的局限是什么？"},
                "evidence_quote": "这种评测方法的局限是什么？", **extra}

    def add(self, message_id, signal="follow_up", **extra):
        self.history, result = record_event(self.history, self.decision(message_id, signal, **extra))
        return result

    def test_two_distinct_turns_qualify_without_different_dimensions(self):
        self.add("m1", dimension="method")
        self.assertEqual([], derive_preferences(self.history))
        self.add("m2", dimension="method")
        memory = derive_preferences(self.history)[0]
        self.assertEqual(0.35, memory["strength"])
        self.assertEqual("p1", memory["project_id"])
        for i in range(3, 8):
            self.add(f"m{i}")
        self.assertEqual(0.35, derive_preferences(self.history)[0]["strength"])

    def test_retry_does_not_reinforce_and_conflicting_retry_fails(self):
        self.add("m1")
        before = copy.deepcopy(self.history)
        self.assertEqual("NONE", self.add("m1")["event"])
        self.assertEqual(before, self.history)
        self.assertEqual([], derive_preferences(self.history))
        with self.assertRaises(ValueError):
            self.add("m1", "interested")

    def test_one_turn_in_each_chat_does_not_count_as_sustained(self):
        self.add("m1")
        self.history["recommendations"].append({**self.history["recommendations"][0],
                                               "recommendation_id": "r2", "conversation_id": "chat2"})
        d = self.decision("m2", recommendation_id="r2")
        d["message"]["conversation_id"] = "chat2"
        self.history, _ = record_event(self.history, d)
        self.assertEqual([], derive_preferences(self.history))

    def test_explicit_overrides_followups_and_retraction_replays(self):
        self.add("m1")
        self.add("m2")
        self.add("m3", "not_interested")
        self.add("m4")
        self.assertEqual(-1, derive_preferences(self.history)[0]["strength"])
        positive = self.add("m5", "interested")
        self.assertEqual(1, derive_preferences(self.history)[0]["strength"])
        self.add("m6", "retract", retracts=positive["event_id"])
        self.assertEqual(-1, derive_preferences(self.history)[0]["strength"])

    def test_retracting_one_of_two_questions_removes_inferred_interest(self):
        first = self.add("m1")
        self.add("m2")
        self.add("m3", "retract", retracts=first["event_id"])
        self.assertEqual([], derive_preferences(self.history))

    def test_paper_negative_reason_does_not_penalize_topic(self):
        self.add("m1")
        self.add("m2")
        self.add("m3", "not_interested", reason="too_basic")
        memories = derive_preferences(self.history)
        self.assertEqual(1, len(memories))
        self.assertEqual("paper", memories[0]["scope"])
        self.assertEqual([], memories[0]["terms"])
        self.add("m4", "interested")
        self.assertEqual([1], [p["strength"] for p in derive_preferences(self.history)])

    def test_neutral_legacy_selection_and_silence_never_become_negative(self):
        self.history["seen"] = [{**self.paper, "feedback": "selected"}]
        for i, kind in enumerate(["neutral", "saved", "read", "temporarily_unavailable"]):
            self.add(f"m{i}", kind)
        self.assertEqual([], derive_preferences(self.history))

    def test_target_role_quote_and_project_validation(self):
        cases = [self.decision("m1", recommendation_id="missing"),
                 self.decision("m1", evidence_quote="not in the message"),
                 self.decision("m1", scope="project", project_id="unrelated"),
                 self.decision("m1", terms=["invented"])]
        for key, value in [("role", "assistant"), ("conversation_id", "other")]:
            d = self.decision("m1")
            d["message"][key] = value
            cases.append(d)
        for d in cases:
            with self.subTest(d=d), self.assertRaises(ValueError):
                record_event(self.history, d)
        self.assertNotIn("feedback_events", self.history)

    def test_multiple_projects_default_to_paper_and_global_needs_explicit_terms(self):
        self.history["recommendations"][0]["project_ids"] = ["p1", "p2"]
        self.add("m1", "interested")
        self.assertEqual("paper", derive_preferences(self.history)[0]["scope"])
        with self.assertRaises(ValueError):
            self.add("m2", scope="global", terms=["evaluation"])
        with self.assertRaises(ValueError):
            self.add("m2", "interested", scope="global", terms=["evaluation"])
        d = self.decision("m2", "interested", scope="global", terms=["evaluation"],
                          evidence_quote="以后多推荐 evaluation 方向")
        d["message"]["content"] = d["evidence_quote"]
        h, _ = record_event(self.history, d)
        self.assertEqual("global", derive_preferences(h)[0]["scope"])

    def test_scoring_is_project_scoped_decaying_and_live_history_overrides_snapshot(self):
        self.add("m1")
        second = self.add("m2")
        candidates = [{**self.paper, "url": "https://example.org/new"},
                      {"title": "Evaluation benchmark", "url": "https://example.org/unrelated",
                       "topics": ["evaluation"], "project_ids": ["p2"]}]
        # Prevent lexical project evidence in the second candidate: its declared
        # project is p2 and neither project's terms match the topic labels.
        self.profile["projects"][0]["name"] = "First"
        self.profile["projects"][0]["terms"] = ["unmatched-first"]
        ranked, _ = score_candidates(self.profile, candidates, self.history, True, False)
        scores = {r["url"]: r["score_breakdown"]["feedback"] for r in ranked}
        self.assertGreater(scores[candidates[0]["url"]], 0)
        self.assertLessEqual(scores[candidates[0]["url"]], 2.8)
        self.assertEqual(0, scores[candidates[1]["url"]])
        self.profile["feedback_preferences"] = derive_preferences(self.history)
        snapshot, _ = score_candidates(self.profile, candidates, None, True, False)
        self.assertEqual(scores, {r["url"]: r["score_breakdown"]["feedback"] for r in snapshot})
        for e in self.history["feedback_events"]:
            e["created_at"] = "2000-01-01T00:00:00+00:00"
        old, _ = score_candidates(self.profile, candidates, self.history, True, False)
        self.assertTrue(all(r["score_breakdown"]["feedback"] == 0 for r in old))
        self.add("m3", "retract", retracts=second["event_id"])
        after, _ = score_candidates(self.profile, candidates, self.history, True, False)
        self.assertTrue(all(r["score_breakdown"]["feedback"] == 0 for r in after))

    def test_end_to_end_cli_preview_register_learn_rank_and_retract(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            profile, candidates, ranked, history, decision = [base / f"{n}.json" for n in
                                                            ["profile", "candidates", "ranked", "history", "decision"]]
            initial_profile = {**self.profile, "liked_topics": ["legacy-selected-topic"],
                               "manual_liked_topics": ["manual-topic"]}
            profile.write_text(json.dumps(initial_profile))
            candidates.write_text(json.dumps([self.paper]))
            run("recommend.py", "rank", "--profile", profile, "--candidates", candidates, "--output", ranked)
            self.assertFalse(history.exists())
            rec = json.loads(ranked.read_text())["selected"][0]
            self.assertEqual(paper_identity(self.paper), rec["paper_id"])
            run("recommend.py", "record-shown", "--ranked", ranked, "--history", history,
                "--conversation-id", "chat1")
            before = history.read_bytes()
            run("recommend.py", "record-shown", "--ranked", ranked, "--history", history,
                "--conversation-id", "chat1")
            self.assertEqual(before, history.read_bytes())
            d = self.decision("m1", recommendation_id=rec["recommendation_id"])
            decision.write_text(json.dumps(d))
            run("feedback.py", "--history", history, "--decision", decision)
            self.assertEqual(before, history.read_bytes())
            run("feedback.py", "--history", history, "--decision", decision, "--write")
            d["message"]["id"] = "m2"
            decision.write_text(json.dumps(d))
            result = json.loads(run("feedback.py", "--history", history, "--decision", decision, "--write").stdout)
            preview = run("profile_manager.py", "learn-feedback", "--profile", profile, "--history", history)
            self.assertEqual(1, len(json.loads(preview.stdout)["feedback_preferences"]))
            self.assertEqual(initial_profile, json.loads(profile.read_text()))
            run("profile_manager.py", "learn-feedback", "--profile", profile, "--history", history, "--write")
            saved = json.loads(profile.read_text())
            self.assertEqual(self.profile["projects"], saved["projects"])
            self.assertEqual(["manual-topic"], saved["liked_topics"])
            self.assertEqual(["legacy-selected-topic"], saved["legacy_feedback_topics"]["liked_topics"])
            # A bounded snapshot must never trim project evidence to make room.
            with_budget = copy.deepcopy(saved)
            with_budget["projects"][0].update(source="scan", evidence=["a.md", "b.md"])
            with_budget["maintenance"] = {"profile_character_budget": 1}
            no_preferences = {**with_budget, "feedback_preferences": []}
            budget = len(json.dumps(no_preferences, ensure_ascii=False)) + 40
            with_budget["maintenance"]["profile_character_budget"] = budget
            profile.write_text(json.dumps(with_budget))
            bounded = json.loads(run("profile_manager.py", "learn-feedback", "--profile", profile,
                                     "--history", history, "--write").stdout)
            self.assertEqual(1, bounded["snapshot_entries_omitted"])
            self.assertEqual(with_budget["projects"], json.loads(profile.read_text())["projects"])
            profile.write_text(json.dumps(saved))
            d.update(signal="retract", retracts=result["event_id"])
            d["message"]["id"] = "m3"
            decision.write_text(json.dumps(d))
            run("feedback.py", "--history", history, "--decision", decision, "--write")
            run("profile_manager.py", "learn-feedback", "--profile", profile, "--history", history, "--write")
            self.assertEqual([], json.loads(profile.read_text())["feedback_preferences"])
            d["recommendation_id"] = "missing"
            decision.write_text(json.dumps(d))
            before = history.read_bytes()
            result = run("feedback.py", "--history", history, "--decision", decision, "--write", check=False)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(before, history.read_bytes())

    def test_arxiv_pdf_and_abstract_share_identity(self):
        a = {"url": "https://arxiv.org/pdf/2401.12345v2"}
        b = {"url": "https://arxiv.org/abs/2401.12345"}
        c = {"arxiv_id": "2401.12345v3"}
        self.assertEqual(paper_identity(a), paper_identity(b))
        self.assertEqual(paper_identity(a), paper_identity(c))


if __name__ == "__main__":
    unittest.main()

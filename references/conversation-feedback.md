# Conversational interest

Use this policy when presenting papers or continuing a discussion about them.
The agent interprets the dialogue; `feedback.py` validates and records its
structured decision. The skill has no background chat listener. Process messages
available in the active conversation only, or transcripts explicitly supplied for
this purpose. Do not scan unrelated chats to build a preference profile.

## Capture and resolve

1. Rank and verify sources as usual. Each selected item has `paper_id` (derived
   from DOI, arXiv ID, or canonical URL) and an occurrence `recommendation_id`.
   Keep source identifiers consistent across candidate collections.
2. Retain only the papers actually presented in the ranked JSON's `selected`
   list, then register it with:
   ```bash
   python3 scripts/recommend.py record-shown --ranked RANKED.json \
     --history HISTORY.json --conversation-id CHAT_ID
   ```
   Use the host chat ID. If unavailable, assign one local ID for this conversation
   and reuse it across turns. A ranking ID stays stable when that saved output is
   retried; a fresh ranking creates new occurrence IDs. Presentation is neutral.
   `--record-selection` remains a convenience for immediately presented results,
   now requiring `--history` and `--conversation-id` and recording `shown`.
3. Resolve the user's reference using the displayed list and recent dialogue.
   "The second one" refers to the displayed order, not an internal candidate
   order. After several papers, an ambiguous "this is interesting" is insufficient
   to select an ID. Ask briefly only if needed; otherwise leave it unrecorded.
   A multi-paper reply may produce one decision per unambiguously identified paper.
4. Pass only supported user evidence. Assistant claims, quoted third-party text,
   paper abstracts, and the user's discussion of this feedback system itself are
   not the user's paper preference. Do not infer interest from silence.

## Interpret engagement before looking for explicit labels

Sustained substantive follow-up is the main implicit interest signal. The user
does not have to say "I like it" or press a button. A question about mechanism,
evidence, application, comparison, code, reproduction, or a limitation counts as
`follow_up` when it advances discussion of the identified paper. Critical inquiry
can count: interest in a research question is different from endorsing a claim.

Exclude repetitions/rephrasing caused by an unanswered question or an incorrect
explanation, logistical messages, and acknowledgments. Judge this from the recent
dialogue, not a keyword list or message length. One long message containing five
questions counts as one user turn. Different question dimensions are useful
metadata, not a requirement for interest.

| User behavior | Signal and effect |
|---|---|
| First substantive question | `follow_up`; event only |
| Second distinct substantive user turn, same paper and chat | Implicit interest, strength 0.35 |
| More follow-ups in that chat | Continue evidence; no unlimited weight growth |
| "This is useful", "I'm interested", "More like this" | `interested`, explicit strength +1 |
| "Not interested", "Don't recommend this kind for this project" | `not_interested`, explicit strength -1 |
| "Save it", "I've read it", "No time this week" | `saved`, `read`, `temporarily_unavailable`; no interest inference |
| "Thanks", unclear attitude or reference | `neutral` or no event |
| "That inferred interest was wrong" | `retract` referencing the event ID |

The two-turn threshold and strengths are initial engineering heuristics, not
validated psychological measures or model confidence probabilities. Explicit
feedback takes precedence over implicit signals. A later explicit judgment in
the same scope replaces the earlier one. No reply never creates a negative event.

## Write one sourced decision

Save a private `feedback-decision.json` outside the skill repo. Example (replace
the IDs with registered values, preserve the actual user quote):

```json
{
  "recommendation_id": "rec-example",
  "message": {
    "id": "user-turn-002",
    "conversation_id": "chat-example",
    "role": "user",
    "content": "这种方法的评测指标能用到我的项目里吗？"
  },
  "signal": "follow_up",
  "evidence_quote": "这种方法的评测指标能用到我的项目里吗？",
  "dimension": "project_application",
  "scope": "project",
  "project_id": "project-example",
  "terms": ["evaluation"],
  "reason": null
}
```

Use the host's user-message ID or a stable local turn ID. Never generate a new
message ID just to retry a failed call. `terms` can narrow learning to the discussed
topics/keywords from the recorded paper; avoid promoting every keyword when only
one aspect interested the user. Omit it to use that paper's topics and keywords.

```bash
python3 scripts/feedback.py --history HISTORY.json --decision DECISION.json
python3 scripts/feedback.py --history HISTORY.json --decision DECISION.json --write
```

The first command previews; the second records. Routine capture in this authorized
feedback workflow needs no extra confirmation. The writer validates role, quote,
registered target, conversation, project membership and duplicate message IDs.
Language interpretation is the agent's responsibility; passing these checks does
not independently prove that its interpretation is correct. A retry returns NONE;
an inconsistent retry fails rather than overwriting evidence. The event stores a
short exact quote, not the whole chat. Briefly acknowledge meaningful inferences
when natural (e.g. "这几轮追问我会记为对这篇论文的隐式兴趣，可以随时纠正");
do not interrupt every question with a preference survey.

For correction, use a new user-message ID, `signal: "retract"`, and `retracts:
"fb-..."`. Retract the mistaken source event(s); the snapshot is recomputed and
may fall below the two-turn threshold. This retracts the learning signal while
retaining audit history; it is not an erasure of personal data.

## Scope and optional reasons

Default to the sole associated project when the recorded recommendation has one;
otherwise keep the feedback at paper scope. For multiple projects, use a project
only when the dialogue identifies it. Project IDs come from the registered query
association or nonempty project-match evidence, never a fabricated folder name.
Scanned project facts and feedback are separate fields; weekly scanning cannot
overwrite feedback.

Global scope requires an explicit general preference and terms named in the user
quote. Repeated engagement does not automatically become a global identity. For
example, "以后多推荐 evaluation 方向" may establish a global preference for that
named term; "这篇挺有趣" may not. The same source paper contributes at most once
to any candidate, even if discussed in several chats or scopes.

Reasons are optional. Preserve them when the user supplies them; do not request
one for every reply. `too_basic`, `too_hard`, `not_actionable`, `source`, and
`already_known` constrain negative feedback to the paper. The current ranker has
no reliable difficulty/source-preference model: these reasons are retained for
later use, without penalizing the whole research topic. `off_topic` can affect
the identified project; temporary unavailability remains neutral.

## Replay and recommendation effects

`history.json` contains presentation records and `feedback_events`. It is the
source of truth, with no second event database. `learn-feedback` rebuilds a
`feedback_preferences` snapshot in the private profile for use without history.
The snapshot fits the profile budget by omitting older preferences before touching
any project data; the rebuild reports omissions. Keep one writer per history file
at a time (serialize feedback capture and presentation registration).
When `rank --history` is supplied, ranking uses current events directly, so a
retraction takes effect without waiting for a weekly job.

Implicit interest decays with a 90-day half-life; explicit signals with 180 days.
Ranking matches the scoped terms and an active project, bounds the total to the
existing -10/+8 feedback limits, and exposes the contributing evidence category.
One source paper cannot supply unlimited reinforcement. Global preferences require
explicit evidence; this version does not automatically promote them across projects.

`shown`, legacy `selected`, neutral events, and legacy status-only `liked/disliked`
records are not replayed as sourced conversational evidence. Historical records
are retained. Existing manual profile lists and top-level history topic lists
remain explicit global inputs. Old auto-learned profile lists are archived as
`legacy_feedback_topics` on the first rebuild; no unsupported likes are carried
forward. If a legacy preference should remain active, explicitly configure it
or record a new user-sourced event. Never fabricate an original quote to migrate it.

Rebuilding does not schedule itself. An existing weekly workflow can call it after
refreshing projects; every weekly literature search can still discover new papers
even when project files are unchanged.
